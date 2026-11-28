from copy import deepcopy
import json
from pathlib import Path
from random import Random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "fledge-one-blank-pr13"))
import fledge_blank_breeding as breeding
from wisp.core import random_genome


class PopulationEvolutionFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = Random(898314)
        cls.enrolled = breeding.enroll({group: [random_genome(rng).to_dict() for _ in range(4)]
                                        for group in breeding.CONDITIONS})
        cls.cohorts = []
        cls.reports = []
        members = cls.enrolled
        for index in range(breeding.GENERATIONS + 1):
            cls.cohorts.append(members)
            members, summary, cls.nominee = breeding.generation(members, index)
            cls.reports.append(summary)
        cls.banks = {split: breeding.evaluate_bank(cls.nominee, cls.enrolled, split) for split in breeding.LIFETIMES}

    def test_real_offspring_pedigree_cap_and_training_only_selection(self):
        founder_hashes = {(m["group"], m["slot"]): m["fingerprint"] for m in self.enrolled}
        for generation in range(1, len(self.cohorts)):
            parents = {(m["group"], m["slot"]): m for m in self.cohorts[generation - 1]}
            children = self.cohorts[generation]
            self.assertEqual(12, sum(m["birth_generation"] == generation for m in children))
            for m in children:
                self.assertLessEqual(len(m["genome"]["nodes"]), 12)
                self.assertEqual(founder_hashes[m["group"], m["founder_slot"]], m["founder_fingerprint"])
                if m["birth_generation"] == generation:
                    p = parents[m["group"], m["parent_slot"]]
                    self.assertEqual((p["fingerprint"], p["founder_slot"]), (m["parent_fingerprint"], m["founder_slot"]))
                    self.assertNotEqual(p["fingerprint"], m["fingerprint"])
        for row in self.reports:
            self.assertEqual(16, row["population"]["count"])
        self.assertNotEqual(self.nominee["fingerprint"], self.nominee["founder_fingerprint"])
        # Held-out seeds cannot steer nomination, including when a checkpoint is reloaded.
        with patch.dict(breeding.SEEDS, {"validation": 733, "confirmation": 734, "validation_earlier": 735}):
            members, summary, chosen = breeding.generation(json.loads(json.dumps(self.cohorts[-1])), breeding.GENERATIONS)
        self.assertIsNone(members)
        self.assertEqual(self.reports[-1], summary)
        self.assertEqual(self.nominee, chosen)

    def test_matched_banks_determinism_and_calibration(self):
        replay = breeding.evaluate_bank(self.nominee, self.enrolled, "validation")
        self.assertEqual(self.banks["validation"], replay)
        self.assertEqual(len(breeding.SEEDS), len(set(breeding.SEEDS.values())))
        for split, bank in self.banks.items():
            breeding.validate_bank(json.loads(json.dumps(bank)), split)
            n = breeding.LIFETIMES[split]
            for arm in breeding.ARMS:
                cells = bank["held_out"][arm]
                self.assertEqual(cells["blank"]["total"], 16 * n)
                self.assertEqual(cells["left_target"]["total"], cells["right_target"]["total"])
                self.assertEqual(cells["dark_only"]["accuracy"], .5)
            self.assertEqual(bank["held_out"]["last_visible_decoder"]["blank"]["accuracy"], 1)
            self.assertEqual(bank["passed"], all(bank["gates"].values()))

    def test_bad_ancestry_and_tampered_metrics_stop(self):
        broken = dict(self.nominee, founder_fingerprint="0" * 64)
        with self.assertRaisesRegex(ValueError, "founder"):
            breeding.evaluate_bank(broken, self.enrolled, "validation")
        for field in ("held_out", "post_practice", "fresh_minus_ancestor"):
            bad = deepcopy(self.banks["validation"])
            if field == "held_out":
                bad[field]["last_visible_decoder"]["blank"]["correct"] -= 1
            elif field == "post_practice":
                bad[field]["difference"]["reversal"] += 1
            else:
                bad[field]["mixed_recall"] += 1
            with self.assertRaises(ValueError):
                breeding.validate_bank(bad, "validation")


if __name__ == "__main__":
    unittest.main()
