"""Small local transition journal shared by the guarded worker and MSI monitor."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys


DIRECTORY = Path(__file__).resolve().parent / "fledge-monitor-runtime"
PHASES = {"preparing_study", "running_validation", "running_confirmation", "reviewing_results",
          "preparing_successor", "evolving", "review_complete", "unscored", "paused", "blocked"}


def ensure_monitor():
    """MSI coordinator startup/recovery hook. The port bind enforces one publisher."""
    try:
        with socket.create_connection(("127.0.0.1", 8766), timeout=.5):
            return
    except (ConnectionRefusedError, TimeoutError):
        pass
    executable = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(executable), "-B", str(DIRECTORY.parent / "fledge-monitor" / "local_monitor.py")],
                     creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connect(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "monitor.sqlite3", timeout=8)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS events
               (id INTEGER PRIMARY KEY, phase TEXT, study TEXT, objective TEXT,
                completed INTEGER, total INTEGER, observed_at TEXT NOT NULL)""")
    return db


def emit(phase, study, objective, completed, total, *, directory=DIRECTORY):
    if phase not in PHASES:
        raise ValueError("Unknown monitor phase")
    if any(not isinstance(s, str) or not s.strip() or len(s) > limit
           for s, limit in ((study, 160), (objective, 700))):
        raise ValueError("Expected a study name and short objective")
    if (completed, total) != (None, None) and not (
            type(completed) is int and type(total) is int and 0 <= completed <= total and total > 0):
        raise ValueError("Expected completed and total work units, or two absent counts")
    fields = phase, study, objective, completed, total
    with closing(connect(directory)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        last = db.execute("SELECT phase,study,objective,completed,total FROM events ORDER BY id DESC LIMIT 1").fetchone()
        if last and tuple(last) == fields:
            return
        db.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?)",
                   (*fields, datetime.now(timezone.utc).isoformat()))
