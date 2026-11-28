"""MSI-only two-bank evaluator for pinned, evaluation-only continuations of this campaign."""

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from marked_review_worker import digest, relative
from pan_campaign_worker import known_locks
from source_review_worker import atomic, campaign, campaign_watch, cursor, fp, json_bytes, now, read, root


BANK_SCRIPT = """
import importlib, json, sys
from pathlib import Path
from wisp.archived_pan import verified_nominee
module, protocol, source, generation, fingerprint, seed, earlier_seed, split = sys.argv[1:]
study = importlib.import_module(module)
if study.PROTOCOL != protocol:
    raise ValueError("Published protocol differs from registration")
champion = verified_nominee(Path(source), int(generation), fingerprint)
print(json.dumps(study.evaluate(champion, int(seed), int(earlier_seed), split), allow_nan=False))
"""


class TwoBankReview:
    def __init__(self, spec):
        self.spec = spec
        self.parent = root / "continuations" / spec.PARENT
        self.source = root / "continuations" / spec.SOURCE
        self.child = self.parent.parent / spec.CHILD
        self.archive = self.parent / "review-at-completion.json"
        self.proposal = root / "proposals" / spec.PROPOSAL
        self.code = Path(spec.CODE).resolve()

    def verify_code(self):
        def git(*args):
            return subprocess.check_output(["git", "-c", "safe.directory=" + self.code.as_posix(),
                                            "-C", str(self.code), *args], text=True).strip()
        if (git("rev-parse", "HEAD") != self.spec.REVISION
                or git("rev-parse", "HEAD^{tree}") != self.spec.TREE
                or git("status", "--porcelain", "--untracked-files=all")):
            raise RuntimeError("Pinned research checkout or working tree differs.")

    def guard(self):
        status = campaign_watch.read_watch_status(root)
        if not status or status["phase"] != "research_review" or status["paused"]:
            raise RuntimeError("Campaign is held or supervisor owns this campaign.")
        current = {d for d in self.parent.parent.iterdir() if d.is_dir()}
        names = {d.name for d in current}
        if names not in (self.spec.SIBLINGS, self.spec.SIBLINGS | {self.child.name}):
            raise RuntimeError("Unexpected continuation. Inspect ownership.")
        for d in current | set(known_locks):
            if ((d / "paused").exists() or
                    ((d / "campaign.sqlite3").exists() and campaign.read_status(d)["state"] == "paused")):
                raise RuntimeError(f"Operator hold at {d}.")

    def predecessor(self):
        self.guard()
        active = read(cursor)
        claimed = active["active_path"] == relative(self.child)
        path = self.archive if claimed else cursor
        proposal_input = self.proposal if self.proposal.exists() else getattr(self.spec, "PROPOSAL_SOURCE", self.proposal)
        if digest(path) != self.spec.PARENT_SHA or digest(proposal_input) != self.spec.PROPOSAL_SHA:
            raise RuntimeError("Parent pointer or frozen proposal changed.")
        parent = read(path)
        if (parent["active_path"], parent["source_path"], parent["state"], parent["source_round"],
            parent["source_fingerprint"], parent["automatic_handoff"],
            parent["result"]) != (relative(self.parent), relative(self.source), getattr(self.spec, "PARENT_STATE", "bounded_study_complete"),
                                  self.spec.GENERATION, fp, False, self.spec.PARENT_RESULT):
            raise RuntimeError("Unexpected archived lineage or predecessor decision.")
        if (parent["review_sha256"] != self.spec.REVIEW_SHA
                or digest(root / parent["review_path"]) != self.spec.REVIEW_SHA
                or digest(root / parent["previous_review"]) != parent["previous_review_sha256"]
                or any(digest(self.parent / name) != sha for name, sha in parent["reports"].items())):
            raise RuntimeError("Predecessor report or ancestry hash differs.")
        prior = read(root / parent["review_path"])
        if (prior["result"] != parent["result"] or prior["reports"] != parent["reports"] or
                prior["retained_both"] != parent["retained_both"] or
                prior["result"] == "instrumentation_failed" or
                any(row.get("instrumentation_passed") is False for row in prior.get("by_split", {}).values())):
            raise RuntimeError("Predecessor review failed integrity or instrumentation checks.")
        with closing(campaign._readonly(self.source / "campaign.sqlite3")) as db:
            pop, report = db.execute("SELECT population,report FROM rounds WHERE generation=?",
                                     (self.spec.GENERATION,)).fetchone()
        if (campaign.read_status(self.source)["generation"] != self.spec.GENERATION or
            json.loads(report)["fingerprint"] != fp or
            campaign._digest(campaign._unpack(pop)) != parent["parent_population_sha256"] or
            campaign._digest(json.loads(report)) != parent["parent_record_sha256"]):
            raise RuntimeError("Source nomination or archive changed.")
        used = {campaign.read_status(d)["seed"] for d in known_locks if (d / "campaign.sqlite3").exists()}
        for name in self.spec.SIBLINGS:
            for artifact in (self.parent.parent / name).glob("*.json"):
                data = read(artifact)
                used.add(data.get("seed"))
                used.update(data.get("seeds", {}).values())
        if len(set(self.spec.SEEDS.values())) != getattr(self.spec, "SEED_COUNT", 2) or used.intersection(self.spec.SEEDS.values()):
            raise RuntimeError("Proposed seeds are not distinct and fresh.")
        return parent, claimed

    def write(self, path, data):
        self.guard()
        atomic(path, json_bytes(data))

    def registration(self, parent, claimed, launch):
        s = self.spec
        plan = dict(active_path=relative(self.child), parent_path=relative(self.parent),
                    source_path=relative(self.source), source_round=s.GENERATION, source_fingerprint=fp,
                    parent_population_sha256=parent["parent_population_sha256"],
                    parent_record_sha256=parent["parent_record_sha256"], protocol=s.PROTOCOL,
                    previous_review=relative(self.archive), previous_review_sha256=s.PARENT_SHA,
                    prior_diagnostic_review_sha256=s.REVIEW_SHA, proposal_path=relative(self.proposal),
                    proposal_sha256=s.PROPOSAL_SHA, research_revision=s.REVISION, research_tree=s.TREE,
                    module_sha256=digest(getattr(s, "MODULE_FILE", self.code / Path(*s.MODULE.split(".")).with_suffix(".py"))),
                    seeds=s.SEEDS, state="preregistered", automatic_handoff=False,
                    objective=s.OBJECTIVE, bounds=s.BOUNDS, controls=s.CONTROLS,
                    thresholds={**s.THRESHOLDS, **{k:v for k,v in parent["thresholds"].items() if k.startswith("old_")}},
                    next_action="Evaluate the two registered banks serially, then stop for research review.")
        extra = getattr(s, "EXTRA_REGISTRATION", {})
        if plan.keys() & extra.keys():
            raise ValueError("Extra registration fields overlap fixed provenance")
        plan.update(extra)
        path = self.child / "preregistration.json"
        if self.archive.exists() and digest(self.archive) != s.PARENT_SHA:
            raise RuntimeError("Parent receipt conflicts.")
        if path.exists():
            saved = read(path)
            if {k:v for k,v in saved.items() if k != "created_at"} != plan:
                raise RuntimeError("Existing child registration conflicts.")
            plan = saved
        elif claimed or (self.child.exists() and any(self.child.iterdir())):
            raise RuntimeError("Unregistered child contains artifacts.")
        if not launch:
            return plan
        if not claimed and getattr(self.spec, "PUBLISH_EVENTS", False):
            from fledge_events import emit
            emit("preparing_study", s.CHILD, s.OBJECTIVE, 0, 2)
        if not self.proposal.exists():
            self.guard()
            contents = self.spec.PROPOSAL_SOURCE.read_bytes()
            if hashlib.sha256(contents).hexdigest() != s.PROPOSAL_SHA:
                raise RuntimeError("Staged proposal changed.")
            atomic(self.proposal, contents)
        if not path.exists():
            self.guard()
            if not self.archive.exists():
                atomic(self.archive, cursor.read_bytes())
            self.child.mkdir(exist_ok=True)
            plan["created_at"] = now()
            self.write(path, plan)
        if digest(self.archive) != s.PARENT_SHA:
            raise RuntimeError("Previous decision not archived.")
        if not claimed:
            self.write(cursor, plan)
        return plan

    def bank_command(self, split):
        s = self.spec
        return [sys.executable, "-B", "-c", BANK_SCRIPT, s.MODULE, s.PROTOCOL, str(self.source.resolve()),
                str(s.GENERATION), fp, str(s.SEEDS["sensory"]), str(s.SEEDS["earlier_skills"]), split]

    def evaluate(self, split):
        args = self.bank_command(split)
        deadline = time.monotonic() + getattr(self.spec, "BANK_TIMEOUT", 1200)
        with subprocess.Popen(args, cwd=self.code, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as p:
            try:
                while True:
                    self.guard()
                    if time.monotonic() > deadline:
                        raise RuntimeError("Registered bank exceeded its wall-clock limit")
                    try:
                        out, err = p.communicate(timeout=.3)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                if sys.platform == "win32" and p.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                p.kill()
                p.communicate()
                raise
        if p.returncode:
            raise RuntimeError(err[-2000:])
        return json.loads(out)

    def collect(self, plan):
        active = read(cursor)
        review_path = self.child / "review.json"
        if active["state"] == "bounded_study_complete":
            saved = read(review_path)
            if (any(active.get(k) != v for k,v in plan.items() if k not in {"state", "next_action"})
                    or digest(review_path) != active["review_sha256"]
                    or active["review_path"] != relative(review_path)
                    or any(saved[k] != active[k] for k in ("result", "retained_both", "reports", "completed_at"))
                    or any(digest(self.child / n) != h for n,h in active["reports"].items())):
                raise RuntimeError("Completed child receipt changed.")
            return active["result"]
        if active != plan:
            raise RuntimeError("Active research ownership changed.")
        saved_review = read(review_path) if review_path.exists() else None
        rows, reports = {}, {}
        for split in ("validation", "confirmation"):
            self.guard()
            self.verify_code()
            path = self.child / f"{self.spec.PREFIX}-{split}.json"
            provenance = dict(split=split, protocol=self.spec.PROTOCOL, seeds=self.spec.SEEDS,
                              source_path=relative(self.source), source_round=self.spec.GENERATION,
                              source_fingerprint=fp, research_revision=self.spec.REVISION)
            if path.exists():
                bank = read(path)
                sha = bank.pop("payload_sha256")
                if bank["provenance"] != provenance or hashlib.sha256(json_bytes(bank)).hexdigest() != sha:
                    raise RuntimeError("Saved bank provenance or hash differs.")
                row = bank["bank"]
            else:
                if saved_review is not None:
                    raise RuntimeError("A completed review exists but its bank is missing. Integrity stop.")
                if getattr(self.spec, "PUBLISH_EVENTS", False):
                    from fledge_events import emit
                    emit("running_" + split, self.spec.CHILD, self.spec.OBJECTIVE, len(rows), 2)
                row = self.evaluate(split)
            self.spec.validate_bank(row, split)
            if not path.exists():
                bank = dict(provenance=provenance, bank=row)
                self.write(path, {**bank, "payload_sha256": hashlib.sha256(json_bytes(bank)).hexdigest()})
            rows[split] = row
            reports[path.name] = digest(path)
            if not row["instrumentation_passed"]:
                break
        review = dict(protocol=self.spec.PROTOCOL, source_fingerprint=fp, reports=reports, **self.spec.summarize(rows))
        path = self.child / "review.json"
        if path.exists():
            saved = read(path)
            if {k:v for k,v in saved.items() if k != "completed_at"} != review:
                raise RuntimeError("Completed review conflicts.")
            review = saved
        else:
            review["completed_at"] = now()
            self.write(path, review)
        self.write(cursor, dict(plan, state="bounded_study_complete", result=review["result"],
                                retained_both=review["retained_both"], reports=reports,
                                review_path=relative(path), review_sha256=digest(path), completed_at=review["completed_at"],
                                next_action="Stop at review. Automatic handoff remains disabled."))
        if getattr(self.spec, "PUBLISH_EVENTS", False):
            from fledge_events import emit
            emit("reviewing_results", self.spec.CHILD, self.spec.OBJECTIVE, len(rows), 2)
        return review["result"]

    def main(self):
        parser = argparse.ArgumentParser(description=self.spec.OBJECTIVE)
        parser.add_argument("--run", action="store_true", help="Register and run the approved bounds under shared locks")
        args = parser.parse_args()
        self.verify_code()
        if not args.run:
            self.registration(*self.predecessor(), launch=False)
            print("Read-only preflight passed. Launch repeats all checks under shared locks.")
            return
        with ExitStack() as locks:
            for d in known_locks:
                locks.enter_context(campaign._batch_lock(d))
            self.verify_code()
            plan = self.registration(*self.predecessor(), launch=True)
            print(self.collect(plan))
