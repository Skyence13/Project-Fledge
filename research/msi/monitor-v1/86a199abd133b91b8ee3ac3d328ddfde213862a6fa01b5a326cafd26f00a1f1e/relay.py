"""Relay a live, aggregate lab observation to its verified private Site."""

import argparse
from contextlib import closing
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from getpass import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

from aggregate_cache import DIRECTORY, connect, initialize


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TransportFailure(Exception):
    def __init__(self, exc):
        reason = getattr(exc, "reason", exc)
        self.code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
        super().__init__("HTTPS transport unavailable")


def origin(value):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or not url.hostname.isascii()
            or url.username is not None or url.password is not None
            or url.path not in ("", "/") or url.query or url.fragment
            or url.port not in (None, 443)):
        raise ValueError("Invalid site origin")
    return "https://" + url.hostname.lower()


def request(opener, method, endpoint, token, body=None):
    headers = {"OAI-Sites-Authorization": "Bearer " + token,
               "Accept": "application/json", "Cache-Control": "no-cache"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(endpoint, data=body, headers=headers, method=method)
    try:
        with opener.open(req, timeout=15) as result:
            return result.status, result.read(256_001)
    except HTTPError as error:
        with error:
            return error.code, b""
    except (OSError, URLError) as exc:
        raise TransportFailure(exc) from exc


def publish(opener, endpoint, token, verify, directory=DIRECTORY):
    with closing(connect(directory)) as db:
        initialize(db)
        cursor = db.execute("SELECT delivered FROM delivery WHERE origin=?", (endpoint,)).fetchone()
        row = db.execute("SELECT id,body FROM snapshots WHERE id>? ORDER BY id LIMIT 1",
                         (cursor[0] if cursor else 0,)).fetchone()
    if row is None:
        return {"idle": 1}
    sequence, body = row
    encoded = body.encode("utf-8")
    summary = json.loads(encoded)
    try:
        put, _ = request(opener, "PUT", endpoint, token, encoded)
    except TransportFailure as exc:
        return {"put": 0, "transport_error": exc.code, "observed_at": summary["observed_at"]}
    result = {"put": put, "observed_at": summary["observed_at"]}
    if put == 409 or (verify and 200 <= put < 300):
        try:
            status, data = request(opener, "GET", endpoint, token)
        except TransportFailure as exc:
            return dict(result, get=0, verified=0, transport_error=exc.code)
        result["get"] = status
        try:
            matches = status == 200 and json.loads(data) == summary
        except (ValueError, UnicodeError):
            matches = False
        result["verified"] = int(matches)
    if (200 <= put < 300 and not verify) or result.get("verified"):
        with closing(connect(directory)) as db, db:
            db.execute("""INSERT INTO delivery(origin,delivered) VALUES(?,?)
                          ON CONFLICT(origin) DO UPDATE SET delivered=max(delivered,excluded.delivered)""",
                       (endpoint, sequence))
            if verify and 200 <= put < 300 and result.get("verified"):
                db.execute("""CREATE TABLE IF NOT EXISTS relay_readbacks(
                           origin TEXT, snapshot_id INTEGER, observed_at TEXT, verified_at TEXT,
                           PRIMARY KEY(origin, snapshot_id))""")
                db.execute("INSERT OR REPLACE INTO relay_readbacks VALUES(?,?,?,?)",
                           (endpoint, sequence, summary["observed_at"], datetime.now(timezone.utc).isoformat()))
                db.execute("""DELETE FROM relay_readbacks WHERE origin=? AND snapshot_id NOT IN
                           (SELECT snapshot_id FROM relay_readbacks WHERE origin=? ORDER BY snapshot_id DESC LIMIT 2)""",
                           (endpoint, endpoint))
    return result


def emit(result):
    print(json.dumps(result), flush=True)


def mutex():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    handle = create(None, False, "Local\\ProjectFledgePrivateSummaryRelay")
    if not handle or ctypes.get_last_error() == 183:
        return None
    # Holding the handle reserves this name until the process exits.
    return handle


def launch(endpoint_origin, settings):
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--daemon", "--origin", endpoint_origin],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
    with process.stdin:
        process.stdin.write(json.dumps(settings) + "\n")
    with process.stdout:
        line = process.stdout.readline()
    result = json.loads(line) if line else {"put": 0}
    emit(result)
    return 0 if result.get("verified") == 1 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--test", action="store_true")
    modes.add_argument("--start", action="store_true")
    modes.add_argument("--run", action="store_true", help="Remain attached while relaying observations")
    modes.add_argument("--observe", action="store_true", help="Read the most recent remote observation")
    modes.add_argument("--daemon", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--origin", required=True, help="Exact origin returned by the private deployment")
    args = parser.parse_args()
    expected = origin(args.origin)
    if args.daemon:
        settings = json.loads(sys.stdin.readline())
        sys.stdin.close()
    else:
        if not sys.stdin.isatty():
            raise ValueError("Hidden interactive input required")
        settings = json.loads(getpass("Private Site connection JSON (input hidden): "))
    token = settings["token"]
    if origin(settings["url"]) != expected or not isinstance(token, str) or not token.strip():
        raise ValueError("Invalid site credentials")
    if not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Invalid bearer value")
    if args.start:
        return launch(expected, settings)
    if args.daemon or args.run or args.test:
        handle = mutex()
        if handle is None:
            emit({"running": 1})
            return 1
    # Use this environment's configured HTTPS proxy and platform trust store.
    opener = build_opener(NoRedirect())
    endpoint = expected + "/api/summary"
    if args.observe:
        try:
            status, data = request(opener, "GET", endpoint, token)
        except TransportFailure as exc:
            emit({"get": 0, "transport_error": exc.code})
            return 1
        result = {"get": status}
        if status == 200:
            observation = json.loads(data)
            for key in ("observed_at", "heartbeat_at"):
                if observation.get(key):
                    result[key] = datetime.fromisoformat(observation[key]).isoformat()
        emit(result)
        return 0 if status == 200 else 1
    first = True
    announced = False
    prior_observation = None
    delay = 1
    while True:
        try:
            result = publish(opener, endpoint, token, first)
        except (OSError, KeyError, ValueError, RuntimeError):
            result = {"put": 0, "observed_at": datetime.now(timezone.utc).isoformat()}
        if first and not announced and not result.get("idle"):
            emit(dict(result, pid=os.getpid(), executable=sys.executable))
            announced = True
            if args.test:
                return 0 if result.get("verified") == 1 else 1
            if args.daemon:
                sys.stdout.close()
        if args.test and result.get("idle"):
            emit(result)
            return 1
        if result.get("verified") == 1 and 200 <= result.get("put", 0) < 300:
            stamp = result.get("observed_at")
            if stamp:
                observed = datetime.fromisoformat(stamp)
                if prior_observation is not None and observed > prior_observation:
                    first = False
                prior_observation = observed
        elif result.get("put") is not None:
            prior_observation = None
        if result.get("put") in (401, 403) or (result.get("put") == 409 and not result.get("verified")) or 300 <= result.get("put", 0) < 400:
            return 1
        # Drain every recorded transition promptly. Heartbeats are coalesced by the cache.
        if result.get("idle") or result.get("put") in (200, 204):
            delay = 1
            time.sleep(.25)
        else:
            time.sleep(delay)
            delay = min(60, delay * 2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Suppress URLs, header values and response text in diagnostics.
        emit({"put": 0})
        sys.exit(1)
