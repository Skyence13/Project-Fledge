from collections import Counter
from random import Random
from unittest import TestCase
from unittest.mock import patch

from wisp.core import random_genome
from wisp.experiment import derived_seed, identifier
from wisp.marker_practice import practice_schedule
from wisp import yoked_practice as study


class Recorder:
    def __init__(self, seed):
        self.seed = seed
        self.events = []
        self.n = 0

    def choose(self, cue):
        action = (0, 1, 0, 0, 1, 1)[self.n % 6]
        self.n += 1
        self.events.append(("choose", cue))
        return action

    def feedback(self, sign):
        self.events.append(("grade", sign))


class YokedTests(TestCase):
    def test_donation_swaps_and_shuffles_without_replacement(self):
        # Distinguishable tokens test permutation even if donors have equal approval rates.
        tapes = [list(range(16)), list(range(16, 32))]
        for member in (0, 1):
            expected = tapes[1 - member].copy()
            Random(derived_seed(79, "donor-order", 4, member)).shuffle(expected)
            actual = study.donated_grades(tapes, 79, 4, member)
            self.assertEqual(actual, expected)
            self.assertCountEqual(actual, tapes[1 - member])
            self.assertNotEqual(actual, tapes[1 - member])

    def test_distinct_lifetimes_and_public_grade_routing(self):
        pair = list(study.practice_pair(Recorder, 91, 0))
        self.assertNotEqual(pair[0][0]["accurate"].seed, pair[1][0]["accurate"].seed)
        schedules = [practice_schedule(derived_seed(91, "practice-order", 0, i)) for i in (0, 1)]
        self.assertNotEqual(*schedules)
        for i, (group, grades) in enumerate(pair):
            self.assertEqual(len({a.seed for a in group.values()}), 1)
            schedule = schedules[i]
            self.assertEqual(grades["yoked"], study.donated_grades(
                [entry[1]["accurate"] for entry in pair], 91, 0, i))
            self.assertEqual(grades["independent"], [row[3] for row in schedule])
            self.assertEqual(grades["accurate"], [1 if row[2] == 1 else -1 for row in schedule])
            for arm, agent in group.items():
                expected = []
                for (cue, marker, target, _), grade in zip(schedule, grades[arm]):
                    expected += [("choose", c) for c in (marker, cue, 1-cue, cue, 1-cue)]
                    expected += [("grade", 1 if target == 1-marker else -1), ("choose", cue), ("grade", grade)]
                self.assertEqual(agent.events, expected)

    def test_pair_resampling_unit_and_direction(self):
        row = study.paired_interval([-4, 4], 313)
        self.assertEqual(row["difference"], 0)
        self.assertEqual(row["ci95"], [-.5, .5])
        self.assertEqual(row["pairs"], 2)
        self.assertEqual(row["histories"], 16)

    def test_synthetic_genome_replay_calibration_and_denominators(self):
        self.assertEqual(study.STARTS, {"validation": 128, "confirmation": 256})
        genome = random_genome(Random(865))
        champion = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
        calls = Counter()
        def checkpoint():
            calls["pairs"] += 1
        with patch.dict(study.STARTS, validation=8, confirmation=8):
            a = study.assess(champion, 59, "validation", checkpoint=checkpoint)
            self.assertEqual(a, study.assess(champion, 59, "validation"))
            self.assertNotEqual(a["bank_seed"], study.assess(champion, 59, "confirmation")["bank_seed"])
        self.assertEqual(calls["pairs"], 5 * 4)
        for learner, arms in a["arms"].items():
            grades = a["practice_probe_feedback"][learner]
            self.assertEqual(grades["accurate"], grades["yoked"])
            self.assertEqual(grades["independent"], {"positive": 64, "negative": 64})
            for arm, results in arms.items():
                for cond, mirrors in (("informative", 2), ("shuffled", 4)):
                    cell = results[cond]
                    self.assertEqual(cell["histories"], 32)
                    self.assertEqual(cell["overall"]["total"], 32 * mirrors)
                    self.assertEqual(cell["conflict"]["total"], mirrors * cell["conflict_histories"])
                    self.assertEqual(cell["conflict_histories"], sum(cell["conflict_histories_by_marker"].values()))
                    if learner in ("cue_lookup", "marker_lookup") and cond == "shuffled":
                        self.assertEqual(cell["conflict"]["accuracy"], .5)
                if learner == "marker_lookup":
                    self.assertEqual(results["informative"]["overall"]["accuracy"], 1)
                if learner == "cue_lookup":
                    by_credit = results["informative"]["by_credit"]
                    self.assertEqual(by_credit["early"]["conflict"]["accuracy"], 0)
                    self.assertEqual(by_credit["late"]["conflict"]["accuracy"], 1)
            for first, second in (("yoked", "accurate"), ("independent", "yoked")):
                actual = a["primary"][learner][f"{first}_minus_{second}"]
                expected = (arms[first]["informative"]["conflict_histories"]
                            - arms[second]["informative"]["conflict_histories"]) / 32
                self.assertEqual(actual["difference"], expected)
            for first, second in study.RECALL_CONTRASTS:
                paired = a["paired"][learner][f"{first}_minus_{second}"]
                for cond in study.CONDITIONS:
                    x, y = (arms[arm][cond]["overall"] for arm in (first, second))
                    self.assertEqual(paired[cond]["difference"], x["accuracy"] - y["accuracy"])
                    self.assertEqual(sum(paired[cond][k] for k in ("wins","ties","losses")), x["total"])
