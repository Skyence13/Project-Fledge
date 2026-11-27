from dataclasses import replace
from itertools import product
import random
import unittest

from wisp.core import random_genome
from wisp.experiment import identifier
from wisp.marked_recall import CueLookup, _score, assess, marked_trial
from wisp.mixed_delay import DELAYS
from wisp.source_attribution import trial


class Scripted:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.events = []

    def choose(self, cue):
        self.events.append(("cue", cue))
        return next(self.actions)

    def feedback(self, sign):
        self.events.append(("grade", sign))


class MarkedRecallTests(unittest.TestCase):
    def test_singleton_lookup_reads_previous_action_before_overwriting(self):
        for seed, cue, lag, target in product(range(9), (0, 1), DELAYS, (0, 1)):
            actor = CueLookup(seed)
            trial(actor, "cue_lookup", 1 - cue, 3, 1 - target, -1)
            _, correct = trial(actor, "cue_lookup", cue, lag, target, -1)
            self.assertTrue(correct)

    def test_grade_selects_only_hidden_occurrence(self):
        for marker, occurrence, target in product((0, 1), repeat=3):
            actor = Scripted([0, 0, 1, 1, 0, 0])
            conflict, correct = marked_trial(actor, cue=1, marker=marker,
                                             occurrence=occurrence, target=target)
            sign = 1 if occurrence == target else -1
            self.assertEqual(actor.events, [("cue", c) for c in (marker, 1, 0, 1, 0)] +
                             [("grade", sign), ("cue", 1)])
            self.assertTrue(conflict)
            self.assertEqual(correct, target == 0)

    def test_same_action_cannot_claim_conflict_performance(self):
        rows = _score(lambda seed: Scripted([0] * 6), bank=31, repeats=1)
        for condition in rows.values():
            self.assertEqual(condition["conflict_histories"], 0)
            self.assertIsNone(condition["conflict"]["accuracy"])
            self.assertEqual(condition["overall"]["accuracy"], .5)

    def test_reference_ceiling_chance_and_split_replay(self):
        genome = replace(random_genome(random.Random(2)), trace_decay=.6)
        champion = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
        validation = assess(champion, 23, "validation")
        self.assertEqual(assess(champion, 23, "validation"), validation)
        confirmation = assess(champion, 23, "confirmation")
        self.assertNotEqual(validation["bank_seed"], confirmation["bank_seed"])
        for report in (validation, confirmation):
            self.assertEqual(report["singleton_cue_lookup"]["accuracy"], 1)
            self.assertEqual(report["singleton_cue_lookup"]["total"], report["pairs"] * 12)
            for arm, rows in report["arms"].items():
                for condition, row in rows.items():
                    n = 2 if condition == "informative" else 4
                    self.assertEqual(row["overall"]["total"], report["pairs"] * 4 * n)
                    self.assertEqual(row["conflict"]["total"], row["conflict_histories"] * n)
                    self.assertEqual(row["histories"], report["pairs"] * 4)
                    self.assertEqual(row["conflict"]["total"] + row["agreement"]["total"],
                                     row["overall"]["total"])
                    if arm in ("cue_lookup", "marker_lookup"):
                        self.assertGreater(row["conflict_histories"], 0)
                        self.assertEqual(row["agreement"]["accuracy"], 1)
                        expected = 1 if (arm, condition) == ("marker_lookup", "informative") else .5
                        self.assertEqual(row["conflict"]["accuracy"], expected)
                    if arm == "no_plasticity":
                        self.assertEqual(row["overall"]["accuracy"], .5)
                    if condition == "shuffled" and row["conflict_histories"]:
                        self.assertEqual(row["conflict"]["accuracy"], .5)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            assess(dict(champion, fingerprint="invalid"), 23, "validation")
        with self.assertRaisesRegex(ValueError, "held-out"):
            assess(champion, 23, "selection")
