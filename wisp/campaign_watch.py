"""Drive the local evolutionary curriculum in short, resumable batches."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
import time

from . import campaign
from .experiment import derived_seed


BATCH = 5
ARMS = {"legacy": False, "trace": True}
DEFAULT_RUN = Path("runs/personal.json.campaign")


def _load(root):
    path = root / "supervisor" / "state.json"
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Unsupported campaign supervisor version.")
    phase = state.get("phase")
    number = state.get("generation")
    plan = state.get("plan")
    arms = state.get("arms")
    if (phase not in ("primary", "comparison", "followup", "research_review") or type(number) is not int or number < 0
            or (plan is not None and (type(plan) is not int or plan < 1)) or
            not isinstance(arms, dict) or set(arms) - ARMS.keys() or not isinstance(state.get("message"), str)):
        raise ValueError("Invalid campaign supervisor cursor.")
    if phase in ("comparison", "followup") or arms:
        if (type(state.get("paired_seed")) is not int or type(state.get("stop_round")) is not int
                or state["stop_round"] < 1 or type(state.get("parent_stage")) is not int
                or not 0 <= state["parent_stage"] < len(campaign.evolution.STAGES)):
            raise ValueError("Invalid matched comparison cursor.")
    if phase == "followup":
        qualified = state.get("qualified")
        targets = state.get("followup_targets") or {}
        if (not isinstance(qualified, list) or not qualified or set(qualified) - ARMS.keys()
                or not isinstance(targets, dict) or targets.keys() - set(qualified)
                or any(type(n) is not int or n < 1 for n in targets.values())):
            raise ValueError("Invalid confirmed continuation cursor.")
    return state


def _save(root, state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = root / "supervisor" / "state.json"
    with NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=path.parent) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(state, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _held(root):
    return (root / "supervisor" / "paused").is_file()


def read_watch_status(directory):
    """Read a compact snapshot with the latest operator hold, including between ticks."""
    root = Path(directory)
    state = _load(root)
    held = _held(root)
    if state is None and not held:
        return None
    if state is None:
        state = {"phase": "primary", "message": "Waiting for the first local review.",
                 "generation": campaign.read_status(root)["generation"], "arms": {}}
    keys = ("phase", "generation", "arms", "message", "updated_at", "stop_round", "qualified", "comparison_result")
    return {**{key: state[key] for key in keys if key in state}, "paused": held}


def set_watch_paused(directory, paused):
    """Set a durable global hold. A generation already in progress may finish."""
    root = Path(directory)
    campaign.read_status(root)
    path = root / "supervisor" / "paused"
    path.parent.mkdir(exist_ok=True)
    if paused:
        path.touch()
    else:
        path.unlink(missing_ok=True)
    return read_watch_status(root)


def _brief(status):
    review = status.get("last_review")
    if review:
        measured = review["validation"]
        keys = ("credit", "delayed", "immediate", "reversal")
        confirmation = review.get("confirmation")
        review = {"generation": review["generation"], "label": review["label"], "decision": review["decision"],
                  "confirmation": {key: confirmation[key] for key in keys} if confirmation else None,
                  **{key: measured[key] for key in ("credit", "delayed", "immediate", "reversal")}}
    return {"generation": status["generation"], "stage_label": status["stage_label"],
            "state": status["state"], "last_review": review}


def _advance(root, directory, target, stage=None):
    status = campaign.read_status(directory)
    while status["state"] == "ready" and status["generation"] < target and not _held(root):
        if stage is not None and status["stage"] != stage:
            break
        status = campaign.run_campaign(directory, status["generation"] + 1)
    return status


def _research(state, message):
    state.update(phase="research_review", message=message, plan=None, followup_targets=None)


def _primary(root, state, source):
    state["generation"] = source["generation"]
    if source["state"] == "ready":
        if state["plan"] is None:
            state["plan"] = source["generation"] + BATCH
            _save(root, state)
        source = _advance(root, root, state["plan"])
        state["generation"] = source["generation"]
        if source["generation"] >= state["plan"]:
            state["plan"] = None
    if source["state"] == "ready":
        state["message"] = "Training the primary campaign on its current challenge."
    elif source["state"] == "paused":
        state["message"] = "The primary campaign is paused by its operator."
    elif source["state"] == "review" and source["stage_generations"] >= source["stage_budget"]:
        state.update(phase="comparison", plan=None, parent_stage=source["stage"],
                     stop_round=source["stage_budget"],
                     paired_seed=derived_seed(source["seed"], "paired-trace-study", source["generation"]),
                     message="The stage budget ended. The next batch compares matched learning rules.")
    else:
        _research(state, source["reason"] + " The primary campaign is ready for research review.")


def _pair(root, state):
    paths = {arm: root / "comparison" / arm for arm in ARMS}
    arms = {}
    for arm, trace in ARMS.items():
        if _held(root):
            return
        # This also validates an existing child's enrollment on retry or after transfer.
        arms[arm] = campaign.fork_campaign(paths[arm], root, state["generation"],
                                          stage=state["parent_stage"], name=f"Delayed feedback / {arm}",
                                          seed=state["paired_seed"], evolve_trace=trace)
    # Keep both arms on the same challenge and restrict their lead to one batch.
    if state["plan"] is None:
        low = min(item["generation"] for item in arms.values())
        interval = arms["legacy"]["validation_interval"]
        state["plan"] = min(low + BATCH, (low // interval + 1) * interval, state["stop_round"])
        _save(root, state)
    for arm, status in arms.items():
        target = min(state["plan"], state["stop_round"])
        if status["generation"] < target:
            status = _advance(root, paths[arm], target, stage=state["parent_stage"])
            arms[arm] = status
        if status["state"] in ("complete", "review") or status["stage"] != state["parent_stage"]:
            state["stop_round"] = min(state["stop_round"], status["generation"])
    state["arms"] = {key: _brief(value) for key, value in arms.items()}
    low = min(item["generation"] for item in arms.values())
    if low >= state["stop_round"]:
        qualified = [arm for arm, status in arms.items()
                     if status["stage"] > state["parent_stage"] or status["state"] == "complete"]
        state["qualified"] = qualified
        state["comparison_result"] = {"generation": state["stop_round"], "qualified": qualified, "arms": state["arms"]}
        if any(arms[arm]["state"] in ("ready", "paused") for arm in qualified):
            state.update(phase="followup", plan=None, followup_targets=None,
                         message="The matched comparison is saved. Continuing the confirmed arms on the harder challenges.")
        else:
            _research(state, f"Matched comparison reached round {state['stop_round']}. Review the two arms before choosing another challenge.")
    else:
        if low >= state["plan"]:
            state["plan"] = None
        state["message"] = ("An experimental arm is paused by its operator."
                            if any(item["state"] == "paused" for item in arms.values())
                            else "Comparing the latest-action rule with evolvable eligibility traces.")


def _followup(root, state):
    paths = {arm: root / "comparison" / arm for arm in ARMS}
    arms = {arm: campaign.read_status(path) for arm, path in paths.items()}
    targets = state.get("followup_targets")
    if targets is None:
        targets = {arm: arms[arm]["generation"] + BATCH for arm in state["qualified"]
                   if arms[arm]["state"] == "ready"}
        state["followup_targets"] = targets
        _save(root, state)
    for arm, target in targets.items():
        arms[arm] = _advance(root, paths[arm], target)
    state["arms"] = {key: _brief(value) for key, value in arms.items()}
    if all(arms[arm]["state"] in ("complete", "review") for arm in state["qualified"]):
        _research(state, "The confirmed arms finished their prepared challenges or reached their study budget. Review their results.")
    else:
        if all(arms[arm]["generation"] >= target or arms[arm]["state"] != "ready" for arm, target in targets.items()):
            state["followup_targets"] = None
        state["message"] = "Continuing confirmed arms on the remaining challenges: " + ", ".join(state["qualified"]) + "."


def watch_tick(directory, *, tick_id=None):
    """Advance one batch. A pending absolute target survives interruption and re-entry."""
    root = Path(directory)
    source = campaign.read_status(root)
    admin = root / "supervisor"
    admin.mkdir(exist_ok=True)
    with campaign._batch_lock(admin):
        state = _load(root) or {"version": 1, "phase": "primary", "generation": source["generation"],
                                "plan": None, "message": "Ready.", "arms": {}, "last_tick_id": None}
        if state["phase"] == "research_review" or _held(root):
            return read_watch_status(root)
        if tick_id is not None and tick_id == state["last_tick_id"]:
            return read_watch_status(root)
        if state["phase"] == "primary":
            _primary(root, state, source)
        elif state["phase"] == "comparison":
            _pair(root, state)
        elif state["phase"] == "followup":
            _followup(root, state)
        else:
            raise ValueError("Unknown campaign supervisor phase.")
        if not _held(root):
            state["last_tick_id"] = tick_id
        _save(root, state)
        return read_watch_status(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--interval", type=int, default=300, help="Seconds between local batches (default 300).")
    actions = parser.add_mutually_exclusive_group()
    for action in ("once", "status", "pause", "resume"):
        actions.add_argument("--" + action, action="store_true")
    parser.add_argument("--tick-id", help="Optional retry identifier for --once.")
    args = parser.parse_args(argv)
    if args.interval < 1 or (args.tick_id and not args.once):
        parser.error("Use a positive interval and supply --tick-id only with --once.")
    try:
        if args.status:
            result = read_watch_status(args.run)
        elif args.pause or args.resume:
            result = set_watch_paused(args.run, args.pause)
        elif args.once:
            result = watch_tick(args.run, tick_id=args.tick_id)
        else:
            while True:
                token = f"clock/{args.interval}/{int(time.time()) // args.interval}"
                try:
                    result = watch_tick(args.run, tick_id=token)
                except (ValueError, sqlite3.OperationalError) as error:
                    if "Another worker" not in str(error) and "locked" not in str(error):
                        raise
                    print("Another worker is using this campaign. Retrying at the next interval.", flush=True)
                    time.sleep(args.interval)
                    continue
                print(json.dumps(result, ensure_ascii=True), flush=True)
                if result and result["phase"] == "research_review":
                    return
                time.sleep(args.interval)
        print(json.dumps(result, ensure_ascii=True))
    except KeyboardInterrupt:
        return
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Local campaign supervisor: {error}\n")


if __name__ == "__main__":
    main()
