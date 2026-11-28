"""Maintain a local aggregate and durable transition outbox independently of HTTP readers."""

from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from statistics import fmean, pstdev
import sys
import time

SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE.parent))
sys.path.insert(0, str(SITE.parent / "fledge"))
from fledge_events import DIRECTORY, connect
from wisp import campaign, dashboard_summary
from wisp.core import Genome
from wisp.experiment import identifier


CAMPAIGN = SITE.parent / "fledge" / "runs" / "personal.json.campaign"
PHASE_LABELS = {"preparing_study": "Preparing study", "running_validation": "Running validation",
                "running_confirmation": "Running confirmation", "reviewing_results": "Reviewing results",
                "preparing_successor": "Preparing successor", "evolving": "Breeding offspring",
                "review_complete": "Research decision recorded", "unscored": "Study ended unscored",
                "paused": "Paused", "blocked": "Blocked"}
SWAP = "one-blank-state-swap-v1"
PRACTICE = "stationary-one-blank-practice-v1"
BREEDING = "one-blank-stationary-breeding-v1"
UNSCORED = "bounded_study_unscored"


def checked(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Study path outside campaign")
    return path


def hashed_record(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Campaign artifact digest mismatch")
    return json.loads(raw)


@lru_cache(maxsize=8)
def population(root, generation):
    with closing(campaign._readonly(root / "campaign.sqlite3")) as db:
        row = db.execute("SELECT population,report FROM rounds WHERE generation=?", (generation,)).fetchone()
    if not row:
        return None
    groups, report = campaign._unpack(row[0]), json.loads(row[1])
    genomes = [genome for group in groups.values() for genome in group]
    accuracies = []
    for entry in report["candidates"]:
        measurements = [entry[k] for k in ("credit", "delayed", "immediate", "reversal")]
        accuracies.append(sum(c["correct"] for c in measurements) / sum(c["total"] for c in measurements))
    if not genomes or len(genomes) != len(accuracies):
        raise ValueError("Population and scored sample sizes differ")
    return dict(count=len(genomes), generation=generation, mean_accuracy=fmean(accuracies),
                accuracy_sd=pstdev(accuracies), mean_neurons=fmean(len(g["nodes"]) for g in genomes),
                mean_connections=fmean(len(g["edges"]) for g in genomes),
                basis="Primary campaign selection probes pooled per Wisp across four tasks. Spread is population SD.")


@lru_cache(maxsize=16)
def specimen_size(root, source, generation, fingerprint):
    with closing(campaign._readonly(checked(root, source) / "campaign.sqlite3")) as db:
        blob, record = db.execute("SELECT population,report FROM rounds WHERE generation=?", (generation,)).fetchone()
    record = json.loads(record)
    group, slot = record["champion_id"].split(":")
    genome = campaign._unpack(blob)[group][int(slot)]
    if record["fingerprint"] != fingerprint or identifier(Genome.from_dict(genome)) != fingerprint:
        raise ValueError("Specimen identity mismatch")
    return dict(count=1, neurons=len(genome["nodes"]), connections=len(genome["edges"]))


@lru_cache(maxsize=16)
def family_size(root, checkpoint, sha, keys, fingerprint):
    record=hashed_record(checked(root,checkpoint),sha)
    for key in keys:
        record=record[key]
    genome=Genome.from_dict(record["genome"])
    if identifier(genome)!=fingerprint:
        raise ValueError("Selected family member differs")
    return dict(count=1,neurons=len(genome.nodes),connections=len(genome.edges))


def unscored_study(root, record):
    review = hashed_record(checked(root, record["active_path"]) / "review.json", record["review_sha256"])
    if not (record["reports"] == review["reports"] == {} and review["by_split"] == {}
            and record["result"] == review["result"] == "unscored_insufficient_matching_support"
            and record["unscored_reason"] == review["unscored_reason"] == "empty_eligible_pool"
            and record["retained_both"] is None and review["retained_both"] is None):
        raise ValueError("Unscored receipt and registration disagree")
    result = dashboard_summary._study_info(record, review, Path(record["active_path"]).name)
    explanation = "Validation had no eligible matched receivers. Confirmation did not run. Neither bank yielded a score or an earlier-skill measurement."
    return dict(result, objective=record["objective"] + " " + explanation, retained_both=None, splits={},
                score_status="unscored", scored_banks=0, planned_banks=2)


def breeding_progress(root, active):
    path = checked(root, active["active_path"]) / "training-progress.json"
    progress = dashboard_summary._record(path)
    thresholds = active.get("thresholds", {})
    result = {"study": Path(active["active_path"]).name, "generation_budget": active.get("generation_budget"),
              "senses": thresholds.get("senses") or "Two graded light inputs show a stationary target, followed by one dark frame.",
              "success": thresholds.get("success") or "Beat the frozen ancestor by at least five points on each blank target side, with a positive paired lower bound in both banks. Retain visible localization and earlier skills.",
              "generation": 0, "population": None}
    if not progress:
        return result
    gen, budget, sample = progress["generation"], progress["generation_budget"], progress["population"]
    sample = sample.get("population", sample)
    if not (type(gen) is int and type(budget) is int and 0 <= gen <= budget and budget > 0
            and budget == active.get("generation_budget")
            and sample.get("generation",gen) == gen and type(sample["count"]) is int and sample["count"] > 0):
        raise ValueError("Invalid breeding progress or population size")
    fields = ("mean_accuracy", "accuracy_sd", "mean_neurons", "mean_connections")
    for key in fields:
        val = sample[key]
        if type(val) not in (int, float) or not math.isfinite(val) or val < 0:
            raise ValueError("Invalid breeding population measurement")
    if sample["mean_accuracy"] > 1 or sample["accuracy_sd"] > 1:
        raise ValueError("Invalid population accuracy")
    # Never copy checkpoint material, parentage, fingerprints, or the unrestricted source document.
    summary = {key: sample[key] for key in ("count", *fields)}
    summary["basis"] = "Balanced one-blank training accuracy per genome. Spread is population SD."
    return dict(result, generation=gen, generation_budget=budget, population=summary)


def breeding_bank(bank):
    rows, skills = [], []
    arms = {"finalist": "Selected offspring", "frozen_ancestor": "Frozen ancestor",
            "last_visible_decoder": "Last-visible decoder"}
    arms.update({key:name for key,name in (("matched_selection","Matched selection"),
                                           ("parent_finalist","Parent finalist")) if key in bank["held_out"]})
    conditions = {"blank": "one blank, pooled", "left_target": "one blank, left target",
                  "right_target": "one blank, right target", "visible": "target visible",
                  "dark_only": "both frames dark"}
    def score(raw):
        value = dashboard_summary._score(raw)
        if not value:
            raise ValueError("Invalid breeding score")
        return value
    for condition, label in conditions.items():
        for arm, name in arms.items():
            rows.append(dict(label=f"{name}, {label}", score=score(bank["held_out"][arm][condition])))
    for arm, field in (("finalist", "fresh_skills"), ("frozen_ancestor", "ancestor_fresh_skills")):
        name = arms[arm]
        metrics = bank[field]["metrics"]
        for label, raw in (("fresh mixed-delay recall", metrics["mixed_recall"]),
                           ("fresh immediate recall", metrics["old_skills"]["immediate"]),
                           ("fresh reversal", metrics["old_skills"]["reversal"])):
            skills.append(dict(label=f"{name}, {label}", score=score(raw)))
        for measure, label in (("fixed_recall", "fixed-delay recall"), ("fixed_one_shot", "one-shot")):
            for delay in ("1", "3", "5"):
                skills.append(dict(label=f"{name}, {label} at delay {delay}", score=score(metrics[measure][delay])))
        for phase in ("recall", "reversal"):
            rate = bank["post_practice"]["rates"][arm][phase]
            if type(rate) not in (int, float) or not 0 <= rate <= 1:
                raise ValueError("Invalid post-practice retention rate")
            skills.append(dict(label=f"{name}, post-practice {phase}", rate=rate))
    return {"lifetimes": bank["lifetimes"], "comparison": rows, "skill_rows": skills}


def last_completed(root, active):
    if not active:
        return None
    record = active
    visited = set()
    while record.get("state") != "bounded_study_complete":
        if record.get("state") == UNSCORED:
            unscored_study(root, record)
        sha = record.get("previous_review_sha256")
        if not sha:
            return None
        if sha in visited:
            raise ValueError("Repeated review in ancestry")
        visited.add(sha)
        record = hashed_record(checked(root, record["previous_review"]), sha)
    if not record.get("review_sha256"):
        return None
    path = checked(root, record["active_path"])
    review = hashed_record(path / "review.json", record["review_sha256"])
    if review["reports"] != record["reports"]:
        raise ValueError("Review and registration disagree on bank files")
    study = dashboard_summary._study(root, record)
    for name, expected in review["reports"].items():
        if Path(name).name != name:
            raise ValueError("Unexpected report filename")
        bank = hashed_record(path / name, expected)["bank"]
        split = bank["split"]
        if split not in ("validation", "confirmation"):
            raise ValueError("Unknown bank")
        view = study["splits"].setdefault(split, {})
        view["lifetimes"] = bank.get("lifetimes")
        if record["protocol"] == BREEDING or "right_choice_curve" in bank:
            study["splits"][split] = breeding_bank(bank)
            continue
        contrasts = record.get("thresholds", {}).get("contrasts")
        if record["protocol"] == SWAP or isinstance(contrasts, dict):
            view["comparison"] = []
            held = bank["held_out"]
            swap = record["protocol"] == SWAP
            arms = ("truthful", "nonplastic", "inherited_weights", "nonplastic_activity") if swap else record["controls"]
            conditions = ("visible", "blank", "dark_only") if swap else list(held[arms[0]])
            if (not arms or not conditions or len(arms) * len(conditions) > 256 or
                    any(not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", label) for label in (*arms, *conditions))):
                raise ValueError("Invalid registered measurement labels")
            for condition in conditions:
                for arm in arms:
                    score = dashboard_summary._score(bank["held_out"][arm][condition])
                    if not score:
                        raise ValueError("Invalid held-out score")
                    view["comparison"].append(dict(label=f"{arm.replace('_', ' ')}, {condition}", score=score))
            view["skill_rows"] = dashboard_summary._one_blank_rows(path, review, bank, split)["skill_rows"]
    return study


def collect(root):
    base = dashboard_summary.read_summary(root)
    active = dashboard_summary._record(root / "active_research.json")
    base["last_completed"] = last_completed(root, active)
    base["registered_study"] = active.get("active_path", "").split("/")[-1]
    if active.get("state") == "bounded_study_complete" and base["last_completed"]:
        base["study"] = base["last_completed"]
    elif active.get("state") == "preregistered":
        # A worker may save review.json just before committing the final registration.
        current = dashboard_summary._study_info(active, {}, base["registered_study"])
        base["study"] = dict(current, objective=active.get("objective"), splits={}, retained_both=None)
    elif active.get("state") == UNSCORED:
        base["study"] = unscored_study(root, active)
        for row in base["history"]:
            if (row.get("name"), row.get("completed_at")) == (base["study"]["name"], base["study"]["completed_at"]):
                row["state"] = UNSCORED
    base["population"] = population(root, base["campaign"]["generation"])
    base["specimen"] = None
    if active.get("study_kind") == "evolution":
        evolution = breeding_progress(root, active)
        base["evolution"] = evolution
        pop = evolution["population"]
        if pop:
            # The deployed dashboard reads study.objective. Label this as training so it stays
            # distinct from the held-out comparisons rendered in the bank selectors.
            caption = (f"Training generation {evolution['generation']} of {evolution['generation_budget']} "
                       f"included {pop['count']} Wisps with mean one-blank accuracy {pop['mean_accuracy']:.1%} "
                       f"(SD {pop['accuracy_sd'] * 100:.1f} points), mean {pop['mean_neurons']:.2f} neurons "
                       f"and {pop['mean_connections']:.2f} connections.")
            base["study"] = dict(base["study"], objective=base["study"]["objective"] + " " + caption)
        return base
    if active.get("source_fingerprint") and active.get("source_round") and active.get("source_path"):
        family=active.get("family_lineage")
        if family:
            size=family_size(root,family["checkpoint"],family["checkpoint_sha256"],tuple(family["member_keys"]),family["finalist"])
        else:
            size = specimen_size(root, active["source_path"], active["source_round"], active["source_fingerprint"])
        protocol = active.get("protocol")
        senses = "A stationary target appears on two graded visual inputs. The second frame can go dark."
        success = {PRACTICE: "Correct second turns after a blank must beat yoked feedback by 10 points and disabled learning by 5, while preserving visible accuracy and older skills.",
                   SWAP: "Reproduce the visible deficit, then measure recovery when weights or activity are restored, on both banks."}
        thresholds = active.get("thresholds", {})
        criterion = success.get(protocol) or thresholds.get("success")
        if isinstance(thresholds.get("contrasts"), dict) and thresholds["contrasts"]:
            primary = next(iter(thresholds["contrasts"].values()))
            criterion = (f"On {primary['condition']}, {primary['arm']} must exceed {primary['control']} by "
                         f"at least {100 * primary['minimum']:g} percentage points with a positive lower "
                         "paired 95% bound in validation and confirmation.")
        base["specimen"] = dict(size, study=active["active_path"].split("/")[-1],
                                senses=senses if protocol in success else thresholds.get("senses"),
                                success=criterion)
    return base


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,event INTEGER,body TEXT,transition INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS delivery(origin TEXT PRIMARY KEY, delivered INTEGER NOT NULL)")


def blocked_reason(directory):
    state = Path(directory).parent / "fledge-coordinator-state"
    try:
        if (state / "stopped.json").exists():
            return "Coordinator stopped for an integrity or worker check."
        if ((state/"standing-authority.json").is_file() and (state/"service-session.json").is_file()
                and not json.loads((state/"service-session.json").read_bytes()).get("standing_workflow")):
            return "The tested successor is ready. MSI is waiting for the installed coordinator to reload."
        status = json.loads((state / "status.json").read_bytes()).get("status")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(status, str):
        return None
    # Never publish exception text, paths, stderr or the coordinator status object.
    if status == "Windows read-only fixture sandbox unavailable":
        return "The read-only fixture sandbox is unavailable."
    if status == "awaiting verified hourly pause and idle":
        return "Awaiting confirmation that the hourly owner is paused and idle."
    if status == "Codex CLI unavailable":
        return "The Codex CLI is unavailable."
    if status.startswith("Codex invocation incomplete:") or status == "Codex returned no decision":
        return "Codex has not returned a decision."
    return None


def latest_decision(directory):
    path = Path(directory).parent/"fledge-coordinator-state/latest-decision.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_bytes())
    if not re.fullmatch(r"[a-z0-9-]{1,100}",record.get("study","")):
        raise ValueError("Invalid study in decision notice")
    fields = {"study":100,"notice":500,"question":700,"recorded_at":64}
    if any(not isinstance(record.get(k),str) or not 0<len(record[k])<=limit for k,limit in fields.items()):
        raise ValueError("Invalid public decision notice")
    return {k:record[k] for k in fields}


def refresh(directory=DIRECTORY, root=CAMPAIGN, *, force=False, collect_base=collect):
    with closing(connect(directory)) as db, db:
        initialize(db)
        previous = db.execute("SELECT event FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        since = previous["event"] if previous else 0
        pending = db.execute("SELECT * FROM events WHERE id>? ORDER BY id", (since,)).fetchall()
        if not pending and previous and not force:
            return False
        source_error = False
        try:
            base = collect_base(root)
            science_at = base.get("observed_at") or datetime.now(timezone.utc).isoformat()
        except (KeyError, ValueError, TypeError, OSError, sqlite3.Error):
            saved = db.execute("SELECT body FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
            base = json.loads(saved[0]) if saved else {"campaign": {"arms": []}, "history": [],
                "study": None, "last_completed": None, "specimen": None, "population": None}
            source_error = True
            science_at = base.get("observed_at")
        for event in pending or [None]:
            event_id = event["id"] if event else since
            history = [dict(row) for row in db.execute(
                """SELECT * FROM events WHERE id IN (
                   SELECT MAX(id) FROM events WHERE id<=? GROUP BY phase,study,objective,completed,total
                   ORDER BY MAX(id) DESC LIMIT 32) ORDER BY id DESC""", (event_id,))]
            for row in history:
                row["unit"] = "generations" if row["phase"] == "evolving" else "banks"
            current = history[0] if history else None
            doc = dict(base, transitions=history, progress=current)
            decision = latest_decision(directory)
            if decision:
                doc["latest_decision"] = decision
                # Earlier dashboard versions render the aggregate in their study or progress card.
                suffix=" Latest decision ("+decision["study"]+"): "+decision["notice"]
                for card in ("last_completed","study"):
                    if doc.get(card) and not source_error:
                        doc[card]=dict(doc[card],objective=(doc[card].get("objective") or "")+suffix)
                if current and current["phase"]=="preparing_successor" and current["study"]==decision["study"]:
                    doc["progress"]=dict(current,objective=decision["question"])
            if current:
                doc["activity"] = dict(state=current["phase"], label=PHASE_LABELS[current["phase"]])
                if current["phase"] == "evolving":
                    doc["activity"]["label"] = f"Breeding offspring · {current['completed']}/{current['total']} generations"
                finished = base.get("last_completed") or {}
                if (current["phase"] in ("preparing_study", "running_validation", "running_confirmation")
                        and current["study"].split("/")[-1] == base.get("registered_study")
                        and (base.get("study") or {}).get("state") == "bounded_study_complete"
                        and finished.get("completed_at")):
                    count = len(finished.get("splits", {}))
                    doc["progress"] = dict(current, phase="awaiting_review", completed=count,
                                           total=current["total"], observed_at=finished["completed_at"])
                    doc["activity"] = dict(state="awaiting_review", label="Study complete, awaiting review")
            measured = base.get("study") or {}
            if measured.get("score_status") == "unscored" and (not current or (
                    current["study"].split("/")[-1] == base.get("registered_study")
                    and current["phase"] in ("preparing_study", "running_validation", "running_confirmation",
                                             "unscored", "blocked"))):
                phase = "blocked" if current and current["phase"] == "blocked" else "unscored"
                doc["activity"] = dict(state=phase, label="Study unscored · 0/2 banks scored")
                doc["progress"] = dict(study=base["registered_study"], objective=measured["objective"],
                                       observed_at=measured["completed_at"], phase=phase, completed=0, total=2, unit="scored banks")
            if base["campaign"].get("paused"):
                doc["activity"] = dict(state="paused", label="Operator pause")
                if current:
                    doc["progress"] = dict(current, phase="paused")
            if source_error:
                doc["activity"] = dict(state="blocked", label="Campaign records unavailable or unverified")
                if current:
                    doc["progress"] = dict(current, phase="blocked")
            elif current and doc["activity"]["state"] == "blocked":
                reason = blocked_reason(directory)
                if reason:
                    doc["activity"]["reason"] = reason
            doc["observed_at"] = science_at
            doc["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO snapshots VALUES(NULL,?,?,?)",
                       (event_id, json.dumps(doc, allow_nan=False), int(event is not None)))
        # A heartbeat supersedes earlier heartbeats. Pending transitions stay durable.
        db.execute("DELETE FROM snapshots WHERE transition=0 AND id < (SELECT MAX(id) FROM snapshots)")
        db.execute("""DELETE FROM snapshots WHERE id < COALESCE((SELECT MIN(delivered) FROM delivery),0)
                      AND id < (SELECT MAX(id)-64 FROM snapshots)""")
        return True


def cached(directory=DIRECTORY):
    path = Path(directory, "monitor.sqlite3").resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT body FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError("Awaiting first observation")
    return row[0].encode("utf-8")


def maintain(stop, directory=DIRECTORY, root=CAMPAIGN):
    heartbeat = 0
    while not stop.is_set():
        try:
            if refresh(directory, root, force=time.monotonic() >= heartbeat):
                heartbeat = time.monotonic() + 25
        except (KeyError, ValueError, TypeError, OSError, sqlite3.Error):
            # Retain the last successful observation timestamp while sources are unavailable.
            stop.wait(2)
        stop.wait(.25)
