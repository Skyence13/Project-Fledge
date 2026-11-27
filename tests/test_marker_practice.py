from collections import Counter
from dataclasses import replace
from itertools import product
import random
import unittest

from wisp.core import random_genome
from wisp.experiment import identifier
from wisp.marker_practice import assess, practice, practice_schedule


class RecordingLearner:
    """Freely emit the same six actions each episode and record public events."""

    def __init__(self):
        self.events = []
        self.action_index = 0

    def choose(self, cue):
        action = (1, 0, 0, 1, 0, 1)[self.action_index % 6]
        self.action_index += 1
        self.events.append(("cue", cue))
        return action

    def feedback(self, sign):
        self.events.append(("grade", sign))


class MarkerPracticeTests(unittest.TestCase):
    def test_each_lifetime_crosses_control_sign_with_every_practice_cell(self):
        expected = Counter(product((0, 1), (0, 1), (0, 1), (-1, 1)))
        for seed in (13, 321, 2026):
            schedule = practice_schedule(seed)
            self.assertEqual(Counter(schedule), expected)
            self.assertEqual(practice_schedule(seed), schedule)
        self.assertNotEqual(practice_schedule(13), practice_schedule(321))

    def test_only_the_probe_grade_changes_between_practice_arms(self):
        schedule = practice_schedule(123)
        for arm in ("accurate", "independent"):
            learner = RecordingLearner()
            practice(learner, schedule, arm)
            expected = []
            for cue, marker, target, sign in schedule:
                # This actor's early source action is 0, late source action is 1,
                # and probe action is 1, independently of cue and hidden target.
                source_grade = 1 if target == marker else -1
                probe_grade = (1 if target == 1 else -1) if arm == "accurate" else sign
                expected.extend([("cue", c) for c in (marker, cue, 1 - cue, cue, 1 - cue)])
                expected.extend([("grade", source_grade), ("cue", cue), ("grade", probe_grade)])
            self.assertEqual(learner.events, expected)

    @classmethod
    def setUpClass(cls):
        genome = replace(random_genome(random.Random(418)), trace_decay=.7)
        cls.nominee = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
        cls.report = assess(cls.nominee, 8891, "validation")

    def test_replay_split_isolation_and_archive_identity(self):
        self.assertEqual(self.report, assess(self.nominee, 8891, "validation"))
        confirmation = assess(self.nominee, 8891, "confirmation")
        self.assertNotEqual(self.report["bank_seed"], confirmation["bank_seed"])
        with self.assertRaises(ValueError):
            assess(dict(self.nominee, fingerprint="wrong"), 8891, "validation")
        with self.assertRaises(ValueError):
            assess(self.nominee, 8891, "selection")

    def test_mirrored_targets_share_prefix_and_conflicts_count_once(self):
        for learner, arms in self.report["arms"].items():
            for arm in ("accurate", "independent"):
                informative = arms[arm]["informative"]
                shuffled = arms[arm]["shuffled"]
                self.assertEqual(informative["conflict_histories_by_marker"],
                                 shuffled["conflict_histories_by_marker"])
                for row, mirrors in ((informative, 2), (shuffled, 4)):
                    with self.subTest(learner=learner, arm=arm, mirrors=mirrors):
                        self.assertEqual(row["histories"], 4 * self.report["pairs"])
                        nconflict = row["conflict_histories"]
                        self.assertEqual(sum(row["conflict_histories_by_marker"].values()), nconflict)
                        self.assertEqual(row["conflict"]["total"], mirrors * nconflict)
                        self.assertEqual(row["overall"]["total"], mirrors * row["histories"])
                        for key in ("correct", "total"):
                            self.assertEqual(row["overall"][key],
                                             row["conflict"][key] + row["agreement"][key])
                if shuffled["conflict_histories"]:
                    self.assertEqual(shuffled["conflict"]["accuracy"], .5)

    def test_control_limits_and_paired_differences(self):
        arms = self.report["arms"]
        for arm in ("accurate", "independent"):
            for learner in ("cue_lookup", "marker_lookup"):
                info = arms[learner][arm]["informative"]
                self.assertGreater(info["conflict_histories"], 0)
                self.assertEqual(info["agreement"]["accuracy"], 1)
                self.assertEqual(info["conflict"]["accuracy"], 1 if learner == "marker_lookup" else .5)
        for learner, conditions in self.report["paired"].items():
            for condition, paired in conditions.items():
                accurate = arms[learner]["accurate"][condition]["overall"]
                independent = arms[learner]["independent"][condition]["overall"]
                with self.subTest(learner=learner, condition=condition):
                    self.assertEqual(paired["total"], accurate["total"])
                    self.assertEqual(paired["wins"] + paired["losses"] + paired["ties"], paired["total"])
                    self.assertEqual(paired["wins"] - paired["losses"], accurate["correct"] - independent["correct"])
                    self.assertAlmostEqual(paired["difference"], accurate["accuracy"] - independent["accuracy"])
                    if learner == "no_plasticity":
                        self.assertEqual(accurate["accuracy"], .5)
                        self.assertEqual(paired["ties"], paired["total"])

