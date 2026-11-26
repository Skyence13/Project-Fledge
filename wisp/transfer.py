"""Transfer a Wisp laboratory between computers using a portable, verified ZIP."""

import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import shutil
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import zlib

from .development import assess
from .lab import LabController
from .lineage import _SCHEMA


PERSONAL = "personal.json"
DATABASE = PERSONAL + ".lineages.sqlite3"
CAMPAIGN_DIR = PERSONAL + ".campaign"
CAMPAIGN = CAMPAIGN_DIR + "/campaign.sqlite3"
COMPARISONS = tuple(f"{CAMPAIGN_DIR}/comparison/{arm}/campaign.sqlite3" for arm in ("legacy", "trace"))
SUPERVISOR = CAMPAIGN_DIR + "/supervisor/state.json"
HOLD = CAMPAIGN_DIR + "/supervisor/paused"
SUMMARY = "reports/summary.json"
GENERATIONS = "reports/generations.csv"
MANIFEST = "manifest.json"
FILES = (PERSONAL, DATABASE, SUMMARY, GENERATIONS)
BYTE_LIMITS = {MANIFEST: 1 << 20, PERSONAL: 1 << 20, DATABASE: 16 << 30,
               SUMMARY: 64 << 20, GENERATIONS: 4 << 30, CAMPAIGN: 4 << 30,
               **dict.fromkeys(COMPARISONS, 4 << 30), SUPERVISOR: 1 << 20, HOLD: 0}
PROTOCOL = "project-fledge-lab"
MEMORY_PROTOCOL = "wisp-delay-retention-v2"
MEMORY_LIMIT = 8 << 20
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


def _data_only(value):
    if isinstance(value, str):
        windows = PureWindowsPath(value)
        if (value.startswith(("/", "\\", "file:")) or windows.drive
                or ".." in windows.parts or "\x00" in value):
            raise ValueError("Memory reports may contain only portable study data.")
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in ("file", "filename", "directory", "path") or key.lower().endswith("_path"):
                raise ValueError("Memory report contains a local file reference.")
            _data_only(item)
    elif isinstance(value, list):
        for item in value:
            _data_only(item)


def _memory_payload(study, branches):
    """Validate provenance and derive all file names locally when restoring a report."""
    if not isinstance(study, dict) or set(study) != {"source", "finished_at", "report"}:
        raise ValueError("Invalid memory study envelope.")
    source = study["source"]
    required = {"branch_id", "branch_name", "origin_branch_id", "generation"}
    if not isinstance(source, dict) or set(source) != required:
        raise ValueError("Invalid memory study source.")
    report = study["report"]
    if (not isinstance(report, dict) or report.get("protocol") != MEMORY_PROTOCOL
            or not all(isinstance(report.get(k), dict) for k in ("source", "settings", "manifest", "baselines", "summary"))
            or not all(isinstance(report.get(k), list) and report[k] for k in ("variants", "metrics"))
            or report["source"].get("generation") != source["generation"]):
        raise ValueError("Unsupported or incomplete memory study results.")
    if len(json.dumps(study, allow_nan=False).encode("utf-8")) > MEMORY_LIMIT:
        raise ValueError("Memory study exceeds the supported transfer size.")
    _data_only(study)
    branch_by_id = {row["id"]: row for row in branches}
    origin = branch_by_id.get(source["branch_id"])
    gen = source["generation"]
    if (origin is None or type(gen) is not int or not 1 <= gen <= origin["completed_generation"]
            or origin["name"] != source["branch_name"]):
        raise ValueError("The memory study refers to an unknown lineage or generation.")
    while origin["parent_id"] is not None and gen <= origin["fork_generation"]:
        origin = branch_by_id[origin["parent_id"]]
    if source["origin_branch_id"] != origin["id"]:
        raise ValueError("The memory study and the archive disagree about the source lineage.")
    stamp = datetime.fromisoformat(study["finished_at"])
    if stamp.tzinfo is None:
        raise ValueError("The memory study completion time must include a time zone.")
    return stamp.astimezone(timezone.utc)


def _reports(root, branch_id, live_tip, memory_study):
    (root / "reports").mkdir()
    with closing(_read_db(root / DATABASE)) as db:
        branches = [dict(r) for r in db.execute(
            "SELECT id,name,parent_id,fork_generation,completed_generation FROM branches ORDER BY rowid")]
        summary = {
            "active_branch_id": branch_id, "branches": branches,
            "development": assess(LabController.restore(live_tip)),
            "generations_csv": ("One row per completed generation visible on each lineage. "
                                "origin_branch_id identifies the lineage that evaluated it. "
                                "Fitness columns are fractions on the 0–1 scale."),
        }
        if memory_study is not None:
            # Accept a service receipt too, but deliberately copy only the data envelope.
            record = {key: memory_study[key] for key in ("source", "finished_at", "report")}
            _memory_payload(record, branches)
            summary["memory_study"] = {"version": 1, **record}
        _write_json(root / SUMMARY, summary)
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


def _backup_campaign(source_path, target_path):
    # Use SQLite's backup API to include committed WAL pages while another process
    # may be scoring the next campaign round. Open the source read-only.
    busy_since = None

    def progress(status, remaining, total):
        nonlocal busy_since
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            if busy_since is None:
                busy_since = time.monotonic()
            if time.monotonic() - busy_since > 5:
                raise ValueError("Campaign checkpoint is busy. Retry the export after the current write.")
        else:
            busy_since = None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(target_path)) as target:
            source.backup(target, pages=256, progress=progress, sleep=.05)
            target.execute("PRAGMA journal_mode=DELETE")


def _campaign_files(root, database):
    def local(name):
        return database.parent / Path(name).relative_to(CAMPAIGN_DIR)

    def snapshot():
        # The cursor is atomically replaced by the supervisor; the two campaign
        # initializations and the global pause marker can change independently.
        sides = [name for name in COMPARISONS if local(name).is_file()]
        notes = {}
        for name in (SUPERVISOR, HOLD):
            try:
                with local(name).open("rb") as handle:
                    notes[name] = handle.read(BYTE_LIMITS[name] + 1)
            except FileNotFoundError:
                continue
            if len(notes[name]) > BYTE_LIMITS[name]:
                raise ValueError("Oversized campaign supervisor data.")
        return sides, notes

    for _ in range(3):
        sides, notes = snapshot()
        with TemporaryDirectory(dir=root) as tmp:
            attempt = Path(tmp)
            _backup_campaign(database, attempt / CAMPAIGN)
            committed = []
            for name in sides:
                _backup_campaign(local(name), attempt / name)
                # A killed first enrollment can leave an empty SQLite file. The
                # supervisor retries that enrollment from the parent's saved round.
                with closing(_read_db(attempt / name)) as db:
                    if (db.execute("PRAGMA user_version").fetchone()[0] != 0
                            or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone() is not None):
                        committed.append(name)
            if (sides, notes) != snapshot():
                continue
            for name, data in notes.items():
                (attempt / name).parent.mkdir(parents=True, exist_ok=True)
                (attempt / name).write_bytes(data)
            (attempt / CAMPAIGN_DIR).rename(root / CAMPAIGN_DIR)
            files = (CAMPAIGN, *committed, *notes)
            _validate_campaign_files(root, files)
            return files
    raise ValueError("The supervisor changed while taking its snapshot. Retry the export.")


def export_lab(archive, branch_id, live_tip, personal_checkpoint, output_dir, *,
               snapshot_ready=None, memory_study=None, campaign_path=None):
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
        files = FILES
        if campaign_path is not None and Path(campaign_path).exists():
            files = (*FILES, *_campaign_files(root, Path(campaign_path)))
        _reports(root, branch_id, live_tip, memory_study)
        metadata = {name: _digest(root / name) for name in files}
        if any(info["size"] > BYTE_LIMITS[name] for name, info in metadata.items()):
            raise ValueError("The laboratory exceeds the size supported by this transfer format.")
        _write_json(root / MANIFEST, {"format": PROTOCOL, "version": 1, "created_utc": stamp.isoformat(),
                                      "active_branch_id": branch_id, "generation": live_tip["generation"],
                                      "cohort_step": live_tip["step"], "source": _source_description(),
                                      "files": metadata})
        temporary = root / filename
        with ZipFile(temporary, "x", ZIP_DEFLATED, compresslevel=1) as package:
            for name in (MANIFEST, *files):
                package.write(root / name, arcname=name)
        result = output_dir / filename
        temporary.rename(result)
    return result


def _manifest(package):
    entries = package.infolist()
    names = {info.filename for info in entries}
    required = {MANIFEST, *FILES}
    if (len(entries) != len(names) or not required <= names <= BYTE_LIMITS.keys()
            or (names != required and CAMPAIGN not in names)):
        raise ValueError("Package members must match the supported laboratory layout.")
    for info in entries:
        kind = stat.S_IFMT(info.external_attr >> 16)
        if (kind not in (0, stat.S_IFREG) or info.is_dir() or info.file_size > BYTE_LIMITS[info.filename]
                or info.flag_bits & 1 or info.orig_filename != info.filename):
            raise ValueError(f"Invalid or oversized package member: {info.filename!r}.")
    manifest = json.loads(package.read(MANIFEST))
    if (not isinstance(manifest, dict) or manifest.get("format") != PROTOCOL
            or type(manifest.get("version")) is not int or manifest["version"] != 1
            or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != names - {MANIFEST}):
        raise ValueError("Unsupported laboratory transfer manifest.")
    for name, info in manifest["files"].items():
        if (not isinstance(info, dict) or type(info.get("size")) is not int
                or info["size"] != package.getinfo(name).file_size
                or not isinstance(info.get("sha256"), str) or len(info["sha256"]) != 64):
            raise ValueError(f"Invalid size or SHA-256 metadata for {name}.")
    return manifest


def _checkpoint_json(blob):
    # Both controller and campaign population checkpoints fit this bound at the maximum population.
    stream = zlib.decompressobj()
    raw = stream.decompress(blob, 8 << 20)
    if not stream.eof or stream.unused_data or stream.unconsumed_tail:
        raise ValueError("Invalid or oversized compressed Wisp population checkpoint.")
    return json.loads(raw)


def _restore_lab(blob):
    return LabController.restore(_checkpoint_json(blob))


def _validate_campaign(path, archive_path):
    from . import campaign
    from . import challenge_evolution as evolution

    schema = "SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"
    with closing(_read_db(path)) as db, closing(sqlite3.connect(":memory:")) as expected:
        for statement in campaign.SCHEMA:
            expected.execute(statement)
        if (db.execute("PRAGMA user_version").fetchone()[0] != 1
                or [tuple(r) for r in db.execute(schema)] != expected.execute(schema).fetchall()):
            raise ValueError("Unexpected SQLite schema in the transferred campaign.")
        if db.execute("PRAGMA integrity_check(1)").fetchone()[0] != "ok":
            raise ValueError("Invalid SQLite integrity in the transferred campaign.")
        tip = db.execute("SELECT status,population FROM tip WHERE id=1").fetchone()
        if tip is None:
            raise ValueError("The transferred campaign has no checkpoint.")
        status = json.loads(tip["status"])
        if (status["version"] != campaign.VERSION or type(status["generation"]) is not int
                or status["generation"] < 0 or type(status["stage"]) is not int
                or not 0 <= status["stage"] < len(evolution.STAGES)
                or status["state"] not in {"ready", "paused", "review", "complete"}
                or any(type(status[k]) is not int or status[k] <= 0
                       for k in ("validation_interval", "required_passes", "stage_budget"))):
            raise ValueError("Invalid checkpoint status in the transferred campaign.")

        def population(blob):
            data = _checkpoint_json(blob)
            parsed = evolution._population(data)
            if sum(map(len, parsed.values())) != status["population_size"]:
                raise ValueError("Population size changed in the transferred campaign.")
            return data

        enrolled = population(tip["population"])
        count = 0
        for row in db.execute("SELECT * FROM rounds ORDER BY generation"):
            count += 1
            report = json.loads(row["report"])
            if row["generation"] != count or report["generation"] != count:
                raise ValueError("Missing or inconsistent rounds in the transferred campaign.")
            evaluated = population(row["population"])
            if count == 1:
                enrolled = evaluated
        if count != status["generation"]:
            raise ValueError("Campaign checkpoint and history disagree about the completed round.")

    source = status["source"]
    fingerprint = campaign._digest(enrolled)
    parent = status.get("parent_campaign")
    expected_enrollment = status.get("enrollment_sha256", source["population_sha256"] if parent is None else None)
    if fingerprint != expected_enrollment:
        raise ValueError("Campaign enrollment fingerprint differs from its saved population.")
    if parent is None:
        if fingerprint != source["population_sha256"]:
            raise ValueError("Campaign source fingerprint differs from its enrolled population.")
    elif (parent["population_sha256"] != fingerprint or type(parent["generation"]) is not int
          or parent["generation"] < 1 or not isinstance(parent["record_sha256"], str)
          or len(parent["record_sha256"]) != 64):
        raise ValueError("Inconsistent parent campaign provenance.")
    checkpoint, lineage = campaign.archived_cohort(archive_path, source["branch_id"],
                                                   source["generation"])
    original = evolution.population_from_checkpoint(checkpoint)
    if (lineage["origin_branch_id"] != source["origin_branch_id"]
            or lineage["branch_name"] != source["branch_name"]
            or campaign._digest(original) != source["population_sha256"]):
        raise ValueError("Campaign source differs from the bundled lineage and generation.")
    return status


def _validate_campaign_files(root, files):
    from . import campaign, campaign_watch

    primary = _validate_campaign(root / CAMPAIGN, root / DATABASE)
    pair = None
    generations = {}
    with closing(_read_db(root / CAMPAIGN)) as db:
        for name in COMPARISONS:
            if name not in files:
                continue
            child = _validate_campaign(root / name, root / DATABASE)
            parent = child.get("parent_campaign")
            if (not isinstance(parent, dict) or parent["name"] != primary["name"] or parent["seed"] != primary["seed"]
                    or child["source"] != primary["source"] or child["evolve_trace"] is not (name == COMPARISONS[1])):
                raise ValueError("Comparison arm does not derive from the bundled primary campaign.")
            row = db.execute("SELECT population,report FROM rounds WHERE generation=?", (parent["generation"],)).fetchone()
            if row is None:
                raise ValueError("Comparison parent generation is missing from the primary campaign.")
            record = json.loads(row["report"])
            if (campaign._digest(_checkpoint_json(row["population"])) != parent["population_sha256"]
                    or campaign._digest(record) != parent["record_sha256"] or record["stage"] != parent["stage"]):
                raise ValueError("Comparison arm does not match the recorded parent round.")
            identity = (parent["generation"], child["initial_stage"], child["seed"])
            if pair is not None and identity != pair:
                raise ValueError("Comparison arms disagree about their parent, challenge, or random seed.")
            pair = identity
            generations[Path(name).parent.name] = child["generation"]
    if SUPERVISOR in files:
        cursor = campaign_watch._load(root / CAMPAIGN_DIR)
        _data_only(cursor)
        if (cursor["generation"] > primary["generation"] or
                (pair is not None and pair != (cursor["generation"], cursor.get("parent_stage"), cursor.get("paired_seed"))) or
                any(arm not in generations or type(item.get("generation")) is not int
                    or not 0 <= item["generation"] <= generations[arm] for arm, item in cursor["arms"].items())):
            raise ValueError("Supervisor cursor does not match its bundled campaign checkpoints.")
        if cursor["phase"] == "followup" or "comparison_result" in cursor or cursor.get("qualified"):
            _validate_comparison_result(root, cursor, generations, primary["stage_budget"])
    elif pair is not None:
        raise ValueError("Comparison arm checkpoints require the supervisor cursor.")


def _validate_comparison_result(root, cursor, generations, stage_budget):
    from . import campaign_watch

    result = cursor.get("comparison_result")
    qualified = cursor.get("qualified")
    arms = campaign_watch.ARMS
    if (not isinstance(result, dict) or type(result.get("generation")) is not int
            or result["generation"] != cursor["stop_round"]
            or not isinstance(qualified, list) or result.get("qualified") != qualified
            or not isinstance(result.get("arms"), dict) or result["arms"].keys() != arms.keys()
            or generations.keys() != arms.keys()):
        raise ValueError("Invalid fixed endpoint in the transferred comparison.")
    endpoint = result["generation"]
    first_stop = stage_budget
    confirmed = []
    for arm in arms:
        brief = result["arms"][arm]
        if brief["generation"] != endpoint or generations[arm] < endpoint:
            raise ValueError("The fixed comparison endpoint must cover the same round in both arms.")
        path = root / CAMPAIGN_DIR / "comparison" / arm / "campaign.sqlite3"
        with closing(_read_db(path)) as db:
            review = None
            for row in db.execute("SELECT report FROM rounds WHERE generation<=? ORDER BY generation", (endpoint,)):
                item = json.loads(row["report"])["review"]
                if item is not None:
                    review = item
                    if item["stage"] == cursor["parent_stage"] and item["decision"] in ("promote", "complete", "review"):
                        first_stop = min(first_stop, item["generation"])
        historical = campaign_watch._brief({**brief, "last_review": review})
        if historical["last_review"] != brief["last_review"]:
            raise ValueError("Frozen comparison evidence differs from its archived review.")
        if review and review["decision"] in ("promote", "complete"):
            if (review["stage"] != cursor["parent_stage"] or not review["confirmation"]
                    or review["confirmation"].get("passed") is not True):
                raise ValueError("A qualifying arm needs confirmation on the matched challenge.")
            confirmed.append(arm)
    if first_stop != endpoint:
        raise ValueError("The frozen endpoint differs from the first matched challenge decision or budget.")
    if qualified != confirmed:
        raise ValueError("Qualified comparison arms differ from their archived confirmations.")


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
    with closing(_read_db(stage / DATABASE)) as db:
        branches = [dict(row) for row in db.execute(
            "SELECT id,name,parent_id,fork_generation,completed_generation FROM branches ORDER BY rowid")]
    if (summary["active_branch_id"] != manifest["active_branch_id"]
            or summary["development"]["generation"] != manifest["generation"]
            or summary["development"]["step"] != manifest["cohort_step"]
            or summary["branches"] != branches):
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
    if "memory_study" in summary:
        raw = summary["memory_study"]
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
            raise ValueError("Unsupported portable memory study envelope.")
        record = {k: v for k, v in raw.items() if k != "version"}
        stamp = _memory_payload(record, branches)
        reports_dir = stage / (PERSONAL + ".next-plan")
        reports_dir.mkdir()
        _write_json(reports_dir / f"study-{stamp:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}.json", record)


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
                for name in manifest["files"]:
                    (stage / name).parent.mkdir(parents=True, exist_ok=True)
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
            if CAMPAIGN in manifest["files"]:
                _validate_campaign_files(stage, manifest["files"])
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
