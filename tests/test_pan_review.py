import random
import unittest
from unittest.mock import patch

from wisp.core import Wisp, random_genome
from wisp.pan_review import (
    bank_decision, fresh_skills, paired_advantage, post_practice_pair, post_practice_summary,
)


class PanReviewTests(unittest.TestCase):
    def test_post_practice_reuses_weights_preserves_sources_and_reenables_plasticity(self):
        genome = random_genome(random.Random(126))
        a = Wisp(genome, 31)
        b = Wisp(genome, 31, plastic=False)
        for agent in (a, b):
            agent.choose_levels(.25, .75)
            agent.feedback(1)
        sources = [agent.checkpoint() for agent in (a, b)]
        self.assertNotEqual(sources[0]["weights"], sources[1]["weights"])
        received = []

        def recorder(agent, lesson):
            received.append((agent.checkpoint(), lesson))
            return {"after_teaching": .5, "after_reteaching": 1.}

        with patch("wisp.pan_review.run_lesson", side_effect=recorder):
            pair = post_practice_pair(a, b, 4331)
        self.assertEqual([item[0] for item in received], [
            dict(sources[i], plastic=True) for i in (0, 0, 1, 1)
        ])
        self.assertEqual([item[1].assignment for item in received], [0, 1, 0, 1])
        self.assertEqual(sources, [agent.checkpoint() for agent in (a, b)])
        self.assertTrue(post_practice_summary([pair])["passed"])
        pair["intact"]["recall"] -= .051
        self.assertFalse(post_practice_summary([pair])["passed"])
        a.choose(0)
        with self.assertRaises(ValueError):
            post_practice_pair(a, b, 4331)

    def test_bootstrap_is_paired_and_reproducible(self):
        a = [0, .5, .75, 1]
        null = paired_advantage(a, a, 670)
        self.assertEqual(null["ci95"], [0, 0])
        effect = paired_advantage(a, [0] * 4, 670)
        self.assertEqual(effect, paired_advantage(a, [0] * 4, 670))
        self.assertEqual(effect["difference"], .5625)
        self.assertEqual((effect["lifetimes"], effect["draws"]), (4, 2000))
        self.assertLess(effect["ci95"][0], effect["difference"])
        self.assertGreater(effect["ci95"][1], effect["difference"])

    def test_changed_body_decision_requires_all_gates(self):
        margins = {"nonplastic": .1, "independent": {"difference": .12}}
        args = [[.65, .75, .8, .8], margins, True, True, True]
        self.assertTrue(bank_decision(*args)["passed"])
        for i in (2, 3, 4):
            variant = args.copy()
            variant[i] = False
            self.assertFalse(bank_decision(*variant)["passed"])
        args[0] = [.64, 1., .8, 1.]
        self.assertFalse(bank_decision(*args)["passed"])
        args[0] = [.8] * 4
        margins["independent"] = .09
        self.assertFalse(bank_decision(*args)["passed"])

    def test_fresh_control_pools_fixed_counts_and_checks_each_lag(self):
        recall = {"correct": 95, "total": 100, "accuracy": .95}
        metrics = {"mixed_recall": {"accuracy": .90},
                   "fixed_recall": {lag: recall for lag in (1, 3, 5)},
                   "fixed_one_shot": {lag: {"accuracy": .75} for lag in (1, 3, 5)},
                   "old_skills": {name: {"accuracy": .90} for name in ("immediate", "reversal")}}
        report = {"bank_seed": "test", "protocol": "fixture", "arms": {"intact": metrics}}
        with patch("wisp.pan_review.transfer", return_value=report):
            self.assertTrue(fresh_skills({}, 6, "validation")["passed"])
            metrics["fixed_one_shot"][3]["accuracy"] = .74
            self.assertFalse(fresh_skills({}, 6, "validation")["gates"]["each_fixed_one_shot"])
