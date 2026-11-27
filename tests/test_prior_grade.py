from dataclasses import replace
import random
import unittest

from wisp.core import Wisp, random_genome
from wisp.experiment import identifier
from wisp.prior_grade import PriorBoundary, assess, score_arm
from wisp.source_attribution import score_arm as score_given


class PriorGradeTests(unittest.TestCase):
    def setUp(self):
        self.genome = replace(random_genome(random.Random(29)), trace_decay=.7)
        self.nominee = {"genome": self.genome.to_dict(), "fingerprint": identifier(self.genome)}

    def test_prior_boundary_uses_native_transition_without_changing_weights(self):
        subject = Wisp(self.genome, seed=197)
        for cue in (1, 0, 0, 0, 0, 0):
            subject.choose(cue)
        before = subject.checkpoint()
        for sign in (-1, 1):
            withheld, boundary, given = (Wisp.restore(before) for _ in range(3))
            PriorBoundary(withheld, "withheld").feedback(sign)
            self.assertEqual(before, withheld.checkpoint())
            PriorBoundary(boundary, "boundary_only").feedback(sign)
            given.feedback(sign)
            actual = boundary.checkpoint()
            expected = given.checkpoint()
            self.assertNotEqual(expected["weights"], before["weights"])
            expected["weights"] = before["weights"]
            self.assertEqual(expected, actual)
            self.assertTrue(boundary.plastic)

    def test_given_matches_the_previous_source_assay_on_identical_lessons(self):
        lessons = [{"seed": str(i)} for i in range(7)]
        for mode in ("neural", "no_plasticity", "source_oracle"):
            current = score_arm(self.genome, mode, lessons)
            previous = score_given(mode, self.genome, lessons)
            for lag, cell in current["by_lag"].items():
                self.assertEqual(cell["accuracy"]["given"], previous["by_lag"][lag]["raw"])

    def test_paired_controls_partitions_and_replay(self):
        validation = assess(self.nominee, 176, "validation")
        self.assertEqual(validation, assess(self.nominee, 176, "validation"))
        confirmation = assess(self.nominee, 176, "confirmation")
        self.assertNotEqual(validation["bank_seed"], confirmation["bank_seed"])
        for report in (validation, confirmation):
            total = report["pairs"] * 24
            for name, arm in report["arms"].items():
                scores = arm["overall"]["accuracy"]
                self.assertEqual(scores["given"]["total"], total)
                if name in ("source_oracle", "no_plasticity"):
                    for row in scores.values():
                        self.assertEqual(row["accuracy"], 1.0 if name == "source_oracle" else .5)
                    self.assertEqual(arm["overall"]["paired"]["given_minus_withheld"]["ties"], total)
                if name == "trace_disabled":
                    self.assertEqual(arm["overall"]["paired"]["boundary_only_minus_withheld"]["ties"], total)
                for parts in (arm["by_lag"], arm["by_context"]):
                    self.assertEqual(sum(row["accuracy"]["given"]["correct"] for row in parts.values()),
                                     scores["given"]["correct"])
                    self.assertEqual(sum(row["accuracy"]["given"]["total"] for row in parts.values()), total)
                ctx = arm["by_context"]
                self.assertEqual(ctx["same_cue_same_target"]["accuracy"]["given"]["total"],
                                 ctx["same_cue_opposite_target"]["accuracy"]["given"]["total"])
                for key, values in arm["overall"]["paired"].items():
                    left, right = key.split("_minus_")
                    self.assertEqual(scores[left]["correct"] - scores[right]["correct"],
                                     values["wins"] - values["losses"])
                    self.assertEqual(total, sum(values[k] for k in ("wins", "losses", "ties")))
                    histories = arm["history_pairs"][key]
                    self.assertEqual(histories["total"], total // 2)
                    self.assertAlmostEqual(histories["delta"], values["delta"])
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            assess(dict(self.nominee, fingerprint="incorrect"), 176, "validation")


if __name__ == "__main__":
    unittest.main()
