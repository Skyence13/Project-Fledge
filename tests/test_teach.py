from concurrent.futures import CancelledError, ThreadPoolExecutor
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
from zipfile import ZipFile

from wisp.core import Wisp, random_genome
from wisp.lineage import LineageArchive
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

    def test_campaign_status_is_read_only_and_uses_this_lab_save(self):
        directory = self.path.with_name("personal.json.campaign")
        population = self.server.lab.checkpoint()
        self.assertEqual(self.request("/lab/campaign"), {"state": "absent"})
        self.assertFalse(directory.exists())
        sample = {"state": "ready", "stage_label": "One intervening choice", "generation": 5}
        with patch("wisp.teach.read_status", return_value=sample) as reader:
            self.assertEqual(self.request("/lab/campaign"), sample)
            reader.assert_called_once_with(directory)
            self.request("/lab/campaign/pause", {}, 409)
            watch = {"phase": "comparison", "paused": False, "arms": {}}
            with patch("wisp.campaign_watch.read_watch_status", return_value=watch), \
                    patch("wisp.campaign_watch.set_watch_paused", return_value={**watch, "paused": True}) as toggle:
                self.assertEqual(self.request("/lab/campaign")["supervision"], watch)
                toggle.assert_not_called()
                self.assertTrue(self.request("/lab/campaign/pause", {})["supervision"]["paused"])
                toggle.assert_called_once_with(directory, True)
                self.request("/lab/campaign/resume", {"ignored": True}, 400)
                self.request("/lab/campaign/resume", {})
                toggle.assert_called_with(directory, False)
        with patch("wisp.teach.read_status", side_effect=sqlite3.DatabaseError("unreadable")):
            self.request("/lab/campaign", expected=503)
        self.request("/lab/campaign", {}, 404)
        self.request("/lab/campaign/pause", {}, 404)
        self.assertEqual(population, self.server.lab.checkpoint())

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

    def test_export_failure_releases_locks_and_preserves_live_progress(self):
        from wisp.teach import export_lab
        before = self.server.lab.checkpoint()
        self.request("/lab/export", {"path": "ad-hoc"}, 400)
        with patch("wisp.teach.export_lab", side_effect=OSError("disk full")):
            self.request("/lab/export", {}, 503)
            self.assertEqual(self.server.lab.checkpoint(), before)
            self.request("/lab/control", {"action": "configure", "mode": "auto", "running": True})
            self.request("/lab/export", {}, 503)
        self.assertTrue(self.request("/lab/state")["running"])
        paused = self.request("/lab/control", {"action": "configure", "running": False})
        entered = threading.Event()
        finish = threading.Event()
        def package_while_live(*args, snapshot_ready, **options):
            def drop_snapshot_lock():
                snapshot_ready()
                entered.set()
                if not finish.wait(3):
                    raise TimeoutError("Live experiment did not regain its lock")
            return export_lab(*args, snapshot_ready=drop_snapshot_lock, **options)
        with patch("wisp.teach.export_lab", side_effect=package_while_live), ThreadPoolExecutor(1) as pool:
            export = pool.submit(self.request, "/lab/export", {})
            try:
                self.assertTrue(entered.wait(3))
                self.request("/lab/control", {"action": "configure", "mode": "manual"})
                self.request("/lab/control", {"action": "step"})
                self.request("/choice", {"cue": 0})
                self.request("/lab/export", {}, 409)
            finally:
                finish.set()
            bundle = export.result(5)
        self.assertEqual(bundle["step"], paused["step"])
        self.assertEqual(self.request("/lab/state")["step"], paused["step"] + 1)
        self.assertTrue(self.request("/state")["pending"])
        self.assertEqual(Path(bundle["path"]).parent, self.path.parent / "exports")

    def test_export_captures_archived_generation_branch_tip_and_unsaved_personal_choice(self):
        self.request("/save", {})
        last_save = self.path.read_bytes()
        self.request("/choice", {"cue": 1})
        self.request("/feedback", {"value": -1})
        self.request("/choice", {"cue": 0})
        personal = self.server.session.wisp.checkpoint()
        for _ in range(self.server.lab.state()["steps_per_generation"]):
            self.server.lab_request({"action": "step"})
        history = self.request("/lab/history")["generations"]
        self.request("/lab/control", {"action": "branch", "branch": "main", "generation": 1})
        live = self.request("/lab/control", {"action": "step"})
        checkpoint = self.server.lab.checkpoint()
        exported = self.request("/lab/export", {})
        path = Path(exported["path"])
        self.assertEqual(path.name, exported["filename"])
        self.assertEqual((exported["branch_id"], exported["generation"], exported["step"]),
                         (live["active_branch_id"], 2, 1))
        self.assertEqual(self.path.read_bytes(), last_save)
        self.assertEqual(self.server.session.wisp.checkpoint(), personal)
        self.assertEqual(self.server.lab.checkpoint(), checkpoint)
        self.assertEqual(len(self.request("/lab/branches")["branches"]), 2)
        with ZipFile(path) as package:
            self.assertIsNone(package.testzip())
            self.assertEqual(json.loads(package.read("personal.json")), personal)
            self.assertTrue(package.read("reports/generations.csv"))
            self.assertIn("reports/summary.json", package.namelist())
            copy = package.extract("personal.json.lineages.sqlite3", self.folder / "copy")
        archive = LineageArchive(copy)
        try:
            active_id, tip = archive.start({})
            self.assertEqual((active_id, tip), (live["active_branch_id"], checkpoint))
            self.assertEqual(archive.history(active_id), history)
        finally:
            archive.close()

    def finish_source_generation(self):
        for _ in range(self.server.lab.state()["steps_per_generation"]):
            self.server.lab_request({"action": "step"})

    def wait_for_study(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.request("/lab/next-plan")
            if state["status"] != "running":
                return state
            time.sleep(.025)
        self.fail("Memory evaluation did not finish")

    def test_study_uses_archived_generation_while_source_lineage_advances(self):
        start = "/lab/next-plan/start"
        self.assertFalse(self.request("/lab/next-plan")["eligible"])
        self.request(start, {}, 409)
        self.request(start, {"branch": "missing"}, 400)
        self.finish_source_generation()
        self.request("/lab/control", {"action": "next_generation"})
        live = self.request("/lab/control", {"action": "step"})
        source = self.server.archive.snapshot("main", 1)
        personal = self.server.session.wisp.checkpoint()
        began, finish = threading.Event(), threading.Event()
        def evaluate(snapshot, *, seed, repeats, progress, cancel_event):
            self.assertEqual(snapshot, source)
            self.assertEqual((seed, repeats), (41, 2))
            progress(2, len(snapshot["members"]))
            began.set()
            self.assertTrue(finish.wait(3))
            return {"protocol": "mock-memory", "genomes": len(snapshot["members"])}
        with patch("wisp.teach.run_next_plan", side_effect=evaluate):
            started = self.request(start, {})
            self.assertEqual(started["status"], "running")
            self.assertEqual(started["source"]["generation"], 1)
            self.assertTrue(began.wait(3))
            try:
                self.assertGreater(self.request("/lab/next-plan")["progress"], 0.)
                self.request(start, {}, 409)
                self.assertEqual(self.request("/lab/state"), live)
                self.request("/lab/control", {"action": "reset", "population": 32})
                self.assertFalse(self.request("/lab/next-plan")["eligible"])
            finally:
                finish.set()
            completed = self.wait_for_study()
        self.assertEqual(completed["status"], "complete", completed["error"])
        self.assertEqual(completed["source"]["branch_id"], "main")
        data = json.loads(Path(completed["path"]).read_text(encoding="utf-8"))
        self.assertEqual(data["report"], completed["report"])
        self.assertEqual(self.server.session.wisp.checkpoint(), personal)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request("/lab/next-plan"), completed)
        self.request(start, {"branch": "main", "generation": 99}, 400)
        self.assertEqual(self.request("/lab/next-plan"), completed)

    def test_study_error_can_retry_and_shutdown_cancels_processing(self):
        self.finish_source_generation()
        before = self.server.lab.checkpoint()
        start = "/lab/next-plan/start"
        with patch("wisp.teach.run_next_plan", side_effect=OSError("sample failure")):
            self.request(start, {"branch": "main", "generation": 1})
            failed = self.wait_for_study()
            self.assertEqual(failed["status"], "error")
            self.assertIn("sample failure", failed["error"])
            self.assertIsNone(failed["path"])
        entered, cancelled = threading.Event(), threading.Event()
        def wait_for_stop(snapshot, *, seed, repeats, progress, cancel_event):
            entered.set()
            if cancel_event.wait(3):
                cancelled.set()
                raise CancelledError()
            raise RuntimeError("Service did not cancel this study")
        with patch("wisp.teach.run_next_plan", side_effect=wait_for_stop):
            self.request(start, {})
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.server.lab.checkpoint(), before)
            worker = self.server._study_thread
            self.stop_server()
            self.assertTrue(cancelled.is_set())
            self.assertFalse(worker.is_alive())

    def test_real_memory_report_exports_after_a_later_study_fails(self):
        self.finish_source_generation()
        self.request("/lab/control", {"action": "next_generation"})
        untouched = self.server.lab.checkpoint()
        self.request("/lab/next-plan/start", {})
        completed = self.wait_for_study()
        self.assertEqual(completed["status"], "complete", completed["error"])
        self.assertEqual(completed["report"]["protocol"], "wisp-delay-retention-v2")
        with patch("wisp.teach.run_next_plan", side_effect=OSError("interrupted repeat")):
            self.request("/lab/next-plan/start", {})
            self.assertEqual(self.wait_for_study()["status"], "error")
        package = self.request("/lab/export", {})
        with ZipFile(package["path"]) as bundle:
            exported = json.loads(bundle.read("reports/summary.json"))["memory_study"]
        self.assertEqual(exported["report"], completed["report"])
        self.assertEqual(exported["source"], completed["source"])
        self.assertNotIn("path", exported)
        self.assertEqual(self.server.lab.checkpoint(), untouched)

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
