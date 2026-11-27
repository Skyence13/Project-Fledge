import json
from pathlib import Path
import tempfile
import unittest

from wisp.core import Wisp
from wisp.experiment import CONDITIONS, TEACH_ROUNDS, evaluate, lesson_bank, main, run_experiment


class ExperimentTests(unittest.TestCase):
    def test_balanced_assignments_and_external_controls(self):
        test = lesson_bank(91, "held-out", 2)
        train = lesson_bank(91, "train-0", 2)
        self.assertTrue({x.seed for x in test}.isdisjoint(x.seed for x in train))
        self.assertEqual(test[0].seed, test[1].seed)
        self.assertEqual([test[0].assignment, test[1].assignment], [0, 1])
        reflex = evaluate(None, test, "reflex")["summary"]
        association = evaluate(None, test, "association")["summary"]
        self.assertEqual((reflex["fitness"], reflex["learning_gain"]), (0.5, 0))
        self.assertEqual(association["before_teaching"], 0.5)
        self.assertEqual(association["after_teaching"], 1)
        self.assertEqual(association["before_reteaching"], 0)
        self.assertEqual(association["after_reteaching"], 1)

    def test_curves_record_choices_before_each_feedback(self):
        result = evaluate(None, lesson_bank(91, "held-out", 2), "association")
        self.assertEqual(result["summary"]["teaching_curve"],
                         [0.5, 0.5] + [1.0] * (2 * TEACH_ROUNDS - 2))
        self.assertEqual(result["summary"]["reversal_curve"],
                         [0.0, 0.0] + [1.0] * (2 * TEACH_ROUNDS - 2))
        self.assertEqual(len(result["episodes"][0]["teaching_curve"]), 2 * TEACH_ROUNDS)

    def test_founders_controls_and_schedule_are_matched(self):
        report, checkpoint = run_experiment(9, 2, 2, 1, 1, 1)
        self.assertEqual(list(report["conditions"]), list(CONDITIONS))
        reference = report["conditions"]["fixed"]["history"][0]["scores"]
        for condition in report["conditions"].values():
            self.assertEqual(condition["history"][0]["scores"], reference)
            for mode in ("held_out", "no_plasticity", "uninformative_feedback"):
                row = condition[mode]
                self.assertEqual(len(row["episodes"]), 2)
                for episode, lesson in zip(row["episodes"], report["seed_manifest"]["held_out"]):
                    self.assertEqual(episode["seed"], lesson["seed"])
                    self.assertEqual(episode["assignment"], lesson["assignment"])
                self.assertEqual(row["summary"]["before_teaching"], 0.5)
            # With rewards severed from correctness, both halves of a pair see the
            # same cues and signs, and the correct answers in the halves are opposite.
            self.assertEqual(condition["uninformative_feedback"]["summary"]["fitness"], 0.5)
            self.assertEqual(condition["no_plasticity"]["summary"]["fitness"], 0.5)
        restored = Wisp.restore(json.loads(json.dumps(checkpoint)))
        self.assertIn(restored.choose(0), (0, 1))

    def test_parallel_workers_preserve_evolution(self):
        args = dict(seed=321, population=2, generations=2, train_pairs=1, test_pairs=1)
        serial, individual = run_experiment(**args, workers=1)
        parallel, parallel_individual = run_experiment(**args, workers=2)
        parallel["settings"]["workers"] = 1
        self.assertEqual(serial, parallel)
        self.assertEqual(individual, parallel_individual)

    def test_cli_exports_a_plain_individual_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            main(["--seed", "5", "--population", "2", "--generations", "1",
                  "--train-pairs", "1", "--test-pairs", "1", "--output", tmp])
            report = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
            checkpoint = json.loads((Path(tmp) / report["checkpoint"]["file"]).read_text(encoding="utf-8"))
            self.assertIn(Wisp.restore(checkpoint).choose(1), (0, 1))
            timing = json.loads((Path(tmp) / "timing.json").read_text(encoding="utf-8"))
            self.assertEqual(timing["selection_lifetimes"], 2 * 1 * 2 * len(CONDITIONS))
            self.assertGreaterEqual(timing["wall_seconds"], timing["selection_evaluation_seconds"])
            self.assertGreater(timing["selection_lifetimes_per_second"], 0)
            self.assertNotIn("wall_seconds", report)

    def test_reject_unusable_population(self):
        with self.assertRaisesRegex(ValueError, "population"):
            run_experiment(population=1)


if __name__ == "__main__":
    unittest.main()
