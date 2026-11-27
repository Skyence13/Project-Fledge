import json
from pathlib import Path
from random import Random
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wisp import archived_pan as pan
from wisp.campaign import SCHEMA, VERSION, _pack
from wisp.core import random_genome
from wisp.experiment import identifier
from wisp.pan_trials import make_trials


class ArchivedPanTests(unittest.TestCase):
    def test_read_only_nominee_lookup_and_invalid_archives(self):
        genome = random_genome(Random(81))
        fingerprint = identifier(genome)
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            db = sqlite3.connect(directory / "campaign.sqlite3")
            for statement in SCHEMA:
                db.execute(statement)
            db.execute("PRAGMA user_version=1")
            db.execute("INSERT INTO tip VALUES(1,?,?)", (json.dumps({"version": VERSION}), b""))
            report = json.dumps({"champion_id": "fixed:0", "fingerprint": fingerprint})
            db.execute("INSERT INTO rounds VALUES(1,?,?)", (_pack({"fixed": [genome.to_dict()]}), report))
            db.commit()
            self.assertEqual(pan.verified_nominee(directory, 1, fingerprint)["genome"], genome.to_dict())
            with self.assertRaises(ValueError):
                pan.verified_nominee(directory, 1, "wrong")
            with self.assertRaises(ValueError):
                pan.verified_nominee(directory, 3, fingerprint)
            db.execute("UPDATE rounds SET population=?", (_pack({"fixed": [dict(genome.to_dict(), nodes=[])]}),))
            db.commit()
            with self.assertRaises(ValueError):
                pan.verified_nominee(directory, 1, fingerprint)
            db.execute("PRAGMA user_version=7")
            db.close()
            with self.assertRaisesRegex(ValueError, "schema"):
                pan.verified_nominee(directory, 1, fingerprint)

    def test_each_probe_is_a_fresh_graded_state_with_a_common_rng(self):
        genome = random_genome(Random(91))
        practice = make_trials(12, 1, 2)
        tests = make_trials(13, 4, 2)
        agents = pan.practice_arms(genome, practice, 98, 99)
        snapshots = {name: agent.checkpoint() for name, agent in agents.items()}
        self.assertEqual({a.trials for a in agents.values()}, {12})
        self.assertEqual(snapshots["intact"]["rng"], snapshots["independent"]["rng"])
        seeds = list(range(len(tests)))
        for name, agent in agents.items():
            answers = pan.test_turns(agent, tests, seeds)
            self.assertEqual(answers[::-1], pan.test_turns(agent, tests[::-1], seeds[::-1]))
            self.assertEqual(snapshots[name], agent.checkpoint())
        self.assertEqual(agents["nonplastic"].checkpoint()["weights"], [e.weight for e in genome.edges])

    def test_bad_instrument_stops_before_training(self):
        genome = random_genome(Random(9))
        nominee = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
        with patch.object(pan, "reference_predictions", return_value={"privileged_reveal": [0.] * 48}), \
                patch.object(pan, "practice_arms", side_effect=AssertionError):
            result = pan.run_bank(nominee, 28, 29, "validation", synthetic=True)
            self.assertFalse(result["instrumentation_passed"])
