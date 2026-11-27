from collections import Counter
import unittest

from wisp.pan_prediction import BLANK, PAN_COMMANDS, TARGET_SPEEDS
from wisp.pan_trials import GradeOnly, make_trials, reference_predictions


class MatchedPanTests(unittest.TestCase):
    def test_mirrors_balance_targets_in_each_motion_stratum_and_keep_every_condition(self):
        for repeats in (1, 4):
            low = make_trials(711, repeats, 2)
            high = make_trials(711, repeats, 6)
            self.assertEqual([row[0] for row in low], [row[0] for row in high])
            self.assertEqual(low, make_trials(711, repeats, 2))
            self.assertNotEqual(low, make_trials(712, repeats, 2))
            for rows in (low, high):
                cells = Counter((speed, h.pan_command) for h, _, _, speed in rows)
                self.assertEqual(cells, {(s, c): repeats for s in TARGET_SPEEDS for c in PAN_COMMANDS})
                for moving in (False, True):
                    bearings = [b for _, _, b, s in rows if bool(s) == moving]
                    self.assertNotIn(0, bearings)
                    self.assertCountEqual(bearings, [-b for b in bearings])
                by_bearing = {b: (h, speed) for h, _, b, speed in rows}
                for h, reveal, bearing, speed in rows:
                    mirror, mirror_speed = by_bearing[-bearing]
                    self.assertNotEqual(reveal, BLANK)
                    self.assertEqual(h.shuttered, BLANK)
                    self.assertEqual(mirror.pan_command, -h.pan_command)
                    self.assertEqual(mirror_speed, -speed)
                    self.assertEqual(mirror.first, tuple(reversed(h.first)))
                    self.assertEqual(mirror.before_pan, tuple(reversed(h.before_pan)))

    def test_public_grade_inference_and_tie_break(self):
        learner = GradeOnly()
        self.assertEqual(learner.gain(), 4)
        learner.feedback(3.0, 1, False, -1)
        self.assertEqual(learner.errors, {2: 0, 4: 1, 6: 1})
        self.assertEqual(learner.gain(), 2)
        learner.errors = {2: 0, 4: 2, 6: 0}
        self.assertEqual(learner.gain(), 2)

    def test_grade_only_is_invariant_to_angle_reveal_and_test_truth(self):
        practice = make_trials(712, 1, 2)
        tests = make_trials(713, 4, 2)
        altered = [(h, other[1], b, s) for (h, _, b, s), other
                   in zip(practice, make_trials(712, 1, 6))]
        masked_tests = [(h, None, None, None) for h, _, _, _ in tests]
        predictions = reference_predictions(practice, tests)
        revised = reference_predictions(altered, masked_tests)
        for name in ("grade_only", "last_seen", "motion_only", "nominal_body"):
            self.assertEqual(predictions[name], revised[name])
        self.assertNotEqual(predictions["privileged_reveal"], revised["privileged_reveal"])
        for prediction, (_, _, target, _) in zip(predictions["privileged_reveal"], tests):
            self.assertAlmostEqual(prediction, target, places=10)
            self.assertEqual(prediction > 0, target > 0)
