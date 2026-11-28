"""Coordinator fault tests on disposable directories. No archived nominee is used."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bounded_evaluation_worker as worker


class BoundedReviewTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(TemporaryDirectory()))
        self.child = self.base / "continuations" / "child"
        self.child.mkdir(parents=True)
        self.parent = self.child.with_name("parent")
        self.parent.mkdir()
        self.cursor = self.base / "active_research.json"
        self.status = {"phase":"research_review", "paused":False}
        self.stack.enter_context(patch.object(worker, "root", self.base))
        self.stack.enter_context(patch.object(worker, "cursor", self.cursor))
        self.stack.enter_context(patch.object(worker, "known_locks", (self.parent,)))
        self.stack.enter_context(patch.object(worker, "relative", lambda p: p.relative_to(self.base).as_posix()))
        self.stack.enter_context(patch.object(worker.campaign_watch, "read_watch_status", return_value=self.status))
        self.spec = SimpleNamespace(PARENT="parent", SOURCE="parent", CHILD="child", PROPOSAL="test.md", CODE=self.base,
            SIBLINGS={"parent"}, MODULE="wisp.test", PROTOCOL="fixture", REVISION="test", PREFIX="probe",
            SEEDS={"sensory":1, "earlier_skills":2}, GENERATION=5, validate_bank=lambda r,s: None,
            summarize=lambda rows: {"result":"done", "retained_both":True, "by_split":list(rows)})
        self.job = worker.TwoBankReview(self.spec)
        self.job.verify_code = Mock()
        self.plan = {"state":"preregistered", "active_path":"continuations/child", "automatic_handoff":False}
        worker.atomic(self.cursor, worker.json_bytes(self.plan))

    def test_hold_and_new_sibling_stop_before_first_probe(self):
        self.job.evaluate = Mock(side_effect=AssertionError("must not run"))
        self.status["paused"] = True
        with self.assertRaisesRegex(RuntimeError, "held"):
            self.job.collect(self.plan)
        self.status["paused"] = False
        (self.parent / "paused").touch()
        with self.assertRaisesRegex(RuntimeError, "hold"):
            self.job.collect(self.plan)
        (self.parent / "paused").unlink()
        self.child.with_name("new").mkdir()
        with self.assertRaisesRegex(RuntimeError, "Unexpected continuation"):
            self.job.collect(self.plan)
        self.job.evaluate.assert_not_called()
        self.assertEqual(worker.read(self.cursor), self.plan)

    def test_live_owner_blocks_and_stale_file_allows(self):
        with worker.campaign._batch_lock(self.parent):
            with self.assertRaisesRegex(ValueError, "owns"):
                with worker.campaign._batch_lock(self.parent):
                    self.fail("Concurrent lock acquired")
        self.assertTrue((self.parent / "writer.lock").exists())
        with worker.campaign._batch_lock(self.parent):
            pass

    def test_interrupted_resume_reuses_verified_bank_and_preserves_completion(self):
        first = {"split":"validation", "instrumentation_passed":True}
        self.job.evaluate = Mock(side_effect=[first, RuntimeError("injected interruption")])
        with self.assertRaisesRegex(RuntimeError, "injected interruption"):
            self.job.collect(self.plan)
        first_path = self.child / "probe-validation.json"
        before = first_path.read_bytes()
        self.assertEqual(worker.read(self.cursor), self.plan)
        first_path.write_bytes(before.replace(b'"validation"', b'"confirmation"'))
        self.job.evaluate = Mock(side_effect=AssertionError("must not score"))
        with self.assertRaisesRegex(RuntimeError, "provenance or hash"):
            self.job.collect(self.plan)
        first_path.write_bytes(before)
        self.job.evaluate = Mock(return_value={"split":"confirmation", "instrumentation_passed":True})
        self.assertEqual(self.job.collect(self.plan), "done")
        self.job.evaluate.assert_called_once_with("confirmation")
        self.assertEqual(first_path.read_bytes(), before)
        self.job.evaluate = Mock(side_effect=AssertionError("must not rescore"))
        receipt = self.cursor.read_bytes()
        self.assertEqual(self.job.collect(self.plan), "done")
        self.assertEqual(self.cursor.read_bytes(), receipt)
        self.job.evaluate.assert_not_called()

    def test_calibration_failure_stops_before_confirmation(self):
        self.job.evaluate = Mock(return_value={"split":"validation","instrumentation_passed":False})
        self.job.collect(self.plan)
        self.job.evaluate.assert_called_once_with("validation")
        self.assertFalse((self.child / "probe-confirmation.json").exists())

    def test_final_review_recovers_cursor_without_running_either_bank(self):
        self.job.evaluate = lambda split: {"split": split, "instrumentation_passed": True}
        original_write = self.job.write

        def interrupt_cursor(path, value):
            if path == self.cursor:
                raise InterruptedError("crash after final review")
            original_write(path, value)

        with patch.object(self.job, "write", side_effect=interrupt_cursor), self.assertRaises(InterruptedError):
            self.job.collect(self.plan)
        review = (self.child / "review.json").read_bytes()
        self.job.evaluate = Mock(side_effect=AssertionError("completed study must not run"))
        confirmation = self.child / "probe-confirmation.json"
        original = confirmation.read_bytes()
        confirmation.unlink()
        with self.assertRaisesRegex(RuntimeError, "bank is missing"):
            self.job.collect(self.plan)
        confirmation.write_bytes(original)
        self.assertEqual(self.job.collect(self.plan), "done")
        self.assertEqual(review, (self.child / "review.json").read_bytes())
        self.assertEqual(worker.read(self.cursor)["state"], "bounded_study_complete")
        self.job.evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
