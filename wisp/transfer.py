"""Transfer a Wisp laboratory between computers using a portable, verified ZIP."""

import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import zlib

from .development import assess
from .lab import LabController
from .lineage import _SCHEMA


PERSONAL = "personal.json"
DATABASE = PERSONAL + ".lineages.sqlite3"
SUMMARY = "reports/summary.json"
GENERATIONS = "reports/generations.csv"
MANIFEST = "manifest.json"
FILES = (PERSONAL, DATABASE, SUMMARY, GENERATIONS)
BYTE_LIMITS = {MANIFEST: 1 << 20, PERSONAL: 1 << 20, DATABASE: 16 << 30,
               SUMMARY: 64 << 20, GENERATIONS: 4 << 30}
PROTOCOL = "project-fledge-lab"
METRIC_COLUMNS = ["branch_id", "origin_branch_id", "parent_id", "fork_generation", "generation",
                  "mean_fitness_0_to_1", "best_fitness_0_to_1", "unique_genomes",
                  "manual_overrides", "fixed_mean_0_to_1", "rules_only_mean_0_to_1",
                  "wiring_only_mean_0_to_1", "rules_and_wiring_mean_0_to_1"]


def _write_json(path, data):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, allow_nan=False, indent=2)
        handle.write("\n")


def _digest(path):
    with path.open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def _source_description():
    modules = {}
    for module in sorted(Path(__file__).resolve().parent.glob("*.py")):
        text = module.read_bytes().replace(b"\r\n", b"\n")
        modules["wisp/" + module.name] = hashlib.sha256(text).hexdigest()
    return {"repository": "https://github.com/Skyence13/Project-Fledge",
            "matching_source": "Use the same Project Fledge revision that produced this export.",
            "module_sha256_lf": modules}


def _read_db(path):
    # immutable=1 ignores foreign journals and never writes to the imported database.
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA query_only=ON")
    db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 32 << 20)
    return db


def _reports(root, branch_id, live_tip):
    (root / "reports").mkdir()
    with closing(_read_db(root / DATABASE)) as db:
        branches = [dict(r) for r in db.execute(
            "SELECT id,name,parent_id,fork_generation,completed_generation FROM branches ORDER BY rowid")]
        _write_json(root / SUMMARY, {
            "active_branch_id": branch_id, "branches": branches,
            "development": assess(LabController.restore(live_tip)),
            "generations_csv": ("One row per completed generation visible on each lineage. "
                                "origin_branch_id identifies the lineage that evaluated it. "
                                "Fitness columns are fractions on the 0–1 scale."),
        })
        with (root / GENERATIONS).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(METRIC_COLUMNS)
            for branch in branches:
                rows = db.execute("""
                    WITH RECURSIVE path(id, ceiling) AS (
                        SELECT ?, ?
                        UNION ALL
                        SELECT b.parent_id, min(p.ceiling,b.fork_generation)
                        FROM path AS p JOIN branches AS b ON b.id=p.id WHERE b.parent_id IS NOT NULL
                    )
                    SELECT g.branch_id,g.generation,g.summary FROM path AS p JOIN generations AS g
                    ON g.branch_id=p.id AND g.generation<=p.ceiling ORDER BY g.generation
                """, (branch["id"], branch["completed_generation"]))
                for row in rows:
                    s = json.loads(row["summary"])
                    conditions = s.get("by_condition", {})
                    writer.writerow([branch["id"], row["branch_id"], branch["parent_id"],
                                     branch["fork_generation"], row["generation"], s.get("mean_fitness"),
                                     s.get("best_fitness"), s.get("unique_genomes"), s.get("manual_overrides"),
                                     *(conditions.get(k) for k in ("fixed", "rules_only",
                                                                   "wiring_only", "rules_and_wiring"))])


def export_lab(archive, branch_id, live_tip, personal_checkpoint, output_dir, *, snapshot_ready=None):
    """Capture under caller's service locks; release them after the backup via snapshot_ready."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    filename = f"fledge-lab-{stamp:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}.zip"
    with TemporaryDirectory(dir=output_dir, prefix=".fledge-export-") as tmp:
        root = Path(tmp)
        _write_json(root / PERSONAL, personal_checkpoint)
        archive.backup(root / DATABASE, branch_id, live_tip)
        if snapshot_ready is not None:
            snapshot_ready()
        _reports(root, branch_id, live_tip)
        metadata = {name: _digest(root / name) for name in FILES}
        if any(info["size"] > BYTE_LIMITS[name] for name, info in metadata.items()):
            raise ValueError("The laboratory exceeds the size supported by this transfer format.")
        _write_json(root / MANIFEST, {"format": PROTOCOL, "version": 1, "created_utc": stamp.isoformat(),
                                      "active_branch_id": branch_id, "generation": live_tip["generation"],
                                      "cohort_step": live_tip["step"], "source": _source_description(),
                                      "files": metadata})
        temporary = root / filename
        with ZipFile(temporary, "x", ZIP_DEFLATED, compresslevel=1) as package:
            for name in (MANIFEST, *FILES):
                package.write(root / name, arcname=name)
        result = output_dir / filename
        temporary.rename(result)
    return result


def _manifest(package):
    entries = package.infolist()
    if len(entries) != len(BYTE_LIMITS) or {info.filename for info in entries} != BYTE_LIMITS.keys():
        raise ValueError("Package must contain exactly the five supported laboratory files.")
    for info in entries:
        kind = stat.S_IFMT(info.external_attr >> 16)
        if (kind not in (0, stat.S_IFREG) or info.is_dir() or info.file_size > BYTE_LIMITS[info.filename]
                or info.flag_bits & 1 or info.orig_filename != info.filename):
            raise ValueError(f"Invalid or oversized package member: {info.filename!r}.")
    manifest = json.loads(package.read(MANIFEST))
    if (not isinstance(manifest, dict) or manifest.get("format") != PROTOCOL
            or type(manifest.get("version")) is not int or manifest["version"] != 1
            or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(FILES)):
        raise ValueError("Unsupported laboratory transfer manifest.")
    for name, info in manifest["files"].items():
        if (not isinstance(info, dict) or type(info.get("size")) is not int
                or info["size"] != package.getinfo(name).file_size
                or not isinstance(info.get("sha256"), str) or len(info["sha256"]) != 64):
            raise ValueError(f"Invalid size or SHA-256 metadata for {name}.")
    return manifest


def _restore_lab(blob):
    # Published controller checkpoints are well below this bound at the maximum population.
    stream = zlib.decompressobj()
    raw = stream.decompress(blob, 8 << 20)
    if not stream.eof or stream.unused_data or stream.unconsumed_tail:
        raise ValueError("Invalid or oversized compressed Wisp population checkpoint.")
    return LabController.restore(json.loads(raw))


def _validate_database(path, manifest):
    with closing(_read_db(path)) as db, closing(sqlite3.connect(":memory:")) as reference:
        schema_query = "SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"
        reference.executescript(_SCHEMA)
        if (db.execute("PRAGMA user_version").fetchone()[0] != 1
                or [tuple(r) for r in db.execute(schema_query)] != reference.execute(schema_query).fetchall()):
            raise ValueError("Unexpected SQLite schema in the transferred lineage archive.")
        if (db.execute("PRAGMA integrity_check(1)").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchone() is not None):
            raise ValueError("Invalid SQLite integrity or lineage references.")
        workspace = db.execute("SELECT active_id FROM workspace").fetchall()
        if len(workspace) != 1 or manifest.get("active_branch_id") != workspace[0][0]:
            raise ValueError("The package does not identify the active lineage correctly.")
        active = workspace[0][0]
        completed = {}
        for branch in db.execute("SELECT * FROM branches ORDER BY rowid"):
            bid, parent, fork, last = (branch[k] for k in ("id", "parent_id", "fork_generation", "completed_generation"))
            if (not isinstance(bid, str) or not 1 <= len(bid) <= 64 or not bid.isascii() or not bid.isalnum()
                    or not isinstance(branch["name"], str) or not 1 <= len(branch["name"]) <= 64
                    or type(last) is not int or last < 0
                    or (parent is None and fork is not None)
                    or (parent is not None and (parent not in completed or type(fork) is not int
                                                or not 1 <= fork <= min(last, completed[parent])))):
                raise ValueError("Invalid branch ancestry in the transferred laboratory.")
            prior = fork or 0
            latest = None
            for row in db.execute("SELECT generation,checkpoint,summary FROM generations WHERE branch_id=? "
                                  "ORDER BY generation", (bid,)):
                latest = _restore_lab(row["checkpoint"])
                summary = json.loads(row["summary"])
                if (row["generation"] != prior + 1 or latest.generation != row["generation"]
                        or not latest.awaiting_next or summary != latest.history[-1]):
                    raise ValueError("An archived generation has incomplete or inconsistent evidence.")
                prior += 1
            if prior != last:
                raise ValueError("The package is missing completed generations.")
            tip = _restore_lab(branch["tip"]) if branch["tip"] is not None else latest
            if tip is None or tip.completed != last:
                raise ValueError("The current trial and completed generation count do not agree.")
            if bid == active and (tip.generation != manifest.get("generation") or tip.step != manifest.get("cohort_step")):
                raise ValueError("The active generation and trial do not match the manifest.")
            completed[bid] = last
    return completed


def _validate_reports(stage, manifest, completed):
    summary = json.loads((stage / SUMMARY).read_bytes())
    if (summary["active_branch_id"] != manifest["active_branch_id"]
            or summary["development"]["generation"] != manifest["generation"]
            or summary["development"]["step"] != manifest["cohort_step"]
            or {b["id"]: b["completed_generation"] for b in summary["branches"]} != completed):
        raise ValueError("The readable summary does not agree with the lineage archive.")
    # Only expected identifier and numeric cells are allowed in the spreadsheet export.
    with (stage / GENERATIONS).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        if next(reader, None) != METRIC_COLUMNS:
            raise ValueError("Unexpected generation report columns.")
        positions = dict.fromkeys(completed, 0)
        for row in reader:
            if (len(row) != len(METRIC_COLUMNS) or any(x not in completed for x in row[:2])
                    or (row[2] and row[2] not in completed)
                    or any(x and (not x.isascii() or not x.isdecimal()) for x in (row[3], row[4], row[7], row[8]))):
                raise ValueError("Invalid identifiers or counts in the generation report.")
            generation = int(row[4])
            if generation != positions[row[0]] + 1:
                raise ValueError("Invalid generation order in the generation report.")
            positions[row[0]] = generation
            for cell in (row[5], row[6], *row[9:]):
                if cell and (cell[0] not in "0123456789." or not math.isfinite(float(cell))
                             or not 0 <= float(cell) <= 1):
                    raise ValueError("Fitness in the generation report must be a fraction from 0 to 1.")
        if positions != completed:
            raise ValueError("The generation report is missing lineage results.")


def import_lab(package_path, destination):
    """Validate into private staging; publish only into a newly created destination."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new import directory. The destination already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".fledge-import-") as temp:
        stage = Path(temp)
        try:
            with ZipFile(package_path) as package:
                manifest = _manifest(package)
                (stage / "reports").mkdir()
                _write_json(stage / MANIFEST, manifest)
                for name in FILES:
                    with package.open(name) as source, (stage / name).open("xb") as target:
                        shutil.copyfileobj(source, target, length=256 * 1024)
                    if _digest(stage / name) != manifest["files"][name]:
                        raise ValueError(f"Checksum mismatch for {name}.")
            # Use the same personal checkpoint checks as a real launch. The local
            # import avoids a module cycle with the HTTP service's export endpoint.
            from .teach import TeachingSession
            TeachingSession(seed=0, save_path=stage / PERSONAL)
            completed = _validate_database(stage / DATABASE, manifest)
            _validate_reports(stage, manifest, completed)
        except (BadZipFile, sqlite3.Error, zlib.error, csv.Error, KeyError, TypeError, AttributeError,
                IndexError, RecursionError, OverflowError, RuntimeError) as exc:
            raise ValueError(f"Invalid laboratory package ({type(exc).__name__}).") from exc
        # mkdir is the exclusive claim. An empty directory created by a competing
        # import is still someone else's destination and must never be replaced.
        destination.mkdir()
        published = []
        try:
            for path in stage.iterdir():
                target = destination / path.name
                path.rename(target)
                published.append(target)
        except BaseException:
            for target in reversed(published):
                target.rename(stage / target.name)
            destination.rmdir()
            raise
    return destination


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["import"]:
        args.pop(0)
    parser = argparse.ArgumentParser(description="Import a portable Fledge ZIP into a new local laboratory.")
    parser.add_argument("--input", type=Path, required=True, help="Exported laboratory ZIP")
    parser.add_argument("--destination", type=Path, required=True, help="New directory for restored laboratory")
    options = parser.parse_args(args)
    try:
        directory = import_lab(options.input, options.destination)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Could not import laboratory: {exc}\n")
    print(f"Imported laboratory. Personal save: {directory / PERSONAL}")
    print(f'Launch with: python -m wisp.play --save "{directory / PERSONAL}"')


if __name__ == "__main__":
    main()
