import csv
from hashlib import sha256
import io
import json
from pathlib import Path
from random import Random
import sqlite3
import tempfile
import unittest
from zipfile import ZipFile

from wisp.core import Wisp, random_genome
from wisp.lab import LabController
from wisp.lineage import LineageArchive
from wisp.transfer import (DATABASE, FILES, GENERATIONS, MANIFEST, PERSONAL, SUMMARY,
                           export_lab, import_lab)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = LineageArchive(self.root / "source.sqlite3")
        self.addCleanup(self.archive.close)
        self.original = LabController(seed=11, per_condition=2)
        self.branch, _ = self.archive.start(self.original.checkpoint())
        for _ in range(len(self.original.schedule)):
            self.original.command({"action": "step"})
        parent = self.original.checkpoint()
        self.archive.record(self.branch, 1, parent, self.original.history[-1])
        self.original.command({"action": "next_generation"})
        self.original.command({"action": "step"})
        self.child_id, child_tip = self.archive.fork(
            self.branch, 1, "Transfer test", self.original.checkpoint(),
            lambda cp, key: LabController.restore(cp).fork(key).checkpoint())
        self.child = LabController.restore(child_tip)
        for _ in range(19):
            self.child.command({"action": "step"})
        self.personal = Wisp(random_genome(Random(33)),99)
        self.personal.choose(1)
        self.bundle = export_lab(self.archive, self.child_id, self.child.checkpoint(),
                                 self.personal.checkpoint(), self.root / "exports")

    def copy_package(self, name, edits, rehash=False):
        with ZipFile(self.bundle) as initial:
            files = {info.filename: initial.read(info) for info in initial.infolist()}
        files.update(edits)
        if rehash:
            manifest = json.loads(files[MANIFEST])
            for path in FILES:
                data = files[path]
                manifest["files"][path] = {"size": len(data), "sha256": sha256(data).hexdigest()}
            files[MANIFEST] = json.dumps(manifest).encode()
        path = self.root / name
        with ZipFile(path, "x") as package:
            for filename, data in files.items():
                package.writestr(filename, data)
        return path

    def test_full_round_trip_exact_tip_individual_and_generations_and_results(self):
        with ZipFile(self.bundle) as package:
            self.assertEqual(set(package.namelist()), set(FILES) | {MANIFEST})
            manifest = json.loads(package.read(MANIFEST))
            for name in FILES:
                self.assertEqual(sha256(package.read(name)).hexdigest(), manifest["files"][name]["sha256"])
            summary = json.loads(package.read(SUMMARY))
            self.assertEqual(summary["development"]["step"], 19)
            self.assertEqual(summary["active_branch_id"], self.child_id)
            self.assertTrue(manifest["source"]["module_sha256_lf"]["wisp/core.py"])
            results = list(csv.DictReader(io.StringIO(package.read(GENERATIONS).decode())))
            self.assertEqual(len(results), 2)
            self.assertEqual({r["origin_branch_id"] for r in results}, {self.branch})
            self.assertEqual({r["branch_id"] for r in results}, {self.branch,self.child_id})
            self.assertEqual(float(results[0]["mean_fitness_0_to_1"]), self.original.history[-1]["mean_fitness"])
        self.assertEqual(self.archive.start({})[1]["step"], 0)  # Export never changes the source live tip.
        imported = import_lab(self.bundle, self.root / "imported")
        personal = Wisp.restore(json.loads((imported / PERSONAL).read_bytes()))
        self.assertEqual(personal.checkpoint(), self.personal.checkpoint())
        self.assertTrue(personal.pending)
        restore = LineageArchive(imported / DATABASE)
        try:
            key, tip = restore.start({})
            self.assertEqual(key, self.child_id)
            self.assertEqual(tip, self.child.checkpoint())
            resumed = LabController.restore(tip)
            resumed.command({"action": "step"})
            self.child.command({"action": "step"})
            self.assertEqual(resumed.checkpoint(), self.child.checkpoint())
            self.assertEqual(restore.switch(self.branch,resumed.checkpoint())[1], self.original.checkpoint())
            self.assertEqual(restore.snapshot(self.branch,1), self.archive.snapshot(self.branch,1))
        finally:
            restore.close()

    def test_bad_members_checksum_invalid_model_and_occupied_destination_do_not_publish(self):
        bad_personal = self.personal.checkpoint()
        bad_personal["pending"] = 1
        with ZipFile(self.bundle) as current:
            bad_csv = current.read(GENERATIONS).replace(b"main,main,", b"=2+2,main,", 1)
        for filename, change, rehash in [
            ("traversal.zip", {"../outside": b"invalid"}, False),
            ("mismatch.zip", {PERSONAL: b"A" * len(json.dumps({}))}, False),
            ("invalid.zip", {PERSONAL: b"{}"}, True),
            ("invalid-personal.zip", {PERSONAL: json.dumps(bad_personal).encode()}, True),
            ("damaged.zip", {DATABASE: b"invalid db"}, True),
            ("invalid-report.zip", {GENERATIONS: bad_csv}, True),
        ]:
            with self.subTest(filename=filename):
                pkg = self.copy_package(filename, change, rehash)
                with self.assertRaises(ValueError):
                    import_lab(pkg, self.root / "target")
                self.assertFalse((self.root / "target").exists())
                self.assertFalse((self.root / "outside").exists())
        occupied = self.root / "occupied"
        occupied.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            import_lab(self.bundle, occupied)
        self.assertEqual(list(occupied.iterdir()), [])

    def test_reject_tampered_sqlite_schema_before_running_it(self):
        path = self.root / "patched.db"
        with ZipFile(self.bundle) as source:
            path.write_bytes(source.read(DATABASE))
        db = sqlite3.connect(path)
        try:
            with db:
                db.execute("CREATE TABLE unexpected_schema(value INTEGER)")
        finally:
            db.close()
        bad = self.copy_package("bad-schema.zip", {DATABASE:path.read_bytes()}, rehash=True)
        with self.assertRaisesRegex(ValueError, "schema"):
            import_lab(bad, self.root / "not-created")
        self.assertFalse((self.root / "not-created").exists())
