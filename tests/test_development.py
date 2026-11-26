import unittest

from wisp.development import assess
from wisp.experiment import evaluate
from wisp.lab import LabController


def finish(lab):
    while not lab.awaiting_next:
        lab.command({"action": "step"})


class DevelopmentTests(unittest.TestCase):
    def test_pending_and_partial_reports_are_observations_without_side_effects(self):
        lab = LabController(per_condition=2)
        empty = assess(lab)
        self.assertEqual(empty["fitness"]["observed"], 0)
        self.assertIsNone(empty["fitness"]["mean"])
        self.assertTrue(all(r["status"] == "pending" and r["accuracy"] is None for r in empty["evidence"]))
        for _ in range(9):
            lab.command({"action": "step"})
        snapshot = lab.checkpoint()
        live = assess(lab)
        self.assertEqual(lab.checkpoint(), snapshot)
        baseline = live["evidence"][0]
        self.assertEqual((baseline["observed"], baseline["expected"], baseline["status"]), (64, 128, "partial"))
        self.assertGreater(live["memory"]["weights_changed"], 0)
        self.assertEqual(live, assess(LabController.restore(snapshot)))

    def test_complete_phase_scores_match_independent_batch_evaluations(self):
        lab = LabController(seed=13, per_condition=2)
        independent = [evaluate(m.genome, lab.lessons)["summary"] for m in lab.members]
        finish(lab)
        report = assess(lab)
        for row in report["evidence"]:
            expected = sum(s[row["id"]] for s in independent) / len(independent)
            self.assertAlmostEqual(row["accuracy"], expected)
            self.assertEqual(row["status"], "complete")
            self.assertEqual(row["expected"], row["observed"])
        self.assertEqual(report["fitness"]["correct"], sum(r["correct"] for r in report["evidence"]
                                                           if r["id"].startswith("after_")))
        self.assertEqual(report["fitness"]["mean"], report["fitness"]["correct"] / report["fitness"]["observed"])
        self.assertEqual(sum(c["observed"] for c in report["conditions"]), report["fitness"]["observed"])
        self.assertFalse(report["trend"]["available"])
        previous = report["fitness"]["mean"]
        lab.command({"action": "next_generation"})
        self.assertFalse(assess(lab)["trend"]["available"])
        finish(lab)
        child = assess(lab)
        self.assertAlmostEqual(child["trend"]["delta_mean"], child["fitness"]["mean"] - previous)

    def test_legacy_snapshot_retains_combined_fitness_and_only_collects_future_phase_counts(self):
        lab = LabController(seed=3, per_condition=2)
        for _ in range(32):
            lab.command({"action": "step"})
        old = lab.checkpoint()
        old.pop("phase_counts")
        old.pop("preferred_rate")
        resumed = LabController.restore(old)
        report = assess(resumed)
        self.assertIsNotNone(report["fitness"]["mean"])
        self.assertEqual(report["evidence"][1]["status"], "unrecorded")
        self.assertIsNone(report["evidence"][1]["accuracy"])
        finish(resumed)
        finish(lab)
        legacy = assess(resumed)
        self.assertEqual(legacy["fitness"], assess(lab)["fitness"])
        self.assertEqual(legacy["evidence"][1]["status"], "partial")
        self.assertEqual(legacy["evidence"][1]["unrecorded"], 64)
        self.assertEqual(legacy["evidence"][-1]["status"], "complete")
        self.assertIn("predates", legacy["interpretation"])
        resumed.command({"action": "next_generation"})
        finish(resumed)
        self.assertTrue(all(row["status"] == "complete" for row in assess(resumed)["evidence"]))
