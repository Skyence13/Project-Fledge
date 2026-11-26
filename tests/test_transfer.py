import csv
from contextlib import closing
from hashlib import sha256
import io
import json
from pathlib import Path
from random import Random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from wisp import campaign
from wisp.core import Wisp, random_genome
from wisp.campaign import fork_campaign, init_campaign, pause_campaign, read_status, resume_campaign, run_campaign
from wisp.campaign_watch import read_watch_status, set_watch_paused, watch_tick
from wisp.lab import LabController
from wisp.lineage import LineageArchive
from wisp.next_plan import run_next_plan
from wisp.transfer import (CAMPAIGN, CAMPAIGN_DIR, COMPARISONS, DATABASE, FILES, GENERATIONS,
                           HOLD, MANIFEST, PERSONAL, SUMMARY, SUPERVISOR,
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
            for path in manifest["files"]:
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
        self.assertFalse((imported / (PERSONAL + ".next-plan")).exists())
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

    def test_campaign_export_at_committed_round_restores_and_resumes(self):
        source = {"kind": "archive", "branch_id": self.child_id, "branch_name": "Transfer test",
                  "origin_branch_id": self.branch, "generation": 1}
        run = self.root / "source-campaign"
        init_campaign(run, self.archive.snapshot(self.child_id, 1), source, name="Memory", seed=23)
        run_campaign(run, 1)
        db_path = run / "campaign.sqlite3"
        writer = sqlite3.connect(db_path)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            paused = pause_campaign(run)  # The committed pause is still in the WAL at export.
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("DELETE FROM rounds")
            self.bundle = export_lab(self.archive, self.child_id, self.child.checkpoint(),
                                     self.personal.checkpoint(), self.root / "exports", campaign_path=db_path)
        finally:
            writer.rollback()
            writer.close()
        with ZipFile(self.bundle) as package:
            self.assertEqual(set(package.namelist()), {MANIFEST, CAMPAIGN, *FILES})
            payload = package.read(CAMPAIGN)
            self.assertEqual(sha256(payload).hexdigest(), json.loads(package.read(MANIFEST))["files"][CAMPAIGN]["sha256"])
        imported = import_lab(self.bundle, self.root / "with-campaign")
        copied_run = (imported / CAMPAIGN).parent
        self.assertEqual(read_status(copied_run), paused)
        self.assertEqual(run_campaign(copied_run, 2), paused)
        resume_campaign(copied_run)
        resume_campaign(run)
        self.assertEqual(run_campaign(copied_run, 2)["last_training"], run_campaign(run, 2)["last_training"])
        with (closing(sqlite3.connect(copied_run / "campaign.sqlite3")) as copied,
              closing(sqlite3.connect(db_path)) as original):
            for query in ("SELECT * FROM rounds ORDER BY generation", "SELECT population FROM tip"):
                self.assertEqual(copied.execute(query).fetchall(), original.execute(query).fetchall())
        fork = self.root / "campaign-fork"
        forked = fork_campaign(fork, run, 2, stage=1, name="Next challenge", seed=41)
        fork_zip = export_lab(self.archive, self.child_id, self.child.checkpoint(), self.personal.checkpoint(),
                              self.root / "exports", campaign_path=fork / "campaign.sqlite3")
        restored_fork = import_lab(fork_zip, self.root / "with-fork")
        self.assertEqual(read_status((restored_fork / CAMPAIGN).parent), forked)
        rebuilt = self.root / "altered-campaign.sqlite3"
        rebuilt.write_bytes(payload)
        connection = sqlite3.connect(rebuilt)
        try:
            connection.execute("UPDATE tip SET status=json_set(status,'$.source.population_sha256','wrong')")
            connection.commit()
        finally:
            connection.close()
        bad = self.copy_package("wrong-campaign.zip", {CAMPAIGN: rebuilt.read_bytes()}, rehash=True)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            import_lab(bad, self.root / "bad-campaign")
        self.assertFalse((self.root / "bad-campaign").exists())

    def test_supervisor_preserves_partial_pair_and_global_pause_then_resumes_both_arms(self):
        source = {"kind": "archive", "branch_id": self.child_id, "branch_name": "Transfer test",
                  "origin_branch_id": self.branch, "generation": 1}
        run = self.root / "primary-campaign"

        def export(directory):
            return export_lab(self.archive, self.child_id, self.child.checkpoint(), self.personal.checkpoint(),
                              self.root / "exports", campaign_path=directory / "campaign.sqlite3")

        with patch("wisp.campaign.STAGE_BUDGET", 1), patch("wisp.campaign.VALIDATE_EVERY", 1):
            init_campaign(run, self.archive.snapshot(self.child_id, 1), source, name="Memory", seed=23)
            run_campaign(run, 1)
            self.assertEqual(watch_tick(run)["phase"], "comparison")
            state = json.loads((run / "supervisor" / "state.json").read_bytes())
            # Simulate a shutdown between the two idempotent child initializations.
            fork_campaign(run / "comparison" / "legacy", run, state["generation"], stage=state["parent_stage"],
                          name="Delayed feedback / legacy", seed=state["paired_seed"])
            # The second process created the file but crashed before its SQLite transaction committed.
            empty_arm = run / "comparison" / "trace"
            empty_arm.mkdir()
            (empty_arm / "campaign.sqlite3").touch()
            set_watch_paused(run, True)
            incomplete = export(run)
            with ZipFile(incomplete) as package:
                self.assertEqual(set(package.namelist()), {MANIFEST, *FILES, CAMPAIGN, COMPARISONS[0], SUPERVISOR, HOLD})
            imported = import_lab(incomplete, self.root / "interrupted") / CAMPAIGN_DIR
            self.assertTrue(watch_tick(imported)["paused"])
            self.assertFalse((imported / "comparison" / "trace").exists())
            set_watch_paused(imported, False)
            summary = watch_tick(imported)
            self.assertEqual(summary["phase"], "research_review")
            self.assertEqual({arm: v["generation"] for arm, v in summary["arms"].items()}, {"legacy": 1, "trace": 1})
            self.bundle = export(imported)
        copied = import_lab(self.bundle, self.root / "both") / CAMPAIGN_DIR
        self.assertEqual(read_watch_status(copied), summary)
        for arm in ("legacy", "trace"):
            self.assertEqual(read_status(copied / "comparison" / arm), read_status(imported / "comparison" / arm))
        with ZipFile(self.bundle) as package:
            self.assertEqual(set(package.namelist()), {MANIFEST, *FILES, CAMPAIGN, *COMPARISONS, SUPERVISOR})
            cursor = json.loads(package.read(SUPERVISOR))
        cursor["generation"] += 1
        malformed = self.copy_package("wrong-cursor.zip", {SUPERVISOR: json.dumps(cursor).encode()}, rehash=True)
        with self.assertRaisesRegex(ValueError, "cursor"):
            import_lab(malformed, self.root / "invalid-cursor")
        self.assertFalse((self.root / "invalid-cursor").exists())

    def test_followup_keeps_frozen_confirmation_and_resumes_only_the_qualified_arm(self):
        run = self.root / "followup"
        source = {"kind": "archive", "branch_id": self.child_id, "branch_name": "Transfer test",
                  "origin_branch_id": self.branch, "generation": 1}
        real_validate = campaign.evolution.validate_champion

        def deterministic_decision(directory, through):
            def validate(*args, **kwargs):
                report = real_validate(*args, **kwargs)
                report["passed"] = directory.name == "trace"
                return report
            with patch.object(campaign.evolution, "validate_champion", validate):
                return run_campaign(directory, through)

        with (patch("wisp.campaign.STAGE_BUDGET", 2), patch("wisp.campaign.VALIDATE_EVERY", 1),
              patch("wisp.campaign_watch.BATCH", 1), patch.object(campaign, "run_campaign", deterministic_decision)):
            init_campaign(run, self.archive.snapshot(self.child_id, 1), source, name="Primary", seed=15)
            for _ in range(5):
                status = watch_tick(run)
            self.assertEqual((status["phase"], status["stop_round"], status["qualified"]), ("followup", 2, ["trace"]))
            self.assertEqual(status["arms"]["trace"]["generation"], 3)
            self.bundle = export_lab(self.archive, self.child_id, self.child.checkpoint(), self.personal.checkpoint(),
                                     self.root / "exports", campaign_path=run / "campaign.sqlite3")
            imported = import_lab(self.bundle, self.root / "transferred-followup") / CAMPAIGN_DIR
            self.assertEqual(read_watch_status(imported), status)
            later = watch_tick(imported)
            self.assertEqual(later["comparison_result"], status["comparison_result"])
            self.assertEqual([later["arms"][arm]["generation"] for arm in ("legacy", "trace")], [2, 4])
        with ZipFile(self.bundle) as package:
            cursor = json.loads(package.read(SUPERVISOR))
        cursor["comparison_result"]["qualified"] = cursor["qualified"] = ["legacy"]
        bad = self.copy_package("ineligible-arm.zip", {SUPERVISOR: json.dumps(cursor).encode()}, rehash=True)
        with self.assertRaisesRegex(ValueError, "confirmations"):
            import_lab(bad, self.root / "bad-qualification")

    def test_latest_memory_study_round_trip_and_path_injection_rejection(self):
        source = {"branch_id": self.child_id, "branch_name": "Transfer test",
                  "generation": 1, "origin_branch_id": self.branch}
        report = run_next_plan(self.archive.snapshot(self.child_id, 1), repeats=1, member_ids=["fixed:0"])
        record = {"source": source, "finished_at": "2026-11-26T18:15:01+00:00", "report": report}
        bundle = export_lab(self.archive, self.child_id, self.child.checkpoint(),
                            self.personal.checkpoint(), self.root / "exports",
                            memory_study={**record, "path": str(self.root / "local-only.json")})
        with ZipFile(bundle) as saved:
            summary = json.loads(saved.read(SUMMARY))
        self.assertEqual(summary["memory_study"], {"version": 1, **record})
        imported = import_lab(bundle, self.root / "with-memory")
        sidecars = list((imported / (PERSONAL + ".next-plan")).glob("study-*.json"))
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(json.loads(sidecars[0].read_bytes()), record)
        self.assertTrue(sidecars[0].name.startswith("study-20261126T181501000000Z-"))
        # The reader ignores no extra fields, especially user-supplied destinations.
        summary["memory_study"]["path"] = "../overwrite.json"
        invalid = self.copy_package("bad-memory.zip", {SUMMARY: json.dumps(summary).encode()}, rehash=True)
        with self.assertRaisesRegex(ValueError, "envelope"):
            import_lab(invalid, self.root / "invalid-memory")
        del summary["memory_study"]["path"]
        summary["memory_study"]["report"]["manifest"]["source_path"] = "C:/private.json"
        invalid = self.copy_package("bad-memory-source.zip", {SUMMARY: json.dumps(summary).encode()}, rehash=True)
        with self.assertRaisesRegex(ValueError, "file reference"):
            import_lab(invalid, self.root / "invalid-memory")
        self.assertFalse((self.root / "invalid-memory").exists())
