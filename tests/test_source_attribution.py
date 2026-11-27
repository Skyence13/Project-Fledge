from dataclasses import replace
import random
import unittest
from unittest.mock import patch

from wisp.core import random_genome
from wisp.experiment import identifier
from wisp.mixed_delay import DELAYS
from wisp.source_attribution import assess, score_arm, trial


class Recorder:
    def __init__(self):
        self.events = []

    def choose(self, cue):
        self.events.append(("cue", cue))
        return cue

    def feedback(self, sign):
        self.events.append(("grade", sign))


class SourceAttributionTests(unittest.TestCase):
    def test_source_grade_probe_and_conflict(self):
        for lag in DELAYS:
            for mode, expected_grade in (("neural", -1), ("uninformative_feedback", 1)):
                actor = Recorder()
                key, correct = trial(actor, mode, source=1, lag=lag, target=0, control_sign=1)
                cues = [0] * 6
                cues[5 - lag] = 1
                self.assertEqual(actor.events, [("cue", cue) for cue in cues] +
                                 [("grade", expected_grade), ("cue", 1)])
                self.assertEqual(key, "conflict")
                self.assertFalse(correct)

    def test_opposite_targets_replay_prefix_and_change_lag(self):
        actors = []
        def enroll(*args):
            actor = Recorder()
            actors.append(actor)
            return actor

        with patch("wisp.source_attribution._agent", side_effect=enroll):
            score_arm("neural", None, [{"seed": "27"}])
        self.assertEqual(len(actors), 3 * 2 * 2 * 2)
        transitions = set()
        for first, second in zip(actors[::2], actors[1::2]):
            self.assertEqual(len(first.events), 16)
            self.assertEqual(first.events[:14], second.events[:14])
            self.assertEqual(first.events[14][1], -second.events[14][1])
            self.assertEqual(first.events[15], second.events[15])
            lags = []
            for start in (0, 8):
                cues = first.events[start:start + 6]
                probe = first.events[start + 7]
                self.assertEqual(cues.count(probe), 1)
                lags.append(5 - cues.index(probe))
            transitions.add(tuple(lags))
        self.assertEqual(transitions, {(a, b) for a in DELAYS for b in DELAYS if a != b})

    def test_balanced_controls_replay_and_retention(self):
        genome = replace(random_genome(random.Random(35)), trace_decay=.6)
        champion = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
        validation = assess(champion, 13, "validation")
        self.assertEqual(validation, assess(champion, 13, "validation"))
        confirmation = assess(champion, 13, "confirmation")
        self.assertNotEqual(validation["bank_seed"], confirmation["bank_seed"])
        for report in (validation, confirmation):
            arms = report["arms"]
            for lag in map(str, DELAYS):
                for name, expected in (("source_oracle", 1), ("recent_association", .5),
                                       ("reflex", .5), ("no_plasticity", .5),
                                       ("uninformative_feedback", .5)):
                    row = arms[name]["by_lag"][lag]
                    self.assertEqual(row["raw"]["total"], report["pairs"] * 8)
                    self.assertEqual(row["raw"]["accuracy"], expected)
                shortcut = arms["last_action"]["by_lag"][lag]
                self.assertEqual(shortcut["agreement"]["accuracy"], 1)
                self.assertEqual(shortcut["conflict"]["accuracy"], 0)
                self.assertEqual(shortcut["balanced_accuracy"], .5)
                self.assertIsNone(arms["reflex"]["by_lag"][lag]["balanced_accuracy"])
            for name in ("intact", "trace_disabled"):
                self.assertEqual(set(arms[name]["old_skills"]), {"immediate", "reversal"})
                self.assertEqual(set(arms[name]["fixed_one_shot"]), {"1", "3", "5"})
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            assess(dict(champion, fingerprint="wrong"), 13, "validation")
