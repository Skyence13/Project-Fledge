import copy
import unittest

from wisp.challenge_evolution import (STAGES, THRESHOLDS, WEIGHTS, breed_population,
                                     population_from_checkpoint, score_generation, seed_trace_population,
                                     validate_champion)
from wisp.core import Genome
from wisp.experiment import CONDITIONS, identifier
from wisp.lab import LabController


class ChallengeEvolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = LabController(seed=9, per_condition=2)
        cls.population = population_from_checkpoint(cls.lab.checkpoint())
        cls.scores = score_generation(cls.population, seed=17, stage=0, generation=1)

    def test_training_scores_and_champion_come_from_balanced_selection_trials(self):
        original = copy.deepcopy(self.population)
        scores = score_generation(self.population, seed=17, stage=0, generation=1)
        self.assertEqual(self.population, original)
        self.assertEqual(self.scores, scores)
        best = max(scores["candidates"], key=lambda row: row["fitness"])
        self.assertEqual(scores["champion"]["id"], best["id"])
        self.assertEqual(scores["champion"]["fingerprint"], best["fingerprint"])
        for row in scores["candidates"]:
            self.assertEqual(row["credit"]["total"], 16)
            self.assertEqual([row[k]["total"] for k in ("delayed", "immediate", "reversal")], [64] * 3)
            self.assertAlmostEqual(row["fitness"], sum(row[k]["accuracy"] * w for k, w in WEIGHTS.items()))
        self.assertNotEqual(scores["bank_seed"],
                            score_generation(self.population, seed=17, stage=0, generation=2)["bank_seed"])

    def test_inherited_genomes_ignore_lifetime_state_and_validate_source(self):
        self.lab.command({"action": "step"})
        checkpoint = self.lab.checkpoint()
        self.assertEqual(self.population, population_from_checkpoint(checkpoint))
        checkpoint["members"].reverse()
        with self.assertRaises(ValueError):
            population_from_checkpoint(checkpoint)
        missing = dict(self.population)
        del missing["fixed"]
        with self.assertRaises(ValueError):
            score_generation(missing, seed=1, stage=0, generation=1)

    def test_condition_specific_elitist_breeding_uses_selection_scores(self):
        child = breed_population(self.population, self.scores, seed=17, generation=1)
        self.assertEqual(child, breed_population(self.population, self.scores, seed=17, generation=1))
        for condition, (rules, wiring) in CONDITIONS.items():
            best = max(range(2), key=lambda slot: self.scores["fitness"][condition][slot])
            parent = self.population[condition][best]
            self.assertEqual(child[condition][0], parent)
            mutated = child[condition][1]
            self.assertNotEqual(mutated, parent)
            if not rules and not wiring:
                self.assertEqual(mutated["nodes"], parent["nodes"])
            if not wiring:
                def topology(g):
                    return [(e["source"], e["target"]) for e in g["edges"]]
                self.assertEqual(topology(mutated), topology(parent))
            self.assertEqual(Genome.from_dict(mutated).to_dict(), mutated)

    def test_paired_branches_have_identical_sources_but_only_rule_arms_can_evolve_traces(self):
        source = seed_trace_population(self.population, 17)
        self.assertEqual(source, self.population)
        self.assertIsNot(source["fixed"][0], self.population["fixed"][0])
        saw_trace = False
        for seed in range(6):
            control = breed_population(source, self.scores, seed=seed, generation=1)
            treatment = breed_population(source, self.scores, seed=seed, generation=1, evolve_trace=True)
            self.assertEqual(control["fixed"], treatment["fixed"])
            self.assertEqual(control["wiring_only"], treatment["wiring_only"])
            for condition in CONDITIONS:
                self.assertEqual(control[condition][0], treatment[condition][0])
                self.assertFalse(any("trace_decay" in genome for genome in control[condition]))
                saw_trace |= any(genome.get("trace_decay", 0) > 0 for genome in treatment[condition])
        self.assertTrue(saw_trace)
        source["rules_only"][0]["trace_decay"] = .5
        with self.assertRaises(ValueError):
            seed_trace_population(source, 17)

    def test_every_delay_separates_recent_only_from_lag_aware_credit(self):
        for stage, _ in enumerate(STAGES):
            with self.subTest(stage=stage):
                result = validate_champion(self.scores["champion"], seed=17, stage=stage, generation=4)
                self.assertEqual(result["credit"]["total"], 64)
                self.assertEqual(result["delayed"]["total"], 256)
                self.assertEqual(set(result["previous_stages"]), {s["id"] for s in STAGES[:stage]})
                for name, metrics in result["controls"].items():
                    self.assertEqual(metrics["credit"]["correct"], 64 if name == "lag_aware_association" else 32)
                    if name in ("reflex", "no_plasticity", "uninformative_feedback"):
                        self.assertEqual(metrics["delayed"]["correct"], 128)
                self.assertEqual(result["controls"]["lag_aware_association"]["delayed"]["correct"], 256)
                scores = [result, *result["previous_stages"].values()]
                self.assertEqual(result["passed"], all(row[key]["accuracy"] >= cutoff for row in scores
                                                       for key, cutoff in THRESHOLDS.items() if key in row))

    def test_confirmation_is_fresh_and_checks_the_same_nominated_genome(self):
        champ = self.scores["champion"]
        banks = {self.scores["bank_seed"]}
        for split, n in (("validation", 64), ("confirmation", 128)):
            result = validate_champion(champ, seed=17, stage=0, generation=1, split=split)
            self.assertNotIn(result["bank_seed"], banks)
            banks.add(result["bank_seed"])
            self.assertEqual(result["credit"]["total"], n)
            self.assertEqual(result["fingerprint"], identifier(Genome.from_dict(champ["genome"])))
        with self.assertRaises(ValueError):
            validate_champion(dict(champ, fingerprint="mismatch"), seed=17, stage=0, generation=1)

    def test_ablation_scores_a_matched_copy_of_the_nominated_genome(self):
        legacy = self.scores["champion"]
        changed = dict(legacy["genome"], trace_decay=.8)
        treated = dict(legacy, genome=changed, fingerprint=identifier(Genome.from_dict(changed)))
        args = dict(seed=17, stage=0, generation=1)
        treatment = validate_champion(treated, **args)
        control = validate_champion(legacy, **args)
        self.assertNotIn("trace_ablation", control)
        self.assertEqual(treatment["bank_seed"], control["bank_seed"])
        self.assertEqual(treatment["trace_ablation"]["inherited_decay"], .8)
        for skill in THRESHOLDS:
            self.assertEqual(treatment["trace_ablation"][skill], control[skill])


if __name__ == "__main__":
    unittest.main()
