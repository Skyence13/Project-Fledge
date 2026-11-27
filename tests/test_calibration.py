from concurrent.futures import CancelledError
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from wisp.calibration import AdaptivePacer, _estimate, calibrate
from wisp.lab import LabController
from wisp.lineage import LineageArchive


class CalibrationTests(unittest.TestCase):
    def test_runs_full_lifetimes_and_breeding_without_touching_the_lineage(self):
        active = LabController(per_condition=2)
        active.command({"action": "configure", "mode": "auto", "running": True, "auto_advance": True})
        for _ in range(150):
            active.tick()
        before = active.checkpoint()
        saved = json.dumps(before)
        # Fixed clock observations provide a reproducible workload budget across CI hosts.
        with patch("wisp.calibration.perf_counter", side_effect=(i * 0.001 for i in range(1000))):
            report = calibrate(before, duration=0.06)
        self.assertEqual(json.dumps(before), saved)
        self.assertEqual(active.checkpoint(), before)
        self.assertTrue(report["representative"])
        self.assertGreaterEqual(report["samples"], 3)
        self.assertGreaterEqual(report["generations"], 3)
        self.assertGreater(report["training_trials"], 0)
        self.assertGreater(report["probe_trials"], 0)
        self.assertLess(report["cap"], report["measured_trials_per_second"])
        self.assertEqual(report["presets"][0], 1)
        self.assertEqual(report["presets"][-1], report["cap"])
        self.assertEqual(report["presets"], sorted(set(report["presets"])))

    def test_short_partial_sample_reports_uncertainty_and_uses_more_headroom(self):
        lab = LabController(per_condition=2)
        with patch("wisp.calibration.perf_counter", side_effect=(0, 0.65)):
            report = calibrate(lab.checkpoint(), duration=0.15)
        self.assertFalse(report["representative"])
        self.assertEqual(report["samples"], 0)
        self.assertEqual(report["headroom"], 0.50)
        self.assertEqual(report["cap"], 1)
        self.assertGreater(_estimate([100, 100, 100], True)[0], _estimate([100], False)[0])
        self.assertEqual(_estimate([2, 3, 4, 900], True)[0], 1)

    def test_generation_commits_use_a_separate_database_in_the_chosen_directory(self):
        lab = LabController(per_condition=2)
        destinations = []
        real_record = LineageArchive.record

        def record(archive, *args):
            result = real_record(archive, *args)
            destinations.append(Path(archive._db.execute("PRAGMA database_list").fetchone()[2]))
            self.assertTrue(destinations[-1].is_file())
            return result

        with TemporaryDirectory() as root:
            live = Path(root) / "lineage.sqlite3"
            live.write_bytes(b"independent existing lineage")
            with patch.object(LineageArchive, "record", record):
                report = calibrate(lab.checkpoint(), duration=0.15, archive_dir=root)
            self.assertEqual(report["archive_location"], "lineage volume")
            self.assertTrue(destinations)
            self.assertTrue(all(path.parent.parent == Path(root) for path in destinations))
            self.assertEqual(list(Path(root).iterdir()), [live])
            self.assertEqual(live.read_bytes(), b"independent existing lineage")

    def test_cancel_and_failed_commit_remove_disposable_archive(self):
        cancelled = threading.Event()
        real_record = LineageArchive.record

        def cancel_after_commit(archive, *args):
            real_record(archive, *args)
            cancelled.set()

        lab = LabController(per_condition=2)
        lab.command({"action": "configure", "mode": "auto", "running": True, "auto_advance": True})
        while lab.step != len(lab.schedule) - 1:
            lab.tick()
        checkpoint = lab.checkpoint()
        with TemporaryDirectory() as root:
            with patch.object(LineageArchive, "record", cancel_after_commit), self.assertRaises(CancelledError):
                calibrate(checkpoint, archive_dir=root, cancel_event=cancelled)
            self.assertEqual(list(Path(root).iterdir()), [])
            with patch.object(LineageArchive, "record", side_effect=OSError("test write error")), self.assertRaises(OSError):
                calibrate(checkpoint, archive_dir=root)
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertEqual(lab.checkpoint(), checkpoint)

    def test_cancellation_and_invalid_budget(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CancelledError):
            calibrate({}, cancel_event=cancelled)
        with self.assertRaises(ValueError):
            calibrate({}, duration=float("inf"))

    def test_adaptive_rate_derates_for_slow_work_and_recovers_below_calibrated_cap(self):
        pacer = AdaptivePacer(200)
        pacer.observe(16, 0.16, 200)
        reduced = pacer.effective_rate(200)
        self.assertLess(reduced, 120)
        self.assertEqual(pacer.effective_rate(1), 1)
        pacer.observe(32, 0.02, 200)
        self.assertGreater(pacer.capacity, reduced)
        self.assertLess(pacer.capacity, 200)
        for _ in range(200):
            pacer.observe(32, 0.02, 200)
        self.assertEqual(pacer.capacity, 200)
        self.assertEqual(pacer.effective_rate(1000), 200)

    def test_overrun_is_bounded_and_zero_cost_cannot_run_unpaced(self):
        pacer = AdaptivePacer(512)
        pacer.observe(1, 0.005, 512, overrun_seconds=3600)
        self.assertGreater(pacer.capacity, 2)
        self.assertLess(pacer.capacity, 512)
        for _ in range(120):
            pacer.observe(32, 0.0, 512)
        self.assertEqual(pacer.capacity, 512)


if __name__ == "__main__":
    unittest.main()
