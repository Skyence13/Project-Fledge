import sqlite3
from pathlib import Path
import tempfile
import unittest

from wisp.lineage import LineageArchive


class LineageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "new-dir" / "lineages.sqlite3"
        self.archive = LineageArchive(self.path)
        self.addCleanup(lambda: self.archive.close())
        self.root, _ = self.archive.start({"step": 0})

    def record(self, branch, generation):
        checkpoint = {"generation": generation, "eval": [branch, generation]}
        self.archive.record(branch, generation, checkpoint, {"mean": 0.5})
        return checkpoint

    @staticmethod
    def offspring(cp, key):
        return {"source": cp, "future_seed_key": key, "step": 0}

    def test_many_generations_paging_and_read_only_historical_lookup(self):
        for generation in range(1, 76):
            self.record(self.root, generation)
        latest = self.archive.history(self.root, limit=5)
        self.assertEqual([row["generation"] for row in latest], [75, 74, 73, 72, 71])
        self.assertEqual(self.archive.history(self.root, before=71, limit=1)[0]["generation"], 70)
        self.assertEqual(self.archive.snapshot(self.root, 1)["eval"], [self.root, 1])
        self.assertEqual(self.archive.start({})[1]["generation"], 75)
        self.assertEqual(self.archive.history(self.root, before=1), [])
        with self.assertRaises(ValueError):
            self.archive.history(self.root, limit=0)
        with self.assertRaises(ValueError):
            self.archive.snapshot(self.root, True)
        with self.assertRaises(ValueError):
            self.archive.snapshot(self.root, 80)

    def test_branch_from_old_generation_preserves_source_tip_and_survives_restart(self):
        first = self.record(self.root, 1)
        second = self.record(self.root, 2)
        live = {"generation": 3, "step": 17, "weights": [0.75, -0.2]}
        child, born = self.archive.fork(self.root, 1, "Alternate", live, self.offspring)
        self.assertEqual(born["source"], first)
        self.assertEqual(born["future_seed_key"], child)
        self.assertEqual(self.archive.history(child)[0]["branch_id"], self.root)
        with self.assertRaises(ValueError):
            self.archive.snapshot(child, 2)
        child_second = self.record(child, 2)
        self.assertNotEqual(child_second, second)
        child_tip = {"step": 13, "future_seed_key": child}
        self.assertEqual(self.archive.switch(self.root, child_tip), (self.root, live))
        self.assertEqual(self.archive.snapshot(self.root, 2), second)
        self.archive.close()
        self.archive = LineageArchive(self.path)
        self.assertEqual(self.archive.start({"ignored": True}), (self.root, live))
        self.assertEqual(self.archive.switch(child, live), (child, child_tip))
        self.assertEqual(self.archive.snapshot(child, 2), child_second)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.archive.record(child, 2, {"different": True}, {"mean": 0.9})

    def test_fork_from_inactive_lineage_and_nested_fork_resolve_ancestry(self):
        for gen in range(1, 5):
            self.record(self.root, gen)
        first, _ = self.archive.fork(self.root, 3, "", {"parent_tip": 51}, self.offspring)
        self.record(first, 4)
        second, _ = self.archive.fork(self.root, 2, None, {"first_tip": 10}, self.offspring)
        self.record(second, 3)
        self.assertEqual(self.archive.switch(first, {"second_tip": 5})[1], {"first_tip": 10})
        third, _ = self.archive.fork(first, 2, "From shared ancestor", {"first_tip": 11}, self.offspring)
        self.assertEqual([(r["generation"], r["branch_id"]) for r in self.archive.history(third)],
                         [(2, self.root), (1, self.root)])
        self.assertEqual(self.archive.switch(second, {"third_tip": 2})[1], {"second_tip": 5})
        self.assertEqual(self.archive.switch(self.root, {"second_tip": 6})[1], {"parent_tip": 51})

    def test_transaction_rolls_back_failed_fork_and_reset_starts_independent_run(self):
        self.record(self.root, 1)
        self.archive.save_tip(self.root, {"step": 19})
        with self.assertRaises(TypeError):
            self.archive.fork(self.root, 1, "Will fail", {"invalid": object()}, self.offspring)
        self.assertEqual(len(self.archive.branches()), 1)
        self.assertEqual(self.archive.start({})[1], {"step": 19})
        new, tip = self.archive.new_run({"fresh": True}, {"step": 20})
        self.assertEqual(tip, {"fresh": True})
        self.assertEqual(self.archive.history(new), [])
        self.assertIsNone(self.archive.branches()[1]["parent_id"])
        self.assertEqual(self.archive.switch(self.root, {"step": 3})[1], {"step": 20})

    def test_single_owner_and_idempotent_boundary(self):
        with self.assertRaisesRegex(ValueError, "another laboratory"):
            LineageArchive(self.path)
        cp = self.record(self.root, 1)
        self.archive.record(self.root, 1, cp, {"mean": 0.5})
        with self.assertRaises(ValueError):
            self.archive.record(self.root, 3, {}, {})
        self.assertEqual(len(self.archive.history(self.root)), 1)
        self.archive.close()
        with sqlite3.connect(self.path) as reader:
            self.assertEqual(reader.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        reader.close()

    def test_invalid_target_tip_preserves_active_and_saved_progress(self):
        alternate, _ = self.archive.new_run({"state": "initial"}, {"step": 5})
        self.archive.switch(self.root, {"invalid_model": True})

        def validate(tip):
            if "invalid_model" in tip:
                raise ValueError("invalid neural model")

        with self.assertRaisesRegex(ValueError, "invalid neural model"):
            self.archive.switch(alternate, {"step": 7}, validate_tip=validate)
        self.assertEqual(self.archive.start({}), (self.root, {"step": 5}))
        # Emulate a damaged file and verify the service receives a readable failure.
        self.archive.close()
        db = sqlite3.connect(self.path)
        with db:
            db.execute("UPDATE branches SET tip = ? WHERE id = ?", (b"bad compressed data", alternate))
        db.close()
        self.archive = LineageArchive(self.path)
        with self.assertRaisesRegex(ValueError, "could not be decoded"):
            self.archive.switch(alternate, {"step": 8}, validate_tip=validate)
        self.assertEqual(self.archive.start({}), (self.root, {"step": 5}))


if __name__ == "__main__":
    unittest.main()
