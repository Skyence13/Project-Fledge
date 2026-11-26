"""Run a checkpointed delayed-feedback evolution campaign alongside the original lab."""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from zipfile import BadZipFile
import zlib

from . import challenge_evolution as evolution

VERSION = "wisp-challenge-campaign-v1"
VALIDATE_EVERY = 5
REQUIRED_PASSES = 2
STAGE_BUDGET = 60
MAX_BATCH = 20
SCHEMA = (
    "CREATE TABLE tip (id INTEGER PRIMARY KEY CHECK(id=1),status TEXT NOT NULL,population BLOB NOT NULL)",
    "CREATE TABLE rounds (generation INTEGER PRIMARY KEY,population BLOB NOT NULL,report TEXT NOT NULL)",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pack(value):
    return zlib.compress(_encode(value).encode())


def _digest(value):
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def _unpack(value):
    return json.loads(zlib.decompress(value))


def _readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No database at {path}.")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)


def archived_cohort(archive, branch, generation):
    """Read one sealed row in a consistent transaction. A live exclusive lab requires an export."""
    from .transfer import _restore_lab
    if type(generation) is not int or generation < 1:
        raise ValueError("Choose a positive completed generation.")
    try:
        with closing(_readonly(archive)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported lineage archive schema.")
            selected = current = db.execute("SELECT * FROM branches WHERE id=?", (branch,)).fetchone()
            if selected is None or generation > selected["completed_generation"]:
                raise ValueError("Choose a completed generation on an existing lineage.")
            visited = set()
            while current["parent_id"] is not None and generation <= current["fork_generation"]:
                visited.add(current["id"])
                current = db.execute("SELECT * FROM branches WHERE id=?", (current["parent_id"],)).fetchone()
                if current is None or current["id"] in visited:
                    raise ValueError("Invalid lineage ancestry.")
            row = db.execute("SELECT checkpoint,summary FROM generations WHERE branch_id=? AND generation=?",
                             (current["id"], generation)).fetchone()
            if row is None:
                raise ValueError("Generation is missing from its lineage.")
            lab = _restore_lab(row["checkpoint"])
            if (not lab.awaiting_next or lab.generation != generation or not lab.history
                    or lab.history[-1] != json.loads(row["summary"])):
                raise ValueError("The archived checkpoint and summary disagree.")
            return lab.checkpoint(), {"kind": "archive", "branch_id": branch, "branch_name": selected["name"],
                                      "origin_branch_id": current["id"], "generation": generation}
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise ValueError("This archive is open in a lab. Use Export run, then pass --export ZIP.") from error
        raise


@contextmanager
def _batch_lock(directory):
    # A separate SQLite transaction owns the OS lock for a whole invocation.
    # It disappears on process exit and does not block status reads or pause.
    with closing(sqlite3.connect(directory / "writer.lock", timeout=0)) as lock:
        try:
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                raise ValueError("Another worker already owns this campaign batch.") from error
            raise
        yield


def _load(db):
    row = db.execute("SELECT status,population FROM tip WHERE id=1").fetchone()
    if row is None:
        raise ValueError("Campaign initialization is incomplete.")
    status = json.loads(row[0])
    if status["version"] != VERSION:
        raise ValueError("Unsupported evolutionary campaign version.")
    return status, _unpack(row[1])


def read_status(directory):
    """Compact, read-only public snapshot; safe while another process evaluates a generation."""
    with closing(_readonly(Path(directory) / "campaign.sqlite3")) as db:
        row = db.execute("SELECT status FROM tip WHERE id=1").fetchone()
        if not row:
            raise ValueError("Campaign initialization is incomplete.")
        status = json.loads(row[0])
        if status["version"] != VERSION:
            raise ValueError("Unsupported evolutionary campaign version.")
        return status


def init_campaign(directory, checkpoint, source, *, name, seed):
    population = evolution.population_from_checkpoint(checkpoint)
    source = {key: source[key] for key in ("kind", "branch_id", "branch_name", "generation", "origin_branch_id")}
    source["population_sha256"] = _digest(population)
    return _init_population(directory, population, source, name=name, seed=seed, stage=0, parent=None,
                            evolve_trace=False)


def fork_campaign(directory, parent_directory, generation, *, stage, name, seed, evolve_trace=False):
    """Enroll the evaluated population of a saved campaign round in a new experiment."""
    if Path(directory).resolve() == Path(parent_directory).resolve():
        raise ValueError("Choose a new campaign directory for the fork.")
    if type(generation) is not int or generation < 1:
        raise ValueError("Choose a completed campaign generation.")
    if type(stage) is not int or not 0 <= stage < len(evolution.STAGES):
        raise ValueError("Choose a defined initial challenge stage.")
    with closing(_readonly(Path(parent_directory) / "campaign.sqlite3")) as db:
        db.execute("BEGIN")
        parent_status, _ = _load(db)
        row = db.execute("SELECT population,report FROM rounds WHERE generation=?", (generation,)).fetchone()
        if row is None:
            raise ValueError("That campaign generation has no evaluated population.")
        population, record = _unpack(row[0]), json.loads(row[1])
    parent = {"name": parent_status["name"], "seed": parent_status["seed"], "generation": generation,
              "stage": record["stage"], "population_sha256": _digest(population), "record_sha256": _digest(record)}
    return _init_population(directory, population, parent_status["source"],
                            name=name, seed=seed, stage=stage, parent=parent, evolve_trace=evolve_trace)


def _init_population(directory, population, source, *, name, seed, stage, parent, evolve_trace):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("Use a campaign name of 1 through 80 characters.")
    if type(seed) is not int:
        raise ValueError("Use an integer campaign seed.")
    if type(evolve_trace) is not bool:
        raise ValueError("Choose whether inherited eligibility traces may mutate.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "campaign.sqlite3"
    with _batch_lock(directory), closing(sqlite3.connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
            for statement in SCHEMA:
                db.execute(statement)
            db.execute("PRAGMA user_version=1")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported campaign database.")
        if db.execute("SELECT 1 FROM tip").fetchone():
            existing, _ = _load(db)
            source_identity = ("population_sha256", "branch_id", "generation", "origin_branch_id")
            if (existing["name"] != name.strip() or existing["seed"] != seed or existing.get("parent_campaign") != parent
                    or existing.get("initial_stage", 0) != stage or existing.get("evolve_trace", False) != evolve_trace or
                    any(existing["source"].get(k) != source.get(k) for k in source_identity)):
                raise ValueError("This directory belongs to a different campaign. Choose a fresh --run directory.")
            return existing
        status = {"version": VERSION, "name": name.strip(), "source": source, "seed": seed, "state": "ready",
                  "parent_campaign": parent, "enrollment_sha256": _digest(population), "initial_stage": stage,
                  "protocol": evolution.PROTOCOL, "evolve_trace": evolve_trace,
                  "reason": "Enrolled archived population.", "generation": 0, "stage": stage,
                  "stage_label": evolution.STAGES[stage]["label"], "stage_generations": 0, "streak": 0,
                  "validation_interval": VALIDATE_EVERY, "required_passes": REQUIRED_PASSES,
                  "stage_budget": STAGE_BUDGET, "last_review": None, "last_training": None,
                  "population_size": sum(map(len, population.values())), "updated_at": _now()}
        db.execute("INSERT INTO tip VALUES(1,?,?)", (_encode(status), _pack(population)))
    return status


def _hold(directory, pause):
    path = Path(directory) / "campaign.sqlite3"
    if not path.is_file():
        raise FileNotFoundError(f"No database at {path}.")
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        status, _ = _load(db)
        if pause and status["state"] == "ready":
            status.update(state="paused", reason="Paused by operator.")
        elif not pause and status["state"] == "paused":
            status.update(state="ready", reason="Operator resumed the campaign.")
        else:
            return status
        status["updated_at"] = _now()
        db.execute("UPDATE tip SET status=? WHERE id=1", (_encode(status),))
    return status


def pause_campaign(directory):
    return _hold(directory, True)


def resume_campaign(directory):
    return _hold(directory, False)


def _review(status, champion):
    """Validation can nominate advancement. Confirm on a fresh bank before changing stages."""
    stage, generation = status["stage"], status["generation"]
    kwargs = {"seed": status["seed"], "stage": stage, "generation": generation}
    validation = evolution.validate_champion(champion, **kwargs, split="validation")
    status["streak"] = status["streak"] + 1 if validation["passed"] else 0
    status["reason"] = (f"Validation passed ({status['streak']} consecutive)."
                        if validation["passed"] else "Validation criteria not yet met on this challenge.")
    record = {"generation": generation, "stage": stage, "label": status["stage_label"],
              "champion_id": champion["id"], "fingerprint": champion["fingerprint"],
              "validation": validation, "confirmation": None, "decision": "continue"}
    if status["streak"] >= status["required_passes"]:
        confirmation = evolution.validate_champion(champion, **kwargs, split="confirmation")
        record["confirmation"] = confirmation
        if confirmation["passed"]:
            if stage + 1 == len(evolution.STAGES):
                status.update(state="complete", reason="All challenges met their confirmation criteria.")
                record["decision"] = "complete"
            else:
                status.update(stage=stage + 1, stage_label=evolution.STAGES[stage + 1]["label"],
                              stage_generations=0, streak=0, reason="Qualified for the next challenge.")
                record["decision"] = "promote"
        else:
            status["streak"] = 0
            status["reason"] = "Independent confirmation missed a criterion. Continuing this challenge."
            record["decision"] = "confirmation_failed"
    if status["state"] == "ready" and status["stage_generations"] >= status["stage_budget"]:
        status.update(state="review", reason="Stage generation budget reached. Review the evidence before extending.")
        record["decision"] = "review"
    status["last_review"] = record
    return record


def run_campaign(directory, through):
    """Evaluate through an absolute campaign generation, committing each generation exactly once."""
    directory = Path(directory)
    database = directory / "campaign.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(f"No database at {database}.")
    if type(through) is not int or through < 1:
        raise ValueError("--through must be a positive absolute campaign generation.")
    with _batch_lock(directory), closing(sqlite3.connect(database, timeout=5)) as db:
        status, population = _load(db)
        if status["state"] != "ready" or through <= status["generation"]:
            return status
        if status.get("protocol", evolution.PROTOCOL) != evolution.PROTOCOL:
            raise ValueError("The campaign uses a different scoring protocol. Fork a saved generation to continue.")
        if through > status["generation"] + MAX_BATCH:
            raise ValueError(f"Request at most {MAX_BATCH} additional generations per batch.")
        while status["state"] == "ready" and status["generation"] < through:
            generation = status["generation"] + 1
            stage = status["stage"]
            scores = evolution.score_generation(population, seed=status["seed"], stage=stage, generation=generation)
            status.update(generation=generation, stage_generations=status["stage_generations"] + 1,
                          last_training=scores["summary"])
            if status["last_review"] is None:
                status["reason"] = "Waiting for the first scheduled validation."
            review = None
            if status["stage_generations"] % status["validation_interval"] == 0:
                review = _review(status, scores["champion"])
            mutation = {"evolve_trace": True} if status.get("evolve_trace", False) else {}
            offspring = evolution.breed_population(population, scores, seed=status["seed"], generation=generation,
                                                  **mutation)
            record = {"generation": generation, "stage": stage, "label": evolution.STAGES[stage]["label"],
                      "champion_id": scores["champion"]["id"], "fingerprint": scores["champion"]["fingerprint"],
                      "bank_seed": scores["bank_seed"], "fitness": scores["fitness"], "candidates": scores["candidates"],
                      "training": scores["summary"], "review": review}
            with db:
                db.execute("BEGIN IMMEDIATE")
                committed, _ = _load(db)
                if committed["state"] != "ready":
                    return committed
                if committed["generation"] != generation - 1:
                    raise RuntimeError("Campaign checkpoint changed during the current generation.")
                status["updated_at"] = _now()
                db.execute("INSERT INTO rounds VALUES(?,?,?)", (generation, _pack(population), _encode(record)))
                db.execute("UPDATE tip SET status=?,population=? WHERE id=1", (_encode(status), _pack(offspring)))
            population = offspring
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for action in ("init", "fork", "run", "status", "pause", "resume"):
        sub = commands.add_parser(action)
        sub.add_argument("--run", required=True, type=Path, help="Dedicated campaign directory")
        if action == "init":
            sources = sub.add_mutually_exclusive_group(required=True)
            sources.add_argument("--archive", type=Path, help="Closed lineage SQLite archive")
            sources.add_argument("--export", type=Path, help="Portable ZIP from a running or closed laboratory")
            sub.add_argument("--branch", default="main")
        if action == "fork":
            sub.add_argument("--parent", required=True, type=Path, help="Existing campaign directory")
            sub.add_argument("--stage", type=int, required=True, choices=range(1, len(evolution.STAGES) + 1),
                             help="Starting challenge (numbered from 1)")
            sub.add_argument("--evolve-trace", action="store_true",
                             help="Allow trace retention to mutate in rule-enabled offspring")
        if action in ("init", "fork"):
            sub.add_argument("--generation", required=True, type=int)
            sub.add_argument("--name", default="Delayed feedback", required=action == "fork")
            sub.add_argument("--seed", type=int, default=20261126, required=action == "fork")
        if action == "run":
            sub.add_argument("--through", type=int, required=True, help="Absolute campaign round to finish")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if args.archive:
                if args.archive.resolve() == (args.run / "campaign.sqlite3").resolve():
                    raise ValueError("Choose a campaign directory separate from the lineage archive.")
                checkpoint, source = archived_cohort(args.archive, args.branch, args.generation)
            else:
                from .memory_experiment import exported_cohort
                checkpoint, source = exported_cohort(args.export, branch=args.branch, generation=args.generation)
            status = init_campaign(args.run, checkpoint, source, name=args.name, seed=args.seed)
        elif args.command == "run":
            status = run_campaign(args.run, args.through)
        elif args.command == "fork":
            status = fork_campaign(args.run, args.parent, args.generation,
                                   stage=args.stage - 1, name=args.name, seed=args.seed, evolve_trace=args.evolve_trace)
        elif args.command == "pause":
            status = pause_campaign(args.run)
        elif args.command == "resume":
            status = resume_campaign(args.run)
        else:
            status = read_status(args.run)
    except (ValueError, OSError, sqlite3.Error, BadZipFile, KeyError, zlib.error, RuntimeError) as error:
        parser.exit(1, f"Campaign error: {error}\n")
    print(json.dumps(status, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
