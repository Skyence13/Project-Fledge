"""Loopback HTTP service for the teaching room and the live evolution experiment."""

import argparse
from concurrent.futures import CancelledError
from contextlib import ExitStack
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .core import Wisp, random_genome
from .calibration import AdaptivePacer, LIVE_MAX_BATCH, LIVE_MAX_BATCH_SECONDS, LIVE_YIELD_SECONDS, calibrate
from .campaign import read_status
from .development import assess
from .lab import LabController
from .lineage import LineageArchive
from .next_plan import run_next_plan
from .transfer import export_lab


MAX_REQUEST_BYTES = 4096
CLOCK_QUANTUM = .025


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
    def log_request(self, code="-", size="-"):
        # Dashboard polls can run all day. Keep control changes and failed polls in the log.
        if self.command == "GET" and urlsplit(self.path).path in ("/lab/state", "/lab/next-plan", "/lab/campaign") and code == HTTPStatus.OK:
            return
        super().log_request(code, size)

    def reply(self, status, data):
        response = json.dumps(data, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self):
        route = urlsplit(self.path)
        try:
            if route.path == "/state":
                state = self.server.session.request(route.path)
            elif route.path == "/lab/state":
                state = self.server.lab_request()
            elif route.path == "/lab/next-plan" and not route.query:
                state = self.server.next_plan_state()
            elif route.path == "/lab/campaign" and not route.query:
                state = self.server.campaign_state()
            elif route.path in ("/lab/branches", "/lab/history", "/lab/generation", "/lab/development"):
                query = parse_qs(route.query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
                if any(len(values) != 1 for values in query.values()):
                    raise ValueError("Use each query parameter once.")
                state = self.server.lineage_view(route.path, {key: values[0] for key, values in query.items()})
            else:
                raise TeachingError(HTTPStatus.NOT_FOUND, "Unknown endpoint.")
        except ValueError as error:
            self.reply(getattr(error, "status", HTTPStatus.BAD_REQUEST), {"error": str(error)})
            return
        except sqlite3.Error:
            self.reply(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Could not read the lineage archive."})
            return
        self.reply(HTTPStatus.OK, state)

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path not in ("/choice", "/feedback", "/save", "/load", "/lab/control", "/lab/export",
                            "/lab/next-plan/start", "/lab/campaign/pause", "/lab/campaign/resume"):
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
            if path == "/lab/control":
                state = self.server.lab_request(data)
            elif path == "/lab/next-plan/start":
                state = self.server.next_plan_state(data)
            elif path in ("/lab/campaign/pause", "/lab/campaign/resume"):
                if data:
                    raise TeachingError(HTTPStatus.BAD_REQUEST, "Campaign controls accept an empty JSON object.")
                state = self.server.campaign_state(pause=path.endswith("/pause"))
            elif path == "/lab/export":
                if data:
                    raise TeachingError(HTTPStatus.BAD_REQUEST, "Export accepts an empty JSON object.")
                state = self.server.export_run()
            else:
                state = self.server.session.request(path, data)
        except TeachingError as error:
            self.reply(error.status, {"error": str(error)})
            return
        except sqlite3.Error:
            self.reply(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Could not update the lineage archive."})
            return
        except OSError:
            self.reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not read or write the checkpoint file."})
            return
        self.reply(HTTPStatus.OK, state)


class TeachingServer(ThreadingHTTPServer):
    """One personal Wisp and one separately clocked population per server."""

    def __init__(self, port, seed, session):
        self.session = session
        self.seed = seed
        self.archive = None
        self._pending_record = None
        self._pending_tip = None
        self._archive_error = None
        self._clock = threading.Condition()
        self._export_lock = threading.Lock()
        self._study_cancel = threading.Event()
        self._study_thread = None
        self._last_study = None
        self._study_dir = session.save_path.with_name(session.save_path.name + ".next-plan")
        self._campaign_dir = session.save_path.with_name(session.save_path.name + ".campaign")
        self._study = {"status": "idle", "source": None, "progress": 0., "report": None,
                       "path": None, "error": None}
        self._deadline = None
        self._closed = False
        self._benchmark = None
        self._benchmark_cancel = threading.Event()
        self._benchmark_requested = False
        self._benchmark_thread = threading.Thread(target=self._benchmark_loop, name="fledge-calibration", daemon=True)
        self._bench_status = "uncalibrated"
        self._bench_error = None
        self._bench_work = 0
        self._bench_time = None
        self._pacer = None
        self._achieved = None
        self._meter_started = None
        self._meter_trials = 0
        self._worker = threading.Thread(target=self._run_lab, name="fledge-lab", daemon=True)
        super().__init__(("127.0.0.1", port), TeachingHandler)
        try:
            path = session.save_path.with_name(session.save_path.name + ".lineages.sqlite3")
            self.archive = LineageArchive(path)
            start = LabController(seed=seed)
            self.branch_id, tip = self.archive.start(start.checkpoint())
            self.branch_name = self._active_name()
            self.lab = self._restore_lab(tip)
            self.lab.running = False
            self._read_latest_study()
            self._worker.start()
            self._benchmark_thread.start()
        except Exception:
            if self.archive is not None:
                self.archive.close()
            super().server_close()
            raise

    @staticmethod
    def _restore_lab(snapshot):
        try:
            return LabController.restore(snapshot)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("This lineage checkpoint contains invalid population data.") from error

    def campaign_state(self, *, pause=None):
        from .campaign_watch import read_watch_status, set_watch_paused
        try:
            status = read_status(self._campaign_dir)
        except FileNotFoundError:
            if pause is not None:
                raise TeachingError(HTTPStatus.NOT_FOUND, "No evolution campaign is attached to this lab.")
            return {"state": "absent"}
        except (OSError, ValueError, sqlite3.Error) as error:
            raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, "Could not read the evolution campaign.") from error
        try:
            watch = read_watch_status(self._campaign_dir)
            if pause is not None:
                if watch is None:
                    raise TeachingError(HTTPStatus.CONFLICT, "Campaign supervision has not started.")
                watch = set_watch_paused(self._campaign_dir, pause)
        except TeachingError:
            raise
        except (OSError, ValueError, sqlite3.Error) as error:
            raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, "Could not access the campaign supervisor.") from error
        return {**status, "supervision": watch} if watch is not None else status

    def lineage_view(self, path, query):
        with self._clock:
            if self._closed:
                raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, "Laboratory is closing.")
            if path == "/lab/branches" and not query:
                return {"active_branch_id": self.branch_id, "branches": self.archive.branches()}
            branch = query.get("branch", self.branch_id)
            if path == "/lab/development" and not query:
                return {**assess(self.lab), "branch_id": self.branch_id, "branch_name": self.branch_name}
            if path == "/lab/development" and "generation" in query and query.keys() <= {"branch", "generation"}:
                historical = self._restore_lab(self.archive.snapshot(branch, int(query["generation"])))
                name = next(row["name"] for row in self.archive.branches() if row["id"] == branch)
                return {**assess(historical), "branch_id": branch, "branch_name": name}
            if path == "/lab/history" and query.keys() <= {"branch", "before", "limit"}:
                before = int(query["before"]) if "before" in query else None
                rows = self.archive.history(branch, before=before, limit=int(query.get("limit", 20)))
                return {"branch": branch, "generations": rows}
            if path == "/lab/generation" and "generation" in query and query.keys() <= {"branch", "generation"}:
                generation = int(query["generation"])
                historical = self._restore_lab(self.archive.snapshot(branch, generation))
                return {"branch": branch, "generation": generation, "state": historical.state()}
            raise ValueError("Invalid query parameters.")

    def _flush_writes(self):
        if self._pending_record is not None:
            try:
                self.archive.record(*self._pending_record)
            except ValueError as error:
                raise sqlite3.IntegrityError("Generation archive is inconsistent.") from error
            self._pending_record = None
        if self._pending_tip is not None:
            self.archive.save_tip(*self._pending_tip)
            self._pending_tip = None

    def _read_latest_study(self):
        try:
            latest = max(self._study_dir.glob("study-*.json"), default=None)
            if latest is not None:
                with latest.open(encoding="utf-8") as stream:
                    data = json.load(stream)
                if (not isinstance(data, dict) or not isinstance(data.get("source"), dict)
                        or not isinstance(data.get("report"), dict)):
                    raise ValueError("invalid report format")
                self._last_study = data
                self._study.update(status="complete", source=data["source"], report=data["report"],
                                   progress=1., path=str(latest.resolve()))
        except (OSError, ValueError, UnicodeError) as error:
            self._study.update(status="error", error=f"Could not reopen the last evaluation: {error}")

    def next_plan_state(self, selection=None):
        with self._clock:
            if self._closed:
                raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, "Laboratory is closing.")
            latest = self.lab.completed - int(self._pending_record is not None)
            if selection is not None:
                if self._study["status"] == "running":
                    raise TeachingError(HTTPStatus.CONFLICT, "A memory evaluation is already running.")
                if selection.keys() - {"branch", "generation"}:
                    raise TeachingError(HTTPStatus.BAD_REQUEST, "Choose only a lineage and generation.")
                branch = selection.get("branch", self.branch_id)
                try:
                    branches = self.archive.branches()
                    entry = next((row for row in branches if row["id"] == branch), None)
                    if entry is None:
                        raise ValueError("Unknown source lineage.")
                    generation = selection.get("generation", entry["completed_generation"])
                    if "generation" not in selection and generation == 0:
                        raise TeachingError(HTTPStatus.CONFLICT, "Complete a generation before running the memory evaluation.")
                    snapshot = self.archive.snapshot(branch, generation)
                    record = self.archive.history(branch, before=generation + 1, limit=1)[0]
                except TeachingError:
                    raise
                except ValueError as error:
                    raise TeachingError(HTTPStatus.BAD_REQUEST, str(error)) from error
                source = {"branch_id": branch, "branch_name": entry["name"], "generation": generation,
                          "origin_branch_id": record["branch_id"]}
                self._study = {"status": "running", "progress": 0., "source": source,
                               "path": None, "error": None, "report": None}
                self._study_thread = threading.Thread(target=self._evaluate_memory, args=(snapshot, source),
                                                      name="fledge-memory-evaluation", daemon=True)
                self._study_thread.start()
            return {**self._study, "eligible": latest > 0, "latest_completed_generation": latest,
                    "active_branch_id": self.branch_id, "active_branch_name": self.branch_name}

    def _evaluate_memory(self, snapshot, source):
        def progress(done, total):
            with self._clock:
                self._study["progress"] = done / total if total else 0.
        try:
            report = run_next_plan(snapshot, seed=41, repeats=2, progress=progress,
                                   cancel_event=self._study_cancel)
            if self._study_cancel.is_set():
                return
            now = datetime.now(timezone.utc)
            filename = f"study-{now:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}.json"
            path = self._study_dir / filename
            finished = {"source": source, "finished_at": now.isoformat(), "report": report}
            write_checkpoint(path, finished)
        except CancelledError:
            return
        except Exception as error:
            with self._clock:
                self._study.update(status="error", error=f"Memory evaluation failed: {error}")
            return
        with self._clock:
            self._last_study = finished
            self._study.update(status="complete", progress=1., report=report, path=str(path.resolve()))

    def export_run(self):
        if not self._export_lock.acquire(blocking=False):
            raise TeachingError(HTTPStatus.CONFLICT, "A run export is already in progress.")
        try:
            with ExitStack() as held:
                held.enter_context(self._clock)
                if self._closed:
                    raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, "Laboratory is closing.")
                if self._pending_record is not None:
                    raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE,
                                        "Resolve the pending generation archive write before exporting.")
                with self.session.lock:
                    # Both minds are stable at this instant. The personal copy now belongs to the export.
                    personal = self.session.wisp.checkpoint()
                    live_tip = self.lab.checkpoint()
                snapshot = {"branch_id": self.branch_id, "branch_name": self.branch_name,
                            "generation": self.lab.generation, "step": self.lab.step}
                directory = self.session.save_path.parent / "exports"
                package = export_lab(self.archive, self.branch_id, live_tip, personal, directory,
                                     snapshot_ready=held.close, memory_study=self._last_study,
                                     campaign_path=self._campaign_dir / "campaign.sqlite3")
            return {**snapshot, "filename": package.name, "path": str(package.resolve())}
        except TeachingError:
            raise
        except (OSError, ValueError, sqlite3.Error) as error:
            raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, f"Could not export this run: {error}") from error
        finally:
            self._export_lock.release()

    def _archive_completion(self, previous_completed):
        if self.lab.completed > previous_completed:
            self._pending_record = (self.branch_id, self.lab.generation,
                                    self.lab.checkpoint(), self.lab.history[-1])
            self._flush_writes()

    def _change_branch(self, command):
        source_tip = self.lab.checkpoint()
        if (command["action"] == "branch" and {"branch", "generation"} <= command.keys()
                and command.keys() <= {"action", "branch", "generation", "name"}):
            def make_tip(snapshot, child_id):
                return self._restore_lab(snapshot).fork(child_id).checkpoint()
            branch, tip = self.archive.fork(command["branch"], command["generation"], command.get("name"),
                                            source_tip, make_tip)
        elif command["action"] == "switch" and set(command) == {"action", "branch"}:
            branch, tip = self.archive.switch(command["branch"], source_tip, validate_tip=self._restore_lab)
        elif command["action"] == "reset" and command.keys() <= {"action", "population"}:
            population = command.get("population", len(self.lab.members))
            if type(population) is not int or population not in (16, 32, 64):
                raise ValueError("Choose a founder population of 16, 32, or 64.")
            fresh = LabController(seed=self.seed, per_condition=population // 4).checkpoint()
            branch, tip = self.archive.new_run(fresh, source_tip)
        else:
            raise ValueError("Invalid lineage command parameters.")
        self.lab = self._restore_lab(tip)
        self.lab.running = False
        self.branch_id = branch
        self.branch_name = self._active_name()
        self._benchmark_cancel.set()
        self._benchmark_requested = False
        self._benchmark = self._pacer = self._bench_error = self._bench_time = None
        self._bench_status = "uncalibrated"
        self._reset_meter()

    def _active_name(self):
        return next(branch["name"] for branch in self.archive.branches() if branch["id"] == self.branch_id)

    def _reset_meter(self):
        self._achieved = None
        self._meter_trials = 0
        self._meter_started = time.monotonic() if self.lab.running else None

    def _work_size(self):
        return sum(len(m.genome.nodes) + len(m.genome.edges) for m in self.lab.members)

    def _queue_benchmark(self):
        if self._bench_status != "measuring":
            self._bench_status = "measuring"
            self._bench_error = None
            self._benchmark_requested = True
            self._clock.notify_all()

    def _benchmark_loop(self):
        # Only one probe exists per service. Its random state and weights belong to a detached copy.
        while True:
            with self._clock:
                self._clock.wait_for(lambda: self._closed or self._benchmark_requested)
                if self._closed:
                    return
                self._benchmark_requested = False
                self._benchmark_cancel = cancel = threading.Event()
                snapshot = self.lab.checkpoint()
                work = self._work_size()
            try:
                result = calibrate(snapshot, cancel_event=cancel, archive_dir=self.session.save_path.parent)
                pacer = AdaptivePacer(result["cap"])
            except CancelledError:
                continue
            except Exception as error:
                with self._clock:
                    if not cancel.is_set():
                        self._bench_status = "error"
                        self._bench_error = f"Calibration failed: {error}"
                        self._clock.notify_all()
                continue
            with self._clock:
                if not cancel.is_set():
                    self._benchmark, self._pacer = result, pacer
                    self._bench_work, self._bench_time = work, time.monotonic()
                    self._bench_status = "ready"
                    if self.lab.speed == "accelerated":
                        self._deadline = None
                    self._clock.notify_all()

    def _rates(self):
        if self.lab.speed == "realtime" or self._pacer is None:
            return 1, 1
        target = min(self.lab.preferred_rate or self._pacer.cap, self._pacer.cap)
        return target, self._pacer.effective_rate(target)

    def _pacing_state(self):
        result = self._benchmark or {}
        target, effective = self._rates()
        live_cap = self._pacer.capacity if self._pacer is not None else None
        choices = [rate for rate in result.get("presets", [1]) if live_cap is None or rate <= live_cap]
        return {**result, "status": self._bench_status, "error": self._bench_error,
                "presets": choices, "cap": result.get("cap"), "live_cap": live_cap,
                "measured_trials_per_second": result.get("measured_trials_per_second"),
                "target_trials_per_second": target, "effective_trials_per_second": effective,
                "requested_rate": self.lab.preferred_rate, "achieved_trials_per_second": self._achieved,
                "population": len(self.lab.members)}

    def lab_request(self, command=None):
        with self._clock:
            if self._closed:
                raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, "Laboratory is closing.")
            if command is not None:
                previous = (self.lab.running, self.lab.speed, self.lab.preferred_rate)
                completed = self.lab.completed
                try:
                    self._flush_writes()
                    if command.get("action") in ("branch", "switch", "reset"):
                        self._change_branch(command)
                    elif command == {"action": "calibrate"}:
                        self._queue_benchmark()
                    else:
                        self.lab.command(command)
                        if (self.lab.speed == "accelerated" and self._bench_status == "uncalibrated"
                                and command.get("action") == "configure"):
                            self._queue_benchmark()
                        self._archive_completion(completed)
                        if previous[0] and not self.lab.running:
                            self._pending_tip = (self.branch_id, self.lab.checkpoint())
                            self._flush_writes()
                    self._archive_error = None
                except ValueError as error:
                    raise TeachingError(HTTPStatus.BAD_REQUEST, str(error)) from error
                except (sqlite3.Error, OSError) as error:
                    self.lab.running = False
                    self._archive_error = "Lineage write failed. Check archive access and free disk space."
                    raise TeachingError(HTTPStatus.SERVICE_UNAVAILABLE, self._archive_error) from error
                if previous != (self.lab.running, self.lab.speed, self.lab.preferred_rate):
                    self._deadline = None
                    self._reset_meter()
                    self._clock.notify_all()
            return {**self.lab.state(), "active_branch_id": self.branch_id,
                    "active_branch_name": self.branch_name, "archive_error": self._archive_error,
                    "pacing": self._pacing_state()}

    def _run_lab(self):
        with self._clock:
            while not self._closed:
                if not self.lab.running:
                    self._deadline = None
                    self._clock.wait()
                    continue
                target, effective = self._rates()
                interval = 1 / effective
                batch = min(LIVE_MAX_BATCH, max(1, round(effective * CLOCK_QUANTUM)))
                period = batch * interval
                now = time.monotonic()
                if self._deadline is None:
                    self._deadline = now + period
                remaining = self._deadline - now
                if remaining > 0:
                    self._clock.wait(remaining)
                    continue
                processed = 0
                try:
                    for _ in range(batch):
                        completed = self.lab.completed
                        self.lab.tick()
                        processed += 1
                        if self.lab.completed != completed:
                            self._archive_completion(completed)
                            self._check_complexity()
                        if not self.lab.running or time.monotonic() - now >= LIVE_MAX_BATCH_SECONDS:
                            break
                except (sqlite3.Error, OSError):
                    self.lab.running = False
                    self._archive_error = "Lineage write failed. Check archive access and free disk space."
                end = time.monotonic()
                if self.lab.speed == "accelerated" and self._pacer is not None and processed:
                    # Recover normal timer jitter with the next deadline. Only excess lateness
                    # beyond one batch is unrecoverable and counts against capacity.
                    self._pacer.observe(processed, max(end - now, 1e-9), target,
                                        overrun_seconds=max(now - self._deadline - period, 0.0))
                if self.lab.running:
                    if self._meter_started is None:
                        self._meter_started = now
                    self._meter_trials += processed
                    elapsed = end - self._meter_started
                    if elapsed >= .75:
                        self._achieved = round(self._meter_trials / elapsed, 2)
                        self._meter_started, self._meter_trials = end, 0
                else:
                    self._reset_meter()
                # Preserve cadence across short late wakeups, but discard a backlog after a
                # long host suspension. Always release the condition between bounded batches.
                if now - self._deadline > period:
                    self._deadline = now
                next_due = self._deadline + processed * interval
                self._deadline = max(next_due, end + LIVE_YIELD_SECONDS)

    def _check_complexity(self):
        if self._bench_status != "ready" or self.lab.speed != "accelerated":
            return
        since_measurement = time.monotonic() - self._bench_time
        relative_change = abs(self._work_size() - self._bench_work) / max(1, self._bench_work)
        if since_measurement >= 15 and relative_change >= .20:
            self._queue_benchmark()

    def server_close(self):
        with self._clock:
            self._closed = True
            self._benchmark_cancel.set()
            self._study_cancel.set()
            self._clock.notify_all()
        if self._worker.is_alive():
            self._worker.join()
        if self._benchmark_thread.is_alive():
            self._benchmark_thread.join()
        if self._study_thread is not None:
            self._study_thread.join()
        # A desktop launcher may exit while a ZIP is being written by a daemon HTTP thread.
        with self._export_lock:
            pass
        try:
            if self.archive is not None:
                try:
                    self._flush_writes()
                    self.archive.save_tip(self.branch_id, self.lab.checkpoint())
                finally:
                    self.archive.close()
                    self.archive = None
        finally:
            super().server_close()


def build_server(port: int, seed: int, save_path: Path, resume_path: Path | None = None) -> TeachingServer:
    session = TeachingSession(seed, save_path, resume_path)
    try:
        return TeachingServer(port, seed, session)
    except sqlite3.Error as error:
        raise ValueError(f"Could not open the lineage archive: {error}") from error


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
    except (ValueError, OSError, sqlite3.Error, OverflowError) as error:
        parser.exit(1, f"Could not start teaching server: {error}\n")


if __name__ == "__main__":
    main()
