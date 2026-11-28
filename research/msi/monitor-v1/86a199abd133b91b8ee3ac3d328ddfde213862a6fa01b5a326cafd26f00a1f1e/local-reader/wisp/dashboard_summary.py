"""Read aggregate observations for a campaign progress dashboard."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from . import campaign, campaign_watch


PROTOCOLS = {
    "stationary-one-blank-practice-v1": "Practice retaining a stationary target",
    "stationary-localization-v1": "Stationary target localization",
    "wisp-mixed-delay-transfer-v1": "Mixed-delay transfer",
    "wisp-single-grade-source-v1": "Single-grade source recall",
    "wisp-prior-grade-interference-v1": "Prior-grade interference",
    "wisp-marked-recall-v1": "Recall with a source marker",
    "wisp-marker-practice-v1": "Marker practice with probe feedback",
}
SPLITS = ("validation", "confirmation")


def _record(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Unrecognized campaign record")
    return value


def _writer_busy(path):
    """Observe the Windows SQLite RESERVED byte without taking a writer lock."""
    if not path.exists():
        return False
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    import msvcrt

    read_file = ctypes.WinDLL("kernel32", use_last_error=True).ReadFile
    read_file.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
    with path.open("rb", buffering=0) as stream:
        stream.seek(0x40000001)
        buffer = ctypes.create_string_buffer(1)
        count = wintypes.DWORD()
        if read_file(msvcrt.get_osfhandle(stream.fileno()), buffer, 1,
                     ctypes.byref(count), None):
            return False
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            return True
        if error == 38:  # ERROR_HANDLE_EOF
            return False
        return None


def _score(value):
    if not isinstance(value, dict):
        return None
    correct, total = value.get("correct"), value.get("total")
    if type(correct) != int or type(total) != int or not 0 <= correct <= total or not total:
        return None
    return {"correct": correct, "total": total, "accuracy": correct / total}


def _arms(source):
    arms = []
    for name, arm in (source or {}).items():
        review = arm.get("last_review") or {}
        row = {"name": name, "generation": arm.get("generation"),
               "stage": arm.get("stage_label"), "state": arm.get("state")}
        for split, measures in (("validation", review), ("confirmation", review.get("confirmation"))):
            row[split] = {key: _score((measures or {}).get(key))
                          for key in ("credit", "delayed", "immediate", "reversal")}
        arms.append(row)
    return arms


def _study_info(metadata, review, fallback_name):
    protocol = metadata.get("protocol") or review.get("protocol")
    name = PROTOCOLS.get(protocol, (protocol or fallback_name).replace("-", " "))
    return {"name": name, "state": metadata.get("state"),
            "completed_at": review.get("completed_at") or metadata.get("completed_at"),
            "result": review.get("result") or metadata.get("result")}


def _field(record, *keys):
    for key in keys:
        if not isinstance(record, dict):
            return None
        record = record.get(key)
    return record


def _one_blank_rows(path, review, bank, split):
    # Only known, reviewed metrics may leave the lab. Labels come from this reader.
    limit = {"validation": 512, "confirmation": 1024}[split]
    rows = []
    for condition, target in (("blank", "one blank frame"), ("visible", "target visible")):
        for arm, teaching in (("accurate", "Accurate feedback"), ("yoked", "Yoked feedback"),
                              ("nonplastic", "Plasticity disabled")):
            score = _score(_field(bank, "held_out", arm, condition))
            if score and score["total"] <= limit:
                rows.append({"label": f"{teaching}, {target}", "score": score})
    fresh = _field(bank, "fresh_skills", "metrics")
    if fresh is None:
        # The compact review keeps the detailed fresh-skill counts in its hashed bank.
        filename = f"one-blank-practice-{split}.json"
        expected = _field(review, "reports", filename)
        if isinstance(expected, str) and (path / filename).is_file():
            data = (path / filename).read_bytes()
            if hashlib.sha256(data).hexdigest() == expected:
                fresh = _field(json.loads(data), "bank", "fresh_skills", "metrics")
    skills = []
    for key, label in (("immediate", "Fresh immediate recall"), ("reversal", "Fresh reversal"),
                       ("mixed_recall", "Fresh mixed-delay recall")):
        cell = _field(fresh, key) if key == "mixed_recall" else _field(fresh, "old_skills", key)
        score = _score(cell)
        if score and score["total"] <= 65536:
            skills.append({"label": label, "score": score})
    for arm, teaching in (("intact", "Accurate feedback"), ("nonplastic", "Plasticity disabled")):
        for phase in ("recall", "reversal"):
            rate = _field(bank, "post_practice", "rates", arm, phase)
            if type(rate) in (float, int) and 0 <= rate <= 1:
                skills.append({"label": f"{teaching}, post-practice {phase}", "rate": rate})
    return {"comparison": rows, "skill_rows": skills}


def _study(root, active):
    if not active:
        return None
    path = (root / active["active_path"]).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Invalid active study directory")
    review = _record(path / "review.json")
    result = _study_info(active, review, path.name)
    result.update(objective=active.get("objective"), retained_both=review.get("retained_both"),
                  splits={})
    for split, bank in (review.get("by_split") or {}).items():
        if split not in SPLITS:
            continue
        protocol = active.get("protocol") or review.get("protocol")
        if protocol == "stationary-one-blank-practice-v1":
            result["splits"][split] = _one_blank_rows(path, review, bank, split)
            continue
        if protocol not in PROTOCOLS:
            result["splits"][split] = {}
            continue
        arms = bank.get("arms") or {}
        intact = arms.get("intact") or {}
        accuracy = intact.get("accuracy") or {}
        old = bank.get("old_scores") or {}
        earlier = old.get("old_skills") or {}
        values = {key: _score(accuracy.get(key)) for key in ("given", "withheld", "boundary_only")}
        delta = None
        if values["given"] and values["withheld"]:
            delta = values["given"]["accuracy"] - values["withheld"]["accuracy"]
        values.update(delta=delta, earlier={"mixed_recall": _score(old.get("mixed_recall")),
                                          "immediate": _score(earlier.get("immediate")),
                                          "reversal": _score(earlier.get("reversal"))})
        if active.get("protocol") == "stationary-localization-v1":
            correct = bank.get("correct") or {}
            total = bank.get("trials_per_arm")
            values["stationary"] = {
                arm: _score({"correct": correct.get(arm), "total": total})
                for arm in ("visible", "blank", "dark_only", "decoder_visible",
                            "decoder_blank", "last_seen_blank")
            }
        if active.get("protocol") == "wisp-marked-recall-v1":
            marked = {}
            for condition in ("informative", "shuffled"):
                cell = intact.get(condition) or {}
                marked[condition] = {"overall": _score(cell.get("overall")),
                                     "conflict": _score(cell.get("conflict"))}
            informative = intact.get("informative") or {}
            conflicts = informative.get("conflict_histories")
            total = informative.get("histories")
            if type(conflicts) is int and type(total) is int and 0 <= conflicts <= total:
                marked.update(conflict_histories=conflicts, histories=total)
            minimum = (active.get("thresholds") or {}).get("min_conflict_histories")
            if type(minimum) is int and minimum > 0:
                marked["min_conflict_histories"] = minimum
            values["marked"] = marked
        if active.get("protocol") == "wisp-marker-practice-v1":
            practice = {}
            for arm in ("accurate", "independent"):
                cell = (intact.get(arm) or {}).get("informative") or {}
                row = {key: _score(cell.get(key)) for key in ("conflict", "overall")}
                count, total = cell.get("conflict_histories"), cell.get("histories")
                if type(count) is int and type(total) is int and 0 <= count <= total:
                    row.update(conflict_histories=count, histories=total)
                practice[arm] = row
            minimum = (active.get("thresholds") or {}).get("min_conflict_histories")
            if type(minimum) is int and minimum > 0:
                practice["min_conflict_histories"] = minimum
            pair = ((bank.get("paired") or {}).get("intact") or {}).get("informative") or {}
            total, wins, losses = (pair.get(key) for key in ("total", "wins", "losses"))
            if all(type(n) is int for n in (total, wins, losses)) and total > 0 and min(wins, losses) >= 0 and wins + losses <= total:
                practice["paired_overall_delta"] = (wins - losses) / total
            values["practice"] = practice
        result["splits"][split] = values
    return result


def _activity(watch, active, lock_status):
    if any(busy is True for busy in lock_status):
        return "running", "Campaign batch running"
    if watch.get("paused"):
        return "paused", "Supervision paused"
    if watch.get("phase") == "primary" and watch.get("primary_state") == "paused":
        return "paused", "Primary campaign paused"
    if not watch:
        return "unknown", "Supervisor status unavailable"
    if None in lock_status:
        return "unknown", "Worker activity unverified"
    if watch.get("phase") == "research_review" and (not active or active.get("completed_at")):
        return "awaiting_review", "Awaiting research review"
    if active and not active.get("completed_at"):
        return "unknown", "Study open; worker activity unverified"
    return "waiting", "Between campaign batches"


def read_summary(directory):
    """Read only public progress fields. All timestamps are UTC observation or record times."""
    root = Path(directory)
    watch = campaign_watch.read_watch_status(root) or {}
    if (root / "campaign.sqlite3").is_file():
        primary = campaign.read_status(root)
        watch.setdefault("phase", "primary")
        watch.update(generation=primary["generation"], primary_state=primary["state"])
    active = _record(root / "active_research.json")
    study = _study(root, active)
    history = []
    directories = [root, root / "supervisor"]
    directories.extend(root / "comparison" / arm for arm in campaign_watch.ARMS)
    for child in sorted((root / "continuations").glob("*")):
        if not child.is_dir() or not child.resolve().is_relative_to(root.resolve()):
            continue
        directories.append(child)
        metadata = _record(child / "review-at-completion.json") or _record(child / "preregistration.json")
        review = _record(child / "review.json")
        if metadata or review:
            row = _study_info(metadata, review, child.name)
            if row["completed_at"] and row["state"] == "preregistered":
                row["state"] = "bounded_study_complete"
            history.append(row)
    history.sort(key=lambda row: row["completed_at"] or "", reverse=True)
    locks = []
    for directory in directories:
        try:
            locks.append(_writer_busy(directory / "writer.lock"))
        except OSError:
            locks.append(None)
    state, label = _activity(watch, study, locks)
    summary = {key: watch.get(key) for key in ("phase", "generation", "primary_state", "paused", "updated_at")}
    summary["arms"] = _arms(watch.get("arms"))
    return {"observed_at": datetime.now(timezone.utc).isoformat(), "activity": {"state": state, "label": label},
            "campaign": summary, "study": study, "history": history}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default=campaign_watch.DEFAULT_RUN, type=Path)
    args = parser.parse_args()
    try:
        result = read_summary(args.run)
    except (OSError, KeyError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Could not read campaign records: {error}\n")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
