import json
import hashlib
from dataclasses import replace
import random
import unittest

from wisp.core import Genome, Wisp, mutate, random_genome
from wisp.experiment import identifier


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.founder = random_genome(random.Random(17))

    def test_historical_zero_trace_fingerprint_and_checkpoint_bytes(self):
        self.assertNotIn("trace_decay", self.founder.to_dict())
        self.assertEqual(identifier(self.founder),
                         "c2a1caf04be2ab1d81ae557297bf9962a418b86fdd4a8c0fe833235c369c7f21")
        wisp = Wisp(self.founder, seed=9)
        for cue, grade in ((1, None), (0, -1), (1, None), (1, None), (0, 1)):
            wisp.choose(cue)
            if grade:
                wisp.feedback(grade)
        payload = json.dumps(wisp.checkpoint(), sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         "203a49f9351cb68e232654125883ef44b9ebf69800bdcc2a3052419b13b0db60")

    def test_trace_remembers_ungraded_source_then_clears_after_credit(self):
        genome = replace(self.founder, trace_decay=.6)
        wisp = Wisp(genome, seed=9)
        control = Wisp(self.founder, seed=9)
        source_motor = next(i for i, e in enumerate(genome.edges) if e.source == -1 and e.target == 0)
        for agent in (wisp, control):
            agent.choose(0)
        initial_trace = wisp.checkpoint()["eligibility"][source_motor]
        self.assertEqual(abs(initial_trace), 1)
        for lag in range(1, 4):
            self.assertEqual(wisp.choose(1), control.choose(1))
            self.assertAlmostEqual(wisp.checkpoint()["eligibility"][source_motor], initial_trace * .6**lag)
            self.assertEqual(control.checkpoint()["eligibility"][source_motor], 0)
        before = wisp.checkpoint()
        self.assertEqual(before["version"], 2)
        restored = Wisp.restore(json.loads(json.dumps(before)))
        for agent in (wisp, restored, control):
            agent.feedback(1)
        after = wisp.checkpoint()
        self.assertEqual(restored.checkpoint(), after)
        self.assertNotEqual(after["weights"][source_motor], before["weights"][source_motor])
        self.assertEqual(control.checkpoint()["weights"][source_motor], before["weights"][source_motor])
        self.assertEqual(set(after["eligibility"]), {0.0})
        wisp.choose(1)
        self.assertEqual(wisp.checkpoint()["eligibility"][source_motor], 0.0)
        with self.assertRaises(ValueError):
            Wisp.restore(dict(before, version=1))
        for invalid in (-.1, 1., float("nan"), True):
            with self.assertRaises(ValueError):
                Genome.from_dict(dict(genome.to_dict(), trace_decay=invalid))

    def test_trace_mutation_shares_the_existing_rule_mutation_budget(self):
        observed = set()
        for seed in range(32):
            legacy = mutate(self.founder, random.Random(seed), rules=True, wiring=False)
            opted = mutate(self.founder, random.Random(seed), rules=True, wiring=False, evolve_trace=True)
            observed.add(bool(opted.trace_decay))
            self.assertEqual(legacy.edges, opted.edges)
            self.assertEqual(legacy.learning_rate, opted.learning_rate)
            if opted.trace_decay:
                self.assertEqual(opted.nodes, self.founder.nodes)
                self.assertNotEqual(identifier(opted), identifier(self.founder))
                self.assertEqual(Genome.from_dict(opted.to_dict()), opted)
                fixed = mutate(opted, random.Random(seed), rules=False, wiring=True, evolve_trace=True)
                self.assertEqual(fixed.trace_decay, opted.trace_decay)
            else:
                self.assertEqual(legacy, opted)
        self.assertEqual(observed, {False, True})

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
