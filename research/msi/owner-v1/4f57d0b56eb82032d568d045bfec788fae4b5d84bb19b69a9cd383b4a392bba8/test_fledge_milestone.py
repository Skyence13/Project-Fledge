"""Recovery rehearsals only use disposable archives and directories."""

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import fledge_milestone as m


class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.campaign = self.work / m.CAMP
        self.campaign.mkdir(parents=True)
        (self.campaign / "continuations").mkdir()
        self.store = self.work / m.BACKUPS
        self.store.mkdir()
        self.anchor = "a" * 64
        m.save(self.campaign / "active_research.json", {"review_sha256": self.anchor, "state": "bounded_study_complete"})
        m.save(self.work / m.OWNER / self.anchor / "decision.json", {"review_sha256": self.anchor})

    def test_sqlite_wal_and_paused_isolation(self):
        source = self.campaign / "campaign.sqlite3"
        db = sqlite3.connect(source)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE messages(value TEXT)")
        db.execute("INSERT INTO messages VALUES('committed')")
        db.commit()
        db.execute("INSERT INTO messages VALUES('uncommitted')")
        m.save(self.work / m.OWNER / "cutover.json", {"hourly_paused_and_idle": True})
        with m.new_tree(self.store, "fixture-") as stage, patch.object(m.Collector, "sources", return_value={}):
            manifest = m.capture(self.work, self.store, stage, self.anchor)
        dest = m.restore(self.store, manifest, self.work / "isolated")
        with m.closing(m.readonly(dest / m.CAMP / "campaign.sqlite3")) as selection:
            self.assertEqual(selection.execute("SELECT value FROM messages").fetchall(), [("committed",)])
        self.assertTrue((dest / m.CAMP / "supervisor/paused").is_file())
        self.assertTrue((dest / m.CAMP / "paused").is_file())
        self.assertFalse((self.campaign / "paused").exists())
        self.assertFalse((dest / m.OWNER / "cutover.json").exists())
        self.assertFalse(any(name.endswith(("-wal", "-shm", "-journal")) for name in manifest["files"]))

    def test_corrupt_blob_path_or_existing_destination_refused(self):
        c = m.Collector(self.work, self.store, self.work)
        c.put("data/file.json", self.campaign / "active_research.json")
        spec = c.files["data/file.json"]
        archive = {"files": {"../outside": spec}}
        with self.assertRaises(ValueError):
            m.restore(self.store, archive, self.work / "invalid")
        with self.assertRaisesRegex(ValueError, "already exists"):
            m.restore(self.store, {"files": c.files}, self.campaign)
        h = spec["sha256"]
        (self.store / "objects" / h[:2] / h).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "differs"):
            m.restore(self.store, {"files": c.files}, self.work / "changed")

    def test_personal_export_identified_and_checked(self):
        data = b'{"fixture":"inert saved individual"}'
        fingerprint = {"sha256": m.sha(data), "size": len(data)}
        package = self.work / m.RUNS / "exports" / "fledge-lab-test.zip"
        package.parent.mkdir()
        def make_archive(record):
            with ZipFile(package, "w") as z:
                z.writestr("manifest.json", json.dumps({"files":{"personal.json":record}}))
                z.writestr("personal.json", data)
        with m.new_tree(self.store, "fixture-") as stage:
            make_archive(fingerprint)
            collector = m.Collector(self.work, self.store, stage)
            self.assertEqual(collector.personal(), package.name)
            self.assertEqual(collector.files["personal-export/personal.json"], fingerprint)
            self.assertTrue(any("dated export" in gap for gap in collector.gaps))
            make_archive(dict(fingerprint, sha256="b" * 64))
            with self.assertRaisesRegex(ValueError, "digest differs"):
                collector.personal()

    def test_changing_boundary_is_deferred(self):
        original = m.Collector.personal
        def changed(collector):
            (self.campaign / "new-record.json").write_text("{}")
            return original(collector)
        with m.new_tree(self.store, "fixture-") as stage, patch.object(m.Collector, "sources", return_value={}), \
                patch.object(m.Collector, "personal", changed), self.assertRaises(m.BoundaryChanged):
            m.capture(self.work, self.store, stage, self.anchor)
        self.assertFalse((self.store / "latest.json").exists())
        p = self.work / m.OWNER / self.anchor / "decision.json"
        p.write_text(json.dumps({"review_sha256": "b" * 64}))
        with self.assertRaises(m.BoundaryChanged):
            m.boundary(self.work)


if __name__ == "__main__":
    unittest.main()
