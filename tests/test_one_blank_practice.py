from collections import Counter
from random import Random
from unittest import TestCase
from unittest.mock import patch

from wisp import one_blank_practice as study
from wisp.core import Wisp, random_genome
from wisp.pan_prediction import observe, projected_frame


class OneBlankPracticeTests(TestCase):
    def test_practice_targets_balance_each_side_and_band(self):
        angles = study.practice_targets(791)
        self.assertEqual(angles, study.practice_targets(791))
        self.assertNotEqual(angles, study.practice_targets(792))
        cells = Counter()
        for angle in angles:
            band = int((abs(angle) - 1) // 7)
            self.assertIn(band, range(4))
            self.assertLessEqual(abs(angle), 7 + 7 * band)
            cells[angle > 0, band] += 1
        self.assertEqual(cells, {(side, band): 4 for side in (False, True) for band in range(4)})

    def test_each_episode_grades_the_second_action_only(self):
        class Recorder:
            def __init__(self, genome, seed, *, plastic=True):
                self.events = []

            def choose_levels(self, left, right):
                self.events.append(("frame", (left, right)))
                return int((left, right) == (0., 0.))

            def feedback(self, grade):
                self.events.append(("grade", grade))

        targets = study.practice_targets(17)
        truth = [1 if angle > 0 else -1 for angle in targets]
        for supplied in (None, [-value for value in truth]):
            with patch.object(study, "Wisp", Recorder):
                agent, applied, turns = study.train(None, targets, 81, grades=supplied)
            expected = truth if supplied is None else supplied
            self.assertEqual(list(applied), expected)
            self.assertEqual(list(turns), [1] * len(targets))
            events = []
            for angle, grade in zip(targets, expected):
                events.extend([("frame", projected_frame(observe(angle))),
                               ("frame", (0., 0.)), ("grade", grade)])
            self.assertEqual(agent.events, events)

    def test_yoking_uses_the_other_lifetimes_approvals_and_matches_budget(self):
        genome = random_genome(Random(81))
        sample = list(study.train_pair(genome, 751, 0))
        replay = list(study.train_pair(genome, 751, 0))
        second = list(study.train_pair(genome, 751, 1))
        self.assertEqual(len(sample), 2)
        self.assertNotEqual(sample[0]["targets"], sample[1]["targets"])
        self.assertNotEqual(sample[0]["targets"], second[0]["targets"])
        for i, (row, again) in enumerate(zip(sample, replay)):
            self.assertEqual(row["donor"], 1 - i)
            self.assertEqual(second[i]["donor"], 3 - i)
            self.assertEqual(row["targets"], again["targets"])
            self.assertEqual(row["grades"], again["grades"])
            self.assertEqual(row["turns"], again["turns"])
            donor = sample[1 - i]["grades"]["accurate"]
            self.assertCountEqual(row["grades"]["yoked"], donor)
            self.assertNotEqual(list(row["grades"]["yoked"]), list(donor))
            for arm in ("accurate", "nonplastic"):
                truthful = [1 if (angle > 0) == turn else -1
                            for angle, turn in zip(row["targets"], row["turns"][arm], strict=True)]
                self.assertEqual(list(row["grades"][arm]), truthful)
            states = {arm: agent.checkpoint() for arm, agent in row["agents"].items()}
            self.assertEqual(set(states), {"accurate", "yoked", "nonplastic"})
            self.assertTrue(all(s["trials"] == 32 for s in states.values()))
            for state in states.values():
                self.assertEqual(state["rng"], states["accurate"]["rng"])
            self.assertNotEqual(states["accurate"]["rng"], sample[1-i]["agents"]["accurate"].checkpoint()["rng"])
            self.assertEqual(states["nonplastic"]["weights"], [edge.weight for edge in genome.edges])

    def test_each_probe_forks_full_practiced_state_with_common_action_draws(self):
        genome = random_genome(Random(228))
        agent, _, _ = study.train(genome, study.practice_targets(911), 809)
        snapshot = agent.checkpoint()
        levels = projected_frame(observe(19.5))
        dark = (0., 0.)
        for seed in (108, 211, 367):
            expected = {}
            for arm, first, last in (("visible", levels, levels), ("blank", levels, dark),
                                     ("dark_only", dark, dark)):
                fork = Wisp.restore(dict(snapshot, rng=Random(seed).getstate()))
                fork.choose_levels(*first)
                expected[arm] = fork.choose_levels(*last)
            self.assertEqual(study.probe_turns(agent, levels, seed), expected)
            self.assertEqual(agent.checkpoint(), snapshot)
