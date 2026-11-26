"""Loopback HTTP interface for teaching one Wisp in the Godot arena."""

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import tempfile
import threading
from urllib.parse import urlsplit

from .core import Wisp, random_genome


MAX_REQUEST_BYTES = 4096


class TeachingError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def is_one_of(value, choices):
    return type(value) is int and value in choices


def write_checkpoint(path, data):
    """Publish a complete snapshot with one same-directory atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class TeachingSession:
    def __init__(self, seed, save_path, resume_path=None):
        self.lock = threading.Lock()
        self.save_path = Path(save_path)
        self.wisp = Wisp(random_genome(random.Random(seed)), seed=seed)
        if resume_path is not None:
            self.restore(Path(resume_path))
        elif self.save_path.is_file():
            self.restore(self.save_path)

    def state(self):
        return {
            "cue": self.wisp.last_cue, "action": self.wisp.last_action,
            "pending": self.wisp.pending, "trials": self.wisp.trials,
            "saved": self.save_path.is_file(),
        }

    def restore(self, path):
        try:
            with path.open(encoding="utf-8") as stream:
                data = json.load(stream)
        except FileNotFoundError as error:
            raise TeachingError(HTTPStatus.NOT_FOUND, "No checkpoint has been saved at this path.") from error
        except (UnicodeError, ValueError) as error:
            raise TeachingError(HTTPStatus.UNPROCESSABLE_ENTITY, "The checkpoint is not readable JSON.") from error
        try:
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            cue, action = data["last_cue"], data["last_action"]
            pending, trials = data["pending"], data["trials"]
            if type(pending) is not bool or type(trials) is not int or trials < 0:
                raise ValueError("invalid trial counters")
            if cue is None or action is None:
                if cue is not None or action is not None or pending or trials != 0:
                    raise ValueError("inconsistent last choice")
            elif not (is_one_of(cue, (0, 1)) and is_one_of(action, (0, 1))):
                raise ValueError("invalid last choice")
            restored = Wisp.restore(data)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError) as error:
            raise TeachingError(HTTPStatus.UNPROCESSABLE_ENTITY, "The checkpoint contains invalid Wisp or teaching data.") from error
        # Change the live session only after the entire file passes validation.
        self.wisp = restored

    def request(self, path, body=None):
        with self.lock:
            if path == "/choice":
                if set(body) != {"cue"} or not is_one_of(body["cue"], (0, 1)):
                    raise TeachingError(HTTPStatus.BAD_REQUEST, "Provide a cue of 0 or 1.")
                if self.wisp.pending:
                    raise TeachingError(HTTPStatus.CONFLICT, "Give feedback on the pending choice first.")
                self.wisp.choose(body["cue"])
            elif path == "/feedback":
                if set(body) != {"value"} or not is_one_of(body["value"], (-1, 1)):
                    raise TeachingError(HTTPStatus.BAD_REQUEST, "Provide feedback of -1 or 1.")
                if not self.wisp.pending:
                    raise TeachingError(HTTPStatus.CONFLICT, "Present a cue before giving feedback.")
                self.wisp.feedback(body["value"])
            elif path in ("/save", "/load"):
                if body:
                    raise TeachingError(HTTPStatus.BAD_REQUEST, "Send an empty JSON object.")
                if path == "/load":
                    self.restore(self.save_path)
                else:
                    write_checkpoint(self.save_path, self.wisp.checkpoint())
            return self.state()


class TeachingHandler(BaseHTTPRequestHandler):
    def reply(self, status, data):
        response = json.dumps(data, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self):
        if urlsplit(self.path).path != "/state":
            self.reply(HTTPStatus.NOT_FOUND, {"error": "Use GET /state to inspect the Wisp."})
            return
        self.reply(HTTPStatus.OK, self.server.session.request("/state"))

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path not in ("/choice", "/feedback", "/save", "/load"):
                raise TeachingError(HTTPStatus.NOT_FOUND, "Unknown teaching endpoint.")
            if self.headers.get_content_type() != "application/json":
                raise TeachingError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send Content-Type: application/json.")
            try:
                size = int(self.headers.get("Content-Length", ""))
            except ValueError as error:
                raise TeachingError(HTTPStatus.BAD_REQUEST, "Provide a valid Content-Length.") from error
            if size < 0 or size > MAX_REQUEST_BYTES:
                raise TeachingError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Teaching requests must fit within 4096 bytes.")
            try:
                data = json.loads(self.rfile.read(size).decode("utf-8"))
            except (UnicodeError, ValueError) as error:
                raise TeachingError(HTTPStatus.BAD_REQUEST, "Provide a valid UTF-8 JSON object.") from error
            if not isinstance(data, dict):
                raise TeachingError(HTTPStatus.BAD_REQUEST, "Provide a JSON object.")
            state = self.server.session.request(path, data)
        except TeachingError as error:
            self.reply(error.status, {"error": str(error)})
            return
        except OSError:
            self.reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not read or write the checkpoint file."})
            return
        self.reply(HTTPStatus.OK, state)


def build_server(port: int, seed: int, save_path: Path, resume_path: Path | None = None) -> ThreadingHTTPServer:
    session = TeachingSession(seed, save_path, resume_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), TeachingHandler)
    server.session = session
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7, help="Seed used when starting a fresh Wisp.")
    parser.add_argument("--port", type=int, default=8765, help="Loopback teaching port (default 8765).")
    parser.add_argument("--save", type=Path, default=Path("runs/personal.json"),
                        help="Teaching save file. An existing file resumes automatically at startup.")
    parser.add_argument("--resume", type=Path, help="Start from a teaching save or individual experiment checkpoint.")
    args = parser.parse_args()
    try:
        with build_server(args.port, args.seed, args.save, args.resume) as server:
            host, port = server.server_address
            print(f"Wisp teaching server at http://{host}:{port} (Ctrl+C to stop)", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (TeachingError, OSError, OverflowError) as error:
        parser.exit(1, f"Could not start teaching server: {error}\n")


if __name__ == "__main__":
    main()
