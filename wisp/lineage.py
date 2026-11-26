"""Local archive of immutable evaluated generations and resumable lineage tips."""

import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4
import zlib


_SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT REFERENCES branches(id),
    fork_generation INTEGER,
    completed_generation INTEGER NOT NULL,
    tip BLOB
);
CREATE TABLE IF NOT EXISTS generations (
    branch_id TEXT NOT NULL REFERENCES branches(id),
    generation INTEGER NOT NULL,
    checkpoint BLOB NOT NULL,
    summary TEXT NOT NULL,
    PRIMARY KEY(branch_id, generation)
);
CREATE TABLE IF NOT EXISTS workspace (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    active_id TEXT NOT NULL REFERENCES branches(id)
);
"""

# A child owns generations strictly after its fork. The running minimum protects
# nested forks made at an earlier ancestor than their parent's own fork point.
_ANCESTRY = """
WITH RECURSIVE lineage(id, ceiling) AS (
    SELECT id, ? FROM branches WHERE id = ?
    UNION ALL
    SELECT b.parent_id, min(l.ceiling, b.fork_generation)
    FROM lineage AS l JOIN branches AS b ON b.id = l.id
    WHERE b.parent_id IS NOT NULL
)
"""


def _json(data):
    if not isinstance(data, dict):
        raise ValueError("Expected an object for the generation checkpoint or summary.")
    return json.dumps(data, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _pack(snapshot):
    return zlib.compress(_json(snapshot).encode("utf-8"), level=1)


def _unpack(payload):
    try:
        data = json.loads(zlib.decompress(payload))
    except (zlib.error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ValueError("This generation checkpoint could not be decoded from the lineage archive.") from exc
    if not isinstance(data, dict):
        raise ValueError("This generation checkpoint must contain an object.")
    return data


def _integer(value, name, maximum=(2**63 - 1)):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be a positive integer up to {maximum}.")


class LineageArchive:
    """One laboratory process owns a file; its HTTP and clock threads share it."""

    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(path, timeout=0.35, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA locking_mode=EXCLUSIVE")
            self._db.execute("BEGIN EXCLUSIVE")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported Wisp lineage archive version {version}.")
            self._db.executescript(_SCHEMA)
            self._db.execute("PRAGMA user_version=1")
            self._db.commit()
        except sqlite3.OperationalError as exc:
            self._db.close()
            if "locked" in str(exc).lower():
                raise ValueError("This lineage archive is open in another laboratory. "
                                 "Close that room or choose a separate save path.") from exc
            raise
        except BaseException:
            self._db.close()
            raise

    def close(self):
        with self._lock:
            self._db.close()

    def backup(self, destination, branch_id, live_tip):
        """Copy the open database consistently, then seal the current trial in the copy."""
        with self._lock:
            if branch_id != self._active_id():
                raise ValueError("The requested lineage is no longer active.")
            target = sqlite3.connect(destination)
            try:
                self._db.backup(target)
                with target:
                    target.execute("UPDATE branches SET tip=? WHERE id=?", (_pack(live_tip), branch_id))
            finally:
                target.close()

    def _branch(self, branch_id):
        if not isinstance(branch_id, str) or not 1 <= len(branch_id) <= 64:
            raise ValueError("Supply a valid lineage ID.")
        row = self._db.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown lineage.")
        return row

    def _active_id(self):
        active = self._db.execute("SELECT active_id FROM workspace WHERE singleton=1").fetchone()
        if active is None:
            raise ValueError("Initialize the lineage archive before using it.")
        return active["active_id"]

    def _completion(self, branch_id, generation):
        _integer(generation, "Generation")
        self._branch(branch_id)
        row = self._db.execute(_ANCESTRY + """
            SELECT g.checkpoint FROM lineage AS l
            JOIN generations AS g ON g.branch_id = l.id
            WHERE g.generation = ? AND g.generation <= l.ceiling
        """, (generation, branch_id, generation)).fetchone()
        if row is None:
            raise ValueError("That generation has no completed checkpoint on this lineage.")
        return _unpack(row["checkpoint"])

    def _tip(self, branch_id):
        branch = self._branch(branch_id)
        if branch["tip"] is not None:
            return _unpack(branch["tip"])
        return self._completion(branch_id, branch["completed_generation"])

    def start(self, initial_snapshot):
        """Open the current lineage, initializing the first run only once."""
        with self._lock, self._db:
            active = self._db.execute("SELECT active_id FROM workspace WHERE singleton=1").fetchone()
            if active is not None:
                return active["active_id"], self._tip(active["active_id"])
            self._db.execute("INSERT INTO branches VALUES ('main', 'Original lineage', NULL, NULL, 0, ?)",
                             (_pack(initial_snapshot),))
            self._db.execute("INSERT INTO workspace VALUES (1, 'main')")
            return "main", initial_snapshot

    def save_tip(self, branch_id, snapshot):
        """Save live progress independently of the immutable generation records."""
        with self._lock, self._db:
            if branch_id != self._active_id():
                raise ValueError("Only the active lineage can replace its live progress.")
            self._db.execute("UPDATE branches SET tip=? WHERE id=?", (_pack(snapshot), branch_id))

    def record(self, branch_id, generation, completed_snapshot, summary):
        """Seal the evaluated cohort before selection breeds its successor."""
        _integer(generation, "Generation")
        checkpoint = _pack(completed_snapshot)
        report = _json(summary)
        with self._lock, self._db:
            branch = self._branch(branch_id)
            if branch_id != self._active_id():
                raise ValueError("Only the active lineage can record new generations.")
            if generation != branch["completed_generation"] + 1:
                previous = self._db.execute(
                    "SELECT checkpoint,summary FROM generations WHERE branch_id=? AND generation=?",
                    (branch_id, generation)).fetchone()
                if previous and previous["checkpoint"] == checkpoint and previous["summary"] == report:
                    return
                raise ValueError("Generation checkpoints are immutable and must be recorded in order.")
            self._db.execute("INSERT INTO generations VALUES (?, ?, ?, ?)",
                             (branch_id, generation, checkpoint, report))
            # A null tip explicitly references the just-committed checkpoint.
            self._db.execute("UPDATE branches SET completed_generation=?, tip=NULL WHERE id=?",
                             (generation, branch_id))

    def snapshot(self, branch_id, generation):
        with self._lock:
            return self._completion(branch_id, generation)

    def history(self, branch_id, before=None, limit=20):
        """List completed generations, including ancestors, newest first. before is exclusive."""
        _integer(limit, "History page size", maximum=100)
        if before is not None:
            _integer(before, "History boundary")
        with self._lock:
            branch = self._branch(branch_id)
            ceiling = branch["completed_generation"] if before is None else before - 1
            rows = self._db.execute(_ANCESTRY + """
                SELECT g.branch_id, g.generation, g.summary FROM lineage AS l
                JOIN generations AS g ON g.branch_id=l.id AND g.generation <= l.ceiling
                ORDER BY g.generation DESC LIMIT ?
            """, (ceiling, branch_id, limit))
            return [{"branch_id": row["branch_id"], "generation": row["generation"],
                     "summary": json.loads(row["summary"])} for row in rows]

    def branches(self):
        with self._lock:
            active = self._active_id()
            rows = self._db.execute(
                "SELECT id,name,parent_id,fork_generation,completed_generation FROM branches ORDER BY rowid")
            return [dict(row, active=(row["id"] == active)) for row in rows]

    def _save_and_activate(self, target_id, current_tip):
        self._db.execute("UPDATE branches SET tip=? WHERE id=?", (_pack(current_tip), self._active_id()))
        self._db.execute("UPDATE workspace SET active_id=? WHERE singleton=1", (target_id,))

    def switch(self, target_id, source_live_tip, validate_tip=None):
        """Commit the outgoing branch's exact in-progress tip before opening another."""
        with self._lock, self._db:
            if target_id == self._active_id():
                return target_id, source_live_tip
            target_tip = self._tip(target_id)
            if validate_tip is not None:
                validate_tip(target_tip)
            self._save_and_activate(target_id, source_live_tip)
            return target_id, target_tip

    def fork(self, source_id, generation, name, source_live_tip, make_tip):
        """Atomically derive the child's starting tip and preserve the outgoing active tip.

        make_tip receives the read-only historical cohort and the child's stable ID.
        The engine uses the ID for the new lineage's future mutation seed.
        """
        with self._lock, self._db:
            checkpoint = self._completion(source_id, generation)
            if name is not None and (not isinstance(name, str) or len(name.strip()) > 64):
                raise ValueError("Give the branch a name of up to 64 characters.")
            child_id = uuid4().hex
            label = (name or "").strip() or f"Branch {child_id[:6]} from G{generation}"
            tip = make_tip(checkpoint, child_id)
            self._db.execute("INSERT INTO branches VALUES (?, ?, ?, ?, ?, ?)",
                             (child_id, label, source_id, generation, generation, _pack(tip)))
            self._save_and_activate(child_id, source_live_tip)
            return child_id, tip

    def new_run(self, initial_snapshot, source_live_tip):
        """Create a new independent root while preserving the run the user leaves."""
        with self._lock, self._db:
            branch_id = uuid4().hex
            count = self._db.execute("SELECT count(*) FROM branches WHERE parent_id IS NULL").fetchone()[0]
            self._db.execute("INSERT INTO branches VALUES (?, ?, NULL, NULL, 0, ?)",
                             (branch_id, f"Independent run {count + 1}", _pack(initial_snapshot)))
            self._save_and_activate(branch_id, source_live_tip)
            return branch_id, initial_snapshot
