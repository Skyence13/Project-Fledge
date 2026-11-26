import json
import random
import unittest

from wisp.core import Genome, Wisp, mutate, random_genome


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.founder = random_genome(random.Random(17))

    def test_inheritance_and_independent_mutation_switches(self):
        original = self.founder.to_dict()
        self.assertEqual(self.founder, random_genome(random.Random(17)))
        self.assertEqual(self.founder, Genome.from_dict(json.loads(json.dumps(original))))
        for rules in (False, True):
            for wiring in (False, True):
                child = mutate(self.founder, random.Random(13), rules=rules, wiring=wiring)
                replica = mutate(self.founder, random.Random(13), rules=rules, wiring=wiring)
                self.assertEqual(child, replica)
                self.assertEqual(child, Genome.from_dict(child.to_dict()))
                self.assertNotEqual(child.learning_rate, self.founder.learning_rate)
                if not wiring:
                    endpoints = lambda genome: [(e.source, e.target) for e in genome.edges]
                    self.assertEqual(endpoints(child), endpoints(self.founder))
                    self.assertNotEqual(child.edges, self.founder.edges)
                    self.assertEqual([n.id for n in child.nodes], [n.id for n in self.founder.nodes])
                if not rules:
                    inherited = {n.id: n for n in self.founder.nodes}
                    for n in child.nodes:
                        if n.id in inherited:
                            self.assertEqual(n, inherited[n.id])
                if rules and not wiring:
                    self.assertNotEqual(child.nodes, self.founder.nodes)
        self.assertEqual(self.founder.to_dict(), original)

    def test_first_offspring_can_switch_rule_and_descendants_can_retain_it(self):
        for seed in range(12):
            founder = random_genome(random.Random(seed))
            self.assertEqual({node.adaptation > 0 for node in founder.nodes}, {True, False})
        directions = set()
        for seed in range(100):
            child = mutate(self.founder, random.Random(seed), rules=True, wiring=False)
            for old, new in zip(self.founder.nodes, child.nodes):
                if bool(old.adaptation) != bool(new.adaptation):
                    directions.add((bool(old.adaptation), bool(new.adaptation)))
            fixed_child = mutate(child, random.Random(seed), rules=False, wiring=False)
            self.assertEqual(fixed_child.nodes, child.nodes)
        self.assertEqual(directions, {(False, True), (True, False)})

    def test_all_structural_variations_possible_in_first_offspring(self):
        changes = set()
        signatures = self.founder.to_dict()
        for seed in range(100):
            child = mutate(self.founder, random.Random(seed), rules=False, wiring=True)
            self.assertEqual(child, Genome.from_dict(child.to_dict()))
            changes.add((len(child.nodes) - len(self.founder.nodes),
                         len(child.edges) - len(self.founder.edges)))
        self.assertIn((0, -1), changes)
        self.assertIn((0, 1), changes)
        self.assertIn((1, 1), changes)  # split an existing connection
        self.assertTrue(any(n == 1 and edges > 1 for n, edges in changes))  # duplicate
        self.assertTrue(any(n == -1 for n, _ in changes))
        self.assertEqual(self.founder.to_dict(), signatures)

    def test_checkpoint_mid_trial_restores_random_stream_and_pending_feedback(self):
        original = Wisp(self.founder, 9)
        for cue in [1, 0, 0, 1]:
            action = original.choose(cue)
            original.feedback(1 if action == cue else -1)
        original.choose(0)
        original.choose(1)  # an unreinforced probe may be followed by another choice
        restored = Wisp.restore(json.loads(json.dumps(original.checkpoint())))
        self.assertEqual(original.checkpoint(), restored.checkpoint())
        for wisp in [original, restored]:
            wisp.feedback(-1)
            self.assertEqual(wisp.trials, 5)
            with self.assertRaises(ValueError):
                wisp.feedback(-1)
        for i in range(25):
            self.assertEqual(original.choose(i % 2), restored.choose(i % 2))
            original.feedback(1)
            restored.feedback(1)
        self.assertEqual(original.checkpoint(), restored.checkpoint())

    def test_opposite_feedback_changes_weights_only_with_plasticity(self):
        for plastic in (True, False):
            positive = Wisp(self.founder, 23, plastic=plastic)
            negative = Wisp(self.founder, 23, plastic=plastic)
            before = positive.checkpoint()["weights"]
            self.assertEqual(positive.choose(1), negative.choose(1))
            positive.feedback(1)
            negative.feedback(-1)
            weights = positive.checkpoint()["weights"]
            self.assertEqual(weights != negative.checkpoint()["weights"], plastic)
            self.assertEqual(weights != before, plastic)
            self.assertEqual(positive.genome, Genome.from_dict(self.founder.to_dict()))
            self.assertEqual(Wisp(self.founder).checkpoint()["weights"], before)

    def test_lifetime_learning_and_reversal(self):
        wisp = Wisp(random_genome(random.Random(1)), seed=1)
        schedule = random.Random(77)
        for inversion in (0, 1):
            for _ in range(32):
                cue = schedule.randrange(2)
                correct = wisp.choose(cue) == (cue ^ inversion)
                wisp.feedback(1 if correct else -1)
            learned = wisp.checkpoint()["weights"]
            successes = 0
            for _ in range(80):
                cue = schedule.randrange(2)
                successes += wisp.choose(cue) == (cue ^ inversion)
            self.assertGreaterEqual(successes, 64)
            self.assertEqual(wisp.checkpoint()["weights"], learned)
        self.assertEqual(wisp.trials, 64)


if __name__ == "__main__":
    unittest.main()
