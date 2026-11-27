from collections import Counter
from dataclasses import replace
import random
import unittest

from wisp.core import random_genome
from wisp.experiment import identifier
from wisp.mixed_delay import DELAYS, schedules, transfer


class MixedTransferTests(unittest.TestCase):
    def test_matching_and_replay(self):
        rows = list(schedules("485", 16))
        self.assertEqual(rows, list(schedules("485", 16)))
        for fixed, mixed in rows:
            self.assertEqual(len(set(fixed)), 1)
            self.assertEqual(sorted(Counter(mixed).values()), [5, 5, 6])
        for position in zip(*(mixed for _, mixed in rows)):
            self.assertEqual(sorted(position), list(DELAYS))

    def test_fixture_controls_and_retention(self):
        genome = replace(random_genome(random.Random(35)), trace_decay=.6)
        nominee = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
        for split, pairs in (("validation", 16), ("confirmation", 32)):
            arms = transfer(nominee, 7301, split)["arms"]
            for name, expected in (("source_oracle", 1), ("reflex", .5),
                                   ("no_plasticity", .5), ("uninformative_feedback", .5)):
                row = arms[name]
                self.assertEqual(row["mixed_recall"]["total"], pairs * 48)
                self.assertEqual(row["mixed_recall"]["accuracy"], expected)
                for fixed in row["fixed_recall"].values():
                    self.assertEqual(fixed["accuracy"], expected)
            for name in ("intact", "trace_disabled"):
                self.assertEqual(set(arms[name]["old_skills"]), {"immediate", "reversal"})
                self.assertEqual(set(arms[name]["fixed_one_shot"]), set(DELAYS))
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            transfer(dict(nominee, fingerprint="wrong"), 7301, "validation")
