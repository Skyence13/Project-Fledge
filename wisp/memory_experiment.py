"""Run the delayed-feedback and retention experiment on a saved Wisp generation."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile
import zlib

from .lab import LabController
from .lineage import _SCHEMA
from .next_plan import run_next_plan
from .teach import write_checkpoint
from .transfer import DATABASE, _manifest, _read_db, _restore_lab, _source_description


def _verified_database(package, destination):
    """Stream only the database to disk, checking all four manifest digests."""
    manifest = _manifest(package)
    for name, expected in manifest["files"].items():
        digest = hashlib.sha256()
        with package.open(name) as source:
            if name == DATABASE:
                with destination.open("xb") as target:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                        target.write(block)
            else:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        if digest.hexdigest() != expected["sha256"]:
            raise ValueError(f"Checksum mismatch for {name}.")
    return manifest


def _cohort(db, manifest, branch_id, generation):
    # Validate the exact v1 table definitions before querying the exported database.
    schema = "SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"
    with closing(sqlite3.connect(":memory:")) as reference:
        reference.executescript(_SCHEMA)
        if (db.execute("PRAGMA user_version").fetchone()[0] != 1
                or list(map(tuple, db.execute(schema))) != reference.execute(schema).fetchall()):
            raise ValueError("Unsupported lineage archive schema.")
    active = db.execute("SELECT active_id FROM workspace WHERE singleton=1").fetchone()
    if active is None or active["active_id"] != manifest.get("active_branch_id"):
        raise ValueError("Active lineage differs from the transfer manifest.")
    branch_id = active["active_id"] if branch_id is None else branch_id
    branches = db.execute(
        "SELECT id,name,parent_id,fork_generation,completed_generation FROM branches"
    )
    lineages = {row["id"]: row for row in branches}
    if branch_id not in lineages:
        raise ValueError(f"Unknown exported lineage: {branch_id}.")
    selected = origin = lineages[branch_id]
    completed = selected["completed_generation"]
    generation = completed if generation is None else generation
    if completed == 0:
        raise ValueError("The selected lineage has no completed generation yet.")
    if type(generation) is not int or not 1 <= generation <= completed:
        raise ValueError(f"Choose a completed generation from 1 through {completed}.")

    seen = set()
    while origin["parent_id"] is not None and generation <= origin["fork_generation"]:
        if origin["id"] in seen or origin["parent_id"] not in lineages:
            raise ValueError("Invalid branch ancestry in the transfer.")
        seen.add(origin["id"])
        origin = lineages[origin["parent_id"]]
    row = db.execute(
        "SELECT checkpoint,summary FROM generations WHERE branch_id=? AND generation=?",
        (origin["id"], generation),
    ).fetchone()
    if row is None:
        raise ValueError("The selected generation is missing from its lineage.")
    lab = _restore_lab(row["checkpoint"])
    if (not lab.awaiting_next or lab.generation != generation or not lab.history
            or lab.history[-1] != json.loads(row["summary"])):
        raise ValueError("The selected cohort is incomplete or its summary is inconsistent.")
    return lab.checkpoint(), {
        "kind": "transfer", "branch_id": branch_id, "branch_name": selected["name"],
        "generation": generation, "origin_branch_id": origin["id"],
        "latest_completed_at_export": completed,
        "database_sha256": manifest["files"][DATABASE]["sha256"],
        "exporter_source": manifest.get("source", {}),
    }


def exported_cohort(path, *, branch=None, generation=None):
    """Read a verified export without opening a live archive or restoring every generation."""
    with TemporaryDirectory(prefix="fledge-evaluation-") as temporary:
        db_path = Path(temporary) / DATABASE
        with ZipFile(path) as package:
            manifest = _verified_database(package, db_path)
        with closing(_read_db(db_path)) as db:
            checkpoint, source = _cohort(db, manifest, branch, generation)
    source["filename"] = Path(path).name
    return checkpoint, source


def founder_cohort(seed, population):
    """Evaluate the founders on the original schedule so enrollment matches archived G1."""
    if type(population) is not int or population not in (16, 32, 64):
        raise ValueError("Choose a founder population of 16, 32, or 64.")
    lab = LabController(seed=seed, per_condition=population // 4)
    for _ in lab.schedule:
        lab.command({"action": "step"})
    return lab.checkpoint(), {
        "kind": "founders", "founder_seed": seed, "population": population, "generation": 1,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--export", type=Path, help="Exported Fledge laboratory ZIP")
    source_group.add_argument("--founder-seed", type=int, help="Generate a reproducible founder cohort")
    parser.add_argument("--branch", help="Exported lineage ID (default: active lineage)")
    parser.add_argument("--generation", type=int, help="Archived generation (default: latest completed)")
    parser.add_argument("--population", type=int, choices=(16, 32, 64),
                        help="Total founder Wisps (default: 16)")
    parser.add_argument("--seed", type=int, default=41, help="Held-out lesson seed (default: 41)")
    parser.add_argument("--repeats", type=int, choices=range(1, 9), default=2,
                        help="Counterbalanced blocks per candidate (default: 2)")
    parser.add_argument("--member", action="append", help="Candidate ID, e.g. fixed:0; repeat to select more")
    parser.add_argument("--output", type=Path, default=Path("runs/next-plan.json"), help="Result JSON file")
    args = parser.parse_args(argv)
    if args.export is not None and args.population is not None:
        parser.error("--population applies only to --founder-seed")
    if args.export is None and (args.branch is not None or args.generation is not None):
        parser.error("--branch and --generation apply only to --export")
    if args.export is not None and args.export.resolve() == args.output.resolve():
        parser.error("Choose a report path separate from the input ZIP")

    try:
        if args.export is not None:
            checkpoint, source = exported_cohort(args.export, branch=args.branch, generation=args.generation)
        else:
            checkpoint, source = founder_cohort(args.founder_seed, args.population or 16)
        result = run_next_plan(checkpoint, seed=args.seed, repeats=args.repeats, member_ids=args.member)
        envelope = {"source": source, "report": result, "evaluator_source": _source_description(),
                    "finished_at": datetime.now(timezone.utc).isoformat()}
        write_checkpoint(args.output, envelope)
    except (ValueError, OSError, BadZipFile, sqlite3.Error, zlib.error, RuntimeError, KeyError, TypeError,
            IndexError, AttributeError, OverflowError, RecursionError) as error:
        parser.exit(1, f"Could not run memory experiment: {error}\n")
    print(f"Completed the memory experiment on generation {source['generation']}. Report: {args.output}")


if __name__ == "__main__":
    main()
