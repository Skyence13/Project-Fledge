import unittest
from collections import Counter
from random import Random

from wisp.core import Wisp, random_genome

from wisp.pan_prediction import (
    BLANK, PAN_COMMANDS, TARGET_SPEEDS, centroid, evaluate, observe, projected_frame, trials,
    wisp_turn,
)


class PanPredictionTests(unittest.TestCase):
    def test_legacy_and_graded_one_hot_share_dynamics(self):
        legacy = Wisp(random_genome(Random(12)), 123)
        graded = Wisp.restore(legacy.checkpoint())
        for cue in (1, 1, 0, 1, 0):
            self.assertEqual(legacy.choose(cue), graded.choose_levels(float(cue == 0), float(cue == 1)))
            legacy.feedback(1)
            graded.feedback(1)
            self.assertEqual(legacy.checkpoint(), dict(graded.checkpoint(), last_cue=cue))
        for bad in (-1.0, 1.1, float("nan")):
            with self.assertRaises(ValueError):
                graded.choose_levels(0, bad)

    def test_founder_gets_graded_inputs_and_checkpoint_replays_its_turn(self):
        self.assertEqual(projected_frame(BLANK), (0., 0.))
        self.assertEqual(projected_frame(observe(15)), (0.25, 0.75))
        history, _, _, _ = next(trials(19, "development", "test", 1, 6))
        learner = Wisp(random_genome(Random(8)), 9)
        wisp_turn(learner, history)
        restored = Wisp.restore(learner.checkpoint())
        for wisp in (learner, restored):
            wisp.feedback(1)
        self.assertEqual(wisp_turn(learner, history), wisp_turn(restored, history))
        self.assertEqual(learner.checkpoint(), restored.checkpoint())

    def test_receptors_and_shutter(self):
        for bearing in (-30, -19.2, 0, 6, 30):
            self.assertAlmostEqual(centroid(observe(bearing)), bearing)
        self.assertEqual(observe(7.5), (0.0, 0.0, 0.5, 0.5, 0.0))
        self.assertEqual(observe(31), BLANK)
        with self.assertRaises(ValueError):
            centroid(BLANK)

    def test_motion_and_pan_are_fully_crossed(self):
        samples = list(trials(702, "development", "test", 4, 6))
        counts = Counter((h.pan_command, speed) for h, _, _, speed in samples)
        self.assertEqual(counts, {(p, s): 4 for p in PAN_COMMANDS for s in TARGET_SPEEDS})
        for history, reveal, truth, _ in samples:
            self.assertEqual(history.shuttered, BLANK)
            self.assertAlmostEqual(centroid(reveal), truth)

    def test_matched_streams_and_held_out_banks(self):
        def histories(bank, phase, gain):
            return [h for h, _, _, _ in trials(702, bank, phase, 3, gain)]
        test = histories("validation", "test", 6)
        self.assertEqual(test, histories("validation", "test", 2))
        self.assertEqual(test, histories("validation", "test", 6))
        self.assertTrue(set(test).isdisjoint(histories("validation", "calibration", 6)))
        self.assertTrue(set(test).isdisjoint(histories("confirmation", "test", 6)))

    def test_controls_discriminate_changed_motor_gain(self):
        for bank in ("development", "validation", "confirmation"):
            report = evaluate(702, bank)
            self.assertEqual(len(report["synthetic_founder_turns"]), 6)
            for row in report["synthetic_founder_turns"]:
                self.assertEqual(row["trials"], 48)
                self.assertTrue(0 < row["right_targets"] < 48)
            results = {(r["actual_gain"], r["model"]): r["scores"] for r in report["rows"]}
            for gain in (2., 4., 6.):
                for group, size in (("moving", 32), ("stationary", 16)):
                    calibrated = results[gain, "calibrated_body"][group]
                    self.assertEqual(calibrated["trials"], size)
                    self.assertEqual(calibrated["inside_one_degree"], size)
                    nominal = results[gain, "nominal_body"][group]
                    self.assertEqual(nominal["inside_one_degree"], size if gain == 4 else 0)
                    self.assertEqual(results[gain, "motion_only"][group]["inside_one_degree"], 0)
