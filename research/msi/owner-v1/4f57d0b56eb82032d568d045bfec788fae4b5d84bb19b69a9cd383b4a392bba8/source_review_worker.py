import argparse
import hashlib
import json
import os
import sys
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path.cwd()))
from wisp import campaign, campaign_watch, mixed_delay

root = Path("runs/personal.json.campaign")
parent_path = "continuations/mixed-transfer-trace-r30"
active_path = "continuations/single-grade-source-mixed-r5"
parent = root / parent_path
child = root / active_path
cursor = root / "active_research.json"
fp = "b6d09864da20dde330fa8712acf97b0a8a11ab3ea90f55625e46bfe235243616"
protocol = "wisp-single-grade-source-v1"

def now():
    return datetime.now(timezone.utc).isoformat()

def read(p):
    return json.loads(p.read_bytes())

def atomic(p, content):
    with NamedTemporaryFile("wb", dir=p.parent, delete=False) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stream.name, p)

def json_bytes(data):
    return (json.dumps(data, indent=2, allow_nan=False) + "\n").encode()

def guard():
    s = campaign_watch.read_watch_status(root)
    if not s or s["phase"] != "research_review" or s["paused"]:
        raise RuntimeError("Supervisor no longer idle at review or operator hold active.")
    for path in [root, parent, root/"comparison/trace", root/"comparison/legacy"]:
        if campaign.read_status(path)["state"] == "paused":
            raise RuntimeError(f"Operator held {path}")
    if (child/"paused").exists():
        raise RuntimeError("Study paused.")

def freeze():
    guard()
    current = read(cursor)
    if current["active_path"] == active_path:
        print("Existing research path; entry already frozen")
        return
    if current["active_path"] != parent_path or current["state"] != "bounded_study_complete":
        raise RuntimeError("Previous research cursor changed.")
    paths = sorted(p.name for p in parent.parent.iterdir() if p.is_dir())
    if paths != [parent.name]:
        raise RuntimeError(f"Unexpected child directory. Inspect manually. {paths}")
    old_archive = parent/"review-at-completion.json"
    if old_archive.exists() and old_archive.read_bytes() != cursor.read_bytes():
        raise RuntimeError("Previous review archive conflicts with current pointer.")
    if campaign.read_status(parent)["generation"] != 5:
        raise RuntimeError("Source endpoint changed.")
    with closing(campaign._readonly(parent/"campaign.sqlite3")) as db:
        data, rec = db.execute("SELECT population, report FROM rounds WHERE generation=5").fetchone()
    rec = json.loads(rec)
    if rec["fingerprint"] != fp:
        raise RuntimeError("Source nomination changed.")
    plan = {
        "active_path":active_path, "parent_path":parent_path, "source_round":5,
        "source_fingerprint":fp, "parent_record_sha256":campaign._digest(rec),
        "parent_population_sha256":campaign._digest(campaign._unpack(data)),
        "previous_review":(old_archive.relative_to(root)).as_posix(),
        "protocol":protocol, "state":"preregistered", "created_at":now(),
        "objective":"Measure single-grade source recall with varying unannounced lag and task-irrelevant choices. Identify outcomes conditional on whether final nuisance and source actions agree.",
        "design":"Evaluation-only continuation of the existing R5 training nominee. Use fresh, paired validation and confirmation banks; do not select a different nominee. Counterbalance hidden target, stratify by lag and realized agreement. One source genotype and one family. No population enrollment or selection.",
        "seeds":{"source_attribution":2026112704,"older_lessons":2026112705},
        "thresholds":{
          "pooled_at_each_lag_min":0.75,"conflict_at_each_lag_min":0.75,
          "intact_min_conflict_histories_per_lag":16,"intact_min_agreement_histories_per_lag":1,
          "intact_gain_over_trace_disabled_and_recent_min":0.15,
          "old_mixed_recall_min":0.90,"old_each_fixed_recall_min":0.90,
          "old_max_drop_from_pooled_fixed":0.05,"old_each_fixed_one_shot_min":0.75,
          "old_immediate_min":0.90,"old_reversal_min":0.90},
        "decision":"Both banks must meet all criteria independently. Each pre-final-grade random history is paired across two opposite target assignments. Insufficient conflict histories or absent agreement is inconclusive for supported conflict skill. Use arm-specific observed cells for description and compare controls on pooled equally weighted lags across identical schedule banks. Previous and new held-out tasks are distinct. No automatic promotion.",
        "bounds":"One archived nominee, once on each protocol and held-out split, no evolution. Reuse an existing report only after verifying provenance.",
        "next_action":"Complete the preregistered two-bank review. Do not enroll a population or extend the earlier five-round run."}
    guard()
    if not old_archive.exists(): atomic(old_archive, cursor.read_bytes())
    child.mkdir()
    atomic(child/"preregistration.json",json_bytes(plan))
    atomic(cursor,json_bytes(plan))
    print("Frozen", plan["active_path"])

def collect():
    from wisp import source_attribution
    assert source_attribution.PROTOCOL == protocol
    current = read(cursor)
    if current["active_path"] != active_path:
        raise RuntimeError("Research pointer changed")
    if current["state"] == "bounded_study_complete":
        print("Already reviewed")
        return
    source = mixed_delay.archived_nominee(parent,5)
    assert source["fingerprint"] == fp
    for split in ("validation","confirmation"):
        for name, func, seed, version in [
            ("source-attribution", source_attribution.assess, current["seeds"]["source_attribution"], protocol),
            ("earlier-skills", mixed_delay.transfer, current["seeds"]["older_lessons"], mixed_delay.PROTOCOL)]:
            guard()
            path = child/(name+"-"+split+".json")
            if path.exists():
                existing = read(path)
                if (existing["fingerprint"],existing["protocol"],existing["seed"],existing["split"]) != (fp,version,seed,split):
                    raise RuntimeError(f"Saved {path} conflicts with plan")
                continue
            report = func(source, seed, split)
            guard()
            if (report["fingerprint"],report["protocol"],report["seed"],report["split"]) != (fp,version,seed,split):
                raise RuntimeError("Fresh report provenance mismatch")
            report.update(source_path=parent_path, source_round=5)
            atomic(path,json_bytes(report))
    print("Saved all four reports")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("freeze","collect"))
    mode=parser.parse_args().action
    with ExitStack() as locks:
        for p in (root/"supervisor",root,root/"comparison/trace",root/"comparison/legacy",parent):
            locks.enter_context(campaign._batch_lock(p))
        guard()
        if mode=="freeze":
            freeze()
        else:
            collect()

