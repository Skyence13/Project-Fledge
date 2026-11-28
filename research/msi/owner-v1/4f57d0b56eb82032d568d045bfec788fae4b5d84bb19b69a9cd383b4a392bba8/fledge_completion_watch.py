"""Read one completed one-blank practice review on MSI. No worker or launch code."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

EXPECTED_CHILD = "continuations/stationary-one-blank-practice-r5"


def aggregate(doc):
    banks = {}
    for split, row in doc["by_split"].items():
        if split not in {"validation", "confirmation"}:
            raise ValueError("Unexpected evaluation bank")
        scores = {
            arm: {condition: {k: row["held_out"][arm][condition][k] for k in ("correct", "total")}
                  for condition in ("visible", "blank", "dark_only")}
            for arm in ("accurate", "yoked", "nonplastic")
        }
        contrasts = {arm: {k: row["blank_accurate_minus_control"][arm][k] for k in ("difference", "ci95")}
                     for arm in ("yoked", "nonplastic")}
        banks[split] = dict(scores=scores, contrasts=contrasts, gates=row["gates"], passed=row["passed"])
    if "validation" not in banks:
        raise ValueError("Missing validation bank")
    return dict(child=EXPECTED_CHILD, result=doc["result"], retained_both=doc["retained_both"], banks=banks)


def try_lock(handle):
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


def check_once(campaign, state, reviewer):
    if state.resolve().is_relative_to(campaign.resolve()):
        raise ValueError("Receipt directory must be outside the campaign")
    registry = campaign / "active_research.json"
    reg = json.loads(registry.read_bytes())
    if reg["active_path"] != EXPECTED_CHILD or reg["state"] != "bounded_study_complete":
        return "waiting"
    if reg["review_path"] != EXPECTED_CHILD + "/review.json":
        raise ValueError("Registered review path differs from expected child")
    source = (campaign / reg["review_path"]).resolve()
    if not source.is_relative_to(campaign.resolve()):
        raise ValueError("Review resolves outside campaign")
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != reg["review_sha256"]:
        raise ValueError("Review SHA-256 differs from registration")
    state.mkdir(exist_ok=True, parents=True)
    with (state / "review.lock").open("a+b") as lock:
        if not try_lock(lock):
            return "busy"
        receipt = state / (digest + ".json")
        if receipt.exists():
            saved = json.loads(receipt.read_bytes())
            if saved["sha256"] != digest or not saved["summary"].strip():
                raise ValueError("Invalid receipt")
            return "already reviewed"
        if json.loads(registry.read_bytes()) != reg:
            return "waiting"
        packet = aggregate(json.loads(data))
        summary = reviewer(packet).strip()
        if not summary:
            raise ValueError("Empty CLI summary")
        saved = dict(sha256=digest, aggregate=packet, summary=summary)
        pending = receipt.with_suffix(".tmp")
        with pending.open("w", encoding="utf-8") as output:
            json.dump(saved, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, receipt)
        print(summary, flush=True)
        print("Local receipt:", receipt, flush=True)
        return "reviewed"


def codex_review(packet):
    cli = Path(os.environ["APPDATA"]) / "npm" / "codex.cmd"
    prompt = ("Summarize the aggregate study below for Morgan and Rook. Use only the supplied "
              "JSON and no tools. Report each available bank's held-out one-blank scores for "
              "accurate, approval-matched yoked and nonplastic arms, the paired contrasts and "
              "intervals in percentage points, retained visible and older skills, and failed gates. "
              "Scope conclusions to this nominee. Distinguish an absent bank from a failed bank. "
              "Suggest one next question. Do not launch anything.\n" + json.dumps(packet))
    with tempfile.TemporaryDirectory(prefix="fledge-review-") as directory:
        answer = Path(directory) / "summary.txt"
        subprocess.run([str(cli), "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                        "--ephemeral", "--output-last-message", str(answer), "-"],
                       input=prompt, encoding="utf-8", cwd=directory, check=True,
                       timeout=300, stdout=subprocess.DEVNULL)
        return answer.read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--watch", action="store_true", help="Wait for this single child's completion")
    args = parser.parse_args()
    delay = 15
    previous = None
    while True:
        try:
            status = check_once(args.campaign, args.state, codex_review)
            delay = 15
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if not args.watch:
                raise
            status = "CLI unavailable; review pending (" + type(error).__name__ + ")"
            delay = min(600, delay * 2)
        if status != previous:
            print(status, flush=True)
            previous = status
        if not args.watch or status in ("reviewed", "already reviewed"):
            return
        time.sleep(delay)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit("Review stopped: " + str(exc))
