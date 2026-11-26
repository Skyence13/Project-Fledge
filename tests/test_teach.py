import http.client
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from wisp.core import Wisp, random_genome
from wisp.teach import TeachingHandler, build_server


class TeachingHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / "saved" / "personal.json"
        reporting = patch.object(TeachingHandler, "log_message")
        reporting.start()
        self.addCleanup(reporting.stop)
        self.server = None
        self.addCleanup(self.stop_server)
        self.start_server()

    def start_server(self, resume=None):
        self.server = build_server(port=0, seed=7, save_path=self.path, resume_path=resume)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(2)
            self.server.server_close()
            self.server = None

    def request(self, path, body=None, expected=200):
        host, port = self.server.server_address
        self.assertEqual(host, "127.0.0.1")
        connection = http.client.HTTPConnection(host, port, timeout=3)
        try:
            if body is None:
                connection.request("GET", path)
            else:
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                connection.request("POST", path, payload, {"Content-Type": "application/json"})
            result = connection.getresponse()
            data = json.loads(result.read())
            self.assertEqual(result.status, expected, data)
            if expected == 200 and not path.startswith("/lab/"):
                self.assertEqual(set(data), {"cue", "action", "pending", "trials", "saved"})
            elif expected != 200:
                self.assertIsInstance(data["error"], str)
            return data
        finally:
            connection.close()

    def test_teaching_order_and_load(self):
        self.assertEqual(self.request("/state"), {
            "cue": None, "action": None, "pending": False, "trials": 0, "saved": False,
        })
        self.request("/feedback", {"value": 1}, 409)
        self.request("/load", {}, 404)
        for cue in (True, -1, 2, "1", None):
            self.request("/choice", {"cue": cue}, 400)
        self.request("/choice", b"{", 400)
        self.request("/choice", ["wrong shape"], 400)
        pending = self.request("/choice", {"cue": 1})
        self.assertIn(pending["action"], (0, 1))
        self.assertTrue(pending["pending"])
        self.request("/choice", {"cue": 0}, 409)
        for invalid in (True, 0, 2, "1"):
            self.request("/feedback", {"value": invalid}, 400)
        self.assertEqual(self.request("/state"), pending)
        self.assertTrue(self.request("/save", {})["saved"])
        saved = self.request("/state")
        result = self.request("/feedback", {"value": -1})
        self.assertFalse(result["pending"])
        self.assertEqual(result["trials"], 1)
        self.request("/feedback", {"value": 1}, 409)
        self.assertEqual(self.request("/load", {}), saved)
        self.assertEqual(self.request("/feedback", {"value": -1}), result)

    def test_manual_lab_steps_are_independent_of_personal_teaching(self):
        personal = self.request("/choice", {"cue": 0})
        initial = self.request("/lab/state")
        self.assertEqual(initial, self.request("/lab/state"))
        self.assertEqual(initial["step"], 0)
        selected = self.request("/lab/control", {"action": "select", "id": "rules_only:1"})
        self.assertEqual(selected["selected"], "rules_only:1")
        stepped = self.request("/lab/control", {"action": "step"})
        self.assertEqual(stepped["step"], 1)
        self.assertGreater(stepped["stats"]["total_choices"], 0)
        self.request("/lab/control", {"action": "does_not_exist"}, 400)
        self.assertEqual(self.request("/lab/state"), stepped)
        self.assertEqual(self.request("/state"), personal)
        self.request("/save", {})
        self.assertEqual(self.request("/lab/state"), stepped)

    def test_auto_clock_runs_without_polling_and_pauses(self):
        control = "/lab/control"
        self.request(control, {"action": "calibrate"})
        calibrated = self.wait_for_calibration()
        running = self.request(control, {"action": "configure", "mode": "auto",
                                         "speed": "accelerated", "running": True, "rate": 8})
        self.assertEqual(running["speed"], "accelerated")
        self.assertEqual(running["pacing"]["target_trials_per_second"], min(8, calibrated["cap"]))
        # Wait for the clock itself; HTTP reads must never drive ticks.
        event = threading.Event()
        original_tick = self.server.lab.tick
        def observe_tick():
            original_tick()
            event.set()
        with patch.object(self.server.lab, "tick", side_effect=observe_tick):
            self.assertTrue(event.wait(3), "Accelerated clock did not tick")
        paused = self.request(control, {"action": "configure", "running": False})
        self.assertGreater(paused["step"], 0)
        time.sleep(.18)
        self.assertEqual(self.request("/lab/state"), paused)
        slower = self.request(control, {"action": "configure", "speed": "realtime"})
        self.assertEqual(slower["speed"], "realtime")
        self.assertEqual(slower["step"], paused["step"])
        worker = self.server._worker
        self.stop_server()
        self.assertFalse(worker.is_alive())

    def wait_for_calibration(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            pacing = self.request("/lab/state")["pacing"]
            if pacing["status"] != "measuring":
                self.assertEqual(pacing["status"], "ready", pacing)
                return pacing
            time.sleep(.05)
        self.fail("Hardware calibration did not finish")

    def test_hardware_benchmark_and_population_reset_preserve_experiment(self):
        control = "/lab/control"
        original = self.server.lab.checkpoint()
        personal = self.server.session.wisp.checkpoint()
        state = self.request(control, {"action": "calibrate"})
        self.assertEqual(state["pacing"]["status"], "measuring")
        measured = self.wait_for_calibration()
        self.assertGreater(measured["measured_trials_per_second"], 0)
        self.assertEqual(measured["presets"][0], 1)
        self.assertEqual(measured["presets"][-1], measured["cap"])
        self.assertEqual(self.server.lab.checkpoint(), original)
        self.assertEqual(self.server.session.wisp.checkpoint(), personal)
        self.assertEqual(self.request("/lab/history")["generations"], [])
        clipped = self.request(control, {"action": "configure", "rate": 65536, "speed": "accelerated"})
        self.assertEqual(clipped["pacing"]["effective_trials_per_second"], measured["cap"])
        with self.server._clock:
            self.server._pacer.observe(32, 32., measured["cap"])
        limited = self.request("/lab/state")["pacing"]
        self.assertLessEqual(max(limited["presets"]), limited["live_cap"])
        self.assertEqual(limited["effective_trials_per_second"], limited["live_cap"])
        self.assertEqual(limited["requested_rate"], 65536)
        for value in (True, 0, -1, "16", 65537):
            self.request(control, {"action": "configure", "rate": value}, 400)
        self.assertEqual(self.request(control, {"action": "configure", "speed": "realtime"})["pacing"]
                         ["effective_trials_per_second"], 1)
        for size in (8, 63, 1024, None, True):
            self.request(control, {"action": "reset", "population": size}, 400)
        reset = self.request(control, {"action": "reset", "population": 64})
        self.assertEqual(reset["population"], 64)
        self.assertEqual(reset["pacing"]["status"], "uncalibrated")
        self.assertEqual(reset["pacing"]["cap"], None)
        source = self.request(control, {"action": "switch", "branch": "main"})
        self.assertEqual(source["population"], 16)
        self.assertEqual(source["pacing"]["requested_rate"], 65536)

    def test_switch_discards_an_inflight_probe_and_no_clock_threads_survive_close(self):
        started = threading.Event()
        cancelled = threading.Event()
        def stale_probe(checkpoint, *, cancel_event, archive_dir):
            self.assertEqual(archive_dir, self.path.parent)
            started.set()
            self.assertTrue(cancel_event.wait(3))
            cancelled.set()
            return {"cap": 9999, "presets": [1, 9999], "measured_trials_per_second": 10000}
        with patch("wisp.teach.calibrate", side_effect=stale_probe):
            self.request("/lab/control", {"action": "calibrate"})
            self.assertTrue(started.wait(3))
            self.request("/lab/control", {"action": "reset", "population": 32})
            self.assertTrue(cancelled.wait(3))
        state = self.request("/lab/state")
        self.assertEqual((state["population"], state["pacing"]["cap"]), (32, None))
        benchmark_thread = self.server._benchmark_thread
        self.stop_server()
        self.assertFalse(benchmark_thread.is_alive())

    def test_archive_browsing_fork_switch_and_restart(self):
        control = "/lab/control"
        self.request("/choice", {"cue": 1})
        self.request("/save", {})
        personal = self.path.read_bytes()
        initial = self.request("/lab/state")
        for _ in range(initial["steps_per_generation"] - 1):
            self.server.lab_request({"action": "step"})
        final = self.request(control, {"action": "step"})
        self.assertTrue(final["awaiting_next"])
        history = self.request("/lab/history?branch=main&limit=5")
        self.assertEqual([row["generation"] for row in history["generations"]], [1])
        old = self.request("/lab/generation?branch=main&generation=1")
        self.assertEqual(old["state"]["variants"], final["variants"])
        development = self.request("/lab/development?branch=main&generation=1")
        self.assertEqual(development, self.request("/lab/development"))
        self.assertEqual(development["branch_name"], final["active_branch_name"])
        self.request(control, {"action": "next_generation"})
        live = self.request(control, {"action": "step"})
        self.assertEqual(self.request("/lab/generation?branch=main&generation=1"), old)
        self.assertNotEqual(self.request("/lab/development"), development)
        self.assertEqual(self.request("/lab/development?branch=main&generation=1"), development)
        self.assertEqual(self.request("/lab/state"), live)

        child = self.request(control, {"action": "branch", "branch": "main", "generation": 1, "name": "Compare memory"})
        child_id = child["active_branch_id"]
        self.assertNotEqual(child_id, "main")
        self.assertEqual((child["generation"], child["step"], child["running"]), (2, 0, False))
        generations = self.request("/lab/history?branch=" + child_id)["generations"]
        self.assertEqual(generations[0]["branch_id"], "main")
        self.assertEqual(self.request("/lab/generation?branch=main&generation=1"), old)
        child = self.request(control, {"action": "step"})
        self.assertEqual(self.request(control, {"action": "switch", "branch": "main"}), live)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request("/lab/state"), live)
        self.assertEqual(self.request(control, {"action": "switch", "branch": child_id}), child)
        reset = self.request(control, {"action": "reset"})
        self.assertEqual((reset["generation"], reset["step"]), (1, 0))
        branches = self.request("/lab/branches")["branches"]
        self.assertEqual(len(branches), 3)
        self.assertEqual(self.path.read_bytes(), personal)
        self.request("/lab/generation?generation=1&generation=2", expected=400)
        self.request("/lab/history?branch=missing", expected=400)
        self.request(control, {"action": "branch", "branch": "main", "generation": 500, "name": "missing"}, 400)
        self.assertEqual(self.request("/lab/state"), reset)

    def test_failed_archive_writes_are_retried_before_advancing(self):
        control = "/lab/control"
        self.request(control, {"action": "step"})
        self.request(control, {"action": "configure", "mode": "auto", "running": True})
        with patch.object(self.server.archive, "save_tip", side_effect=sqlite3.OperationalError("disk full")):
            self.request(control, {"action": "configure", "running": False}, 503)
        self.assertIsNotNone(self.request("/lab/state")["archive_error"])
        recovered = self.request(control, {"action": "configure", "running": False, "mode": "manual"})
        self.assertIsNone(recovered["archive_error"])
        _, durable = self.server.archive.start({})
        self.assertEqual(durable["step"], 1)
        for _ in range(recovered["steps_per_generation"] - 2):
            self.server.lab_request({"action": "step"})
        with patch.object(self.server.archive, "record", side_effect=sqlite3.OperationalError("disk full")):
            self.request(control, {"action": "step"}, 503)
            self.request(control, {"action": "next_generation"}, 503)
            self.assertEqual(self.request("/lab/history")["generations"], [])
        next_state = self.request(control, {"action": "next_generation"})
        self.assertEqual((next_state["generation"], next_state["step"]), (2, 0))
        self.assertEqual(self.request("/lab/history")["generations"][0]["generation"], 1)

    def test_restart_with_pending_feedback_and_exact_continuation(self):
        self.request("/choice", {"cue": 0})
        self.request("/feedback", {"value": 1})
        self.request("/choice", {"cue": 1})
        checkpoint_state = self.request("/save", {})
        future = []
        for cue in (0, 1, 1, 0, 0, 1):
            self.request("/feedback", {"value": 1})
            future.append(self.request("/choice", {"cue": cue}))
        expected_brain = self.server.session.wisp.checkpoint()
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request("/state"), checkpoint_state)
        for state in future:
            self.request("/feedback", {"value": 1})
            self.assertEqual(self.request("/choice", {"cue": state["cue"]}), state)
        self.assertEqual(self.server.session.wisp.checkpoint(), expected_brain)

    def test_resume_raw_individual_checkpoint(self):
        champion_path = self.folder / "champion.json"
        original = Wisp(random_genome(random.Random(21)), seed=21)
        for cue in (0, 1, 0):
            original.choose(cue)
            original.feedback(1)
        champion_path.write_text(json.dumps(original.checkpoint()), encoding="utf-8")
        self.stop_server()
        self.start_server(resume=champion_path)
        state = self.request("/state")
        self.assertFalse(state["saved"])
        self.assertFalse(state["pending"])
        self.assertEqual(state["trials"], 3)
        self.assertEqual(self.request("/choice", {"cue": 1})["action"], original.choose(1))
        self.request("/feedback", {"value": -1})
        original.feedback(-1)
        self.request("/save", {})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), original.checkpoint())
        self.assertNotEqual(champion_path.read_bytes(), self.path.read_bytes())

    def test_failed_save_preserves_file_and_failed_load_preserves_session(self):
        self.request("/save", {})
        saved = self.path.read_bytes()
        pending = self.request("/choice", {"cue": 1})
        with patch("wisp.teach.os.replace", side_effect=OSError("read only")):
            self.request("/save", {}, 500)
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(list(self.path.parent.glob(".personal.json.*.tmp")), [])
        for corrupt in ("truncated{", "{}", '{"pending": false}'):
            self.path.write_text(corrupt, encoding="utf-8")
            self.request("/load", {}, 422)
            self.assertEqual(self.request("/state"), pending)


if __name__ == "__main__":
    unittest.main()
