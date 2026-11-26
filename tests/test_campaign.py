from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wisp import campaign
from wisp.lab import LabController
from wisp.lineage import LineageArchive


class CampaignTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.run = self.directory / "challenge"
        self.archive = self.directory / "original.sqlite3"
        lab = LabController(seed=7, per_condition=2)
        with closing(LineageArchive(self.archive)) as archive:
            archive.start(lab.checkpoint())
            for _ in lab.schedule:
                lab.command({"action": "step"})
            self.source = lab.checkpoint()
            archive.record("main", lab.generation, self.source, lab.history[-1])
            self.branch, _ = archive.fork("main", 1, "Child with inherited G1", self.source,
                                          lambda cp, key: LabController.restore(cp).fork(key).checkpoint())
        self.before = self.archive.read_bytes()
        cohort, self.provenance = campaign.archived_cohort(self.archive, self.branch, 1)
        self.assertEqual(self.source, cohort)

    def enroll(self, path=None, seed=9):
        return campaign.init_campaign(path or self.run, self.source, self.provenance, name="Delayed", seed=seed)

    def rows(self, path):
        with closing(sqlite3.connect(path / "campaign.sqlite3")) as db:
            return list(db.execute("SELECT generation,population,report FROM rounds ORDER BY generation")), \
                db.execute("SELECT population FROM tip").fetchone()[0]

    def test_enrollment_and_restart_replay_are_exact(self):
        status = self.enroll()
        self.assertEqual(status, self.enroll())
        self.assertEqual(status["source"]["origin_branch_id"], "main")
        self.assertEqual(status["source"]["branch_id"], self.branch)
        with self.assertRaisesRegex(ValueError, "different campaign"):
            self.enroll(seed=10)
        other = self.directory / "copy"
        self.enroll(other)
        campaign.run_campaign(self.run, 1)
        with patch("wisp.campaign.evolution.breed_population", side_effect=ValueError("interrupted")):
            with self.assertRaisesRegex(ValueError, "interrupted"):
                campaign.run_campaign(self.run, 2)
        self.assertEqual(campaign.read_status(self.run)["generation"], 1)
        self.assertEqual(campaign.run_campaign(self.run, 2), campaign.run_campaign(self.run, 2))
        campaign.run_campaign(other, 2)
        self.assertEqual(self.rows(self.run), self.rows(other))
        self.assertEqual(self.archive.read_bytes(), self.before)
        with self.assertRaisesRegex(ValueError, "completed generation"):
            campaign.archived_cohort(self.archive, self.branch, 2)

    def test_concurrent_worker_and_operator_pause(self):
        self.enroll()
        with campaign._batch_lock(self.run):
            with self.assertRaisesRegex(ValueError, "Another worker"):
                campaign.run_campaign(self.run, 1)
            self.assertEqual(campaign.read_status(self.run)["state"], "ready")
        breed = campaign.evolution.breed_population

        def pause_then_breed(*args, **kwargs):
            campaign.pause_campaign(self.run)
            return breed(*args, **kwargs)

        with patch("wisp.campaign.evolution.breed_population", side_effect=pause_then_breed):
            status = campaign.run_campaign(self.run, 2)
        self.assertEqual((status["state"], status["generation"]), ("paused", 0))
        self.assertEqual(campaign.run_campaign(self.run, 2), status)
        self.assertEqual(campaign.resume_campaign(self.run)["state"], "ready")
        with self.assertRaisesRegex(ValueError, "per batch"):
            campaign.run_campaign(self.run, campaign.MAX_BATCH + 1)
        self.assertEqual(campaign.run_campaign(self.run, 1)["generation"], 1)

    def test_fork_uses_archived_genomes_and_retains_original_source(self):
        original = self.enroll()
        campaign.run_campaign(self.run, 3)
        parent_rows = self.rows(self.run)
        parent_status = campaign.read_status(self.run)
        child = self.directory / "delayed-three"
        status = campaign.fork_campaign(child, self.run, 2, stage=1, name="Longer delay", seed=11)
        evaluated = campaign._unpack(parent_rows[0][1][1])
        self.assertEqual(status["source"], original["source"])
        self.assertEqual(status["enrollment_sha256"], campaign._digest(evaluated))
        self.assertEqual(campaign._unpack(self.rows(child)[1]), evaluated)
        self.assertEqual((status["generation"], status["initial_stage"], status["stage"]), (0, 1, 1))
        self.assertEqual(status["parent_campaign"]["generation"], 2)
        self.assertEqual(campaign.run_campaign(child, 1)["generation"], 1)
        self.assertEqual(self.rows(self.run), parent_rows)
        self.assertEqual(campaign.read_status(self.run), parent_status)
        with self.assertRaisesRegex(ValueError, "different campaign"):
            campaign.fork_campaign(child, self.run, 1, stage=1, name="Longer delay", seed=11)
        with self.assertRaisesRegex(ValueError, "no evaluated"):
            campaign.fork_campaign(child, self.run, 45, stage=1, name="Longer delay", seed=11)
        with self.assertRaisesRegex(ValueError, "new campaign directory"):
            campaign.fork_campaign(self.run, self.run, 1, stage=1, name="Longer delay", seed=11)

    def test_two_validations_and_fresh_confirmation_per_stage(self):
        calls = []

        def validate(champ, **kwargs):
            calls.append((kwargs["stage"], kwargs["generation"], kwargs["split"], champ["fingerprint"]))
            # First eligible confirmation fails; a new two-review streak is required.
            return {"passed": kwargs["generation"] != 2 or kwargs["split"] != "confirmation"}

        with patch.object(campaign, "VALIDATE_EVERY", 1), patch.object(campaign, "STAGE_BUDGET", 6), \
                patch("wisp.campaign.evolution.validate_champion", side_effect=validate):
            self.enroll()
            failed = campaign.run_campaign(self.run, 2)
            self.assertEqual((failed["stage"], failed["streak"]), (0, 0))
            self.assertEqual(failed["last_review"]["decision"], "confirmation_failed")
            promoted = campaign.run_campaign(self.run, 4)
            self.assertEqual((promoted["stage"], promoted["stage_generations"]), (1, 0))
            terminal = campaign.run_campaign(self.run, 12)
        self.assertEqual(terminal["state"], "complete")
        self.assertEqual(terminal["generation"], 4 + (len(campaign.evolution.STAGES) - 1) * 2)
        self.assertEqual(calls[-2][1], calls[-1][1])
        self.assertEqual(calls[-2][2], "validation")
        self.assertEqual(calls[-1][2], "confirmation")
        self.assertEqual(calls[-2][3], calls[-1][3])
        self.assertEqual(campaign.run_campaign(self.run, 20), terminal)

    def test_paired_trace_branches_share_enrollment_and_lesson_bank(self):
        self.enroll()
        campaign.run_campaign(self.run, 1)
        paths = [self.directory / label for label in ("comparison", "trace")]
        status = [campaign.fork_campaign(path, self.run, 1, stage=0, name=path.name, seed=41, evolve_trace=enabled)
                  for path, enabled in zip(paths, (False, True))]
        self.assertEqual(status[0]["enrollment_sha256"], status[1]["enrollment_sha256"])
        self.assertEqual(status[0]["parent_campaign"], status[1]["parent_campaign"])
        self.assertEqual([s["evolve_trace"] for s in status], [False, True])
        with self.assertRaisesRegex(ValueError, "different campaign"):
            campaign.fork_campaign(paths[0], self.run, 1, stage=0, name=paths[0].name, seed=41, evolve_trace=True)
        for path in paths:
            campaign.run_campaign(path, 1)
        control, trace = (self.rows(path) for path in paths)
        self.assertEqual(control[0], trace[0])  # Round-one parents, per-candidate scores and scientific bank.
        for group in ("fixed", "wiring_only"):
            self.assertEqual(campaign._unpack(control[1])[group], campaign._unpack(trace[1])[group])

    def test_budget_expiry_holds_for_review(self):
        with patch.object(campaign, "VALIDATE_EVERY", 1), patch.object(campaign, "STAGE_BUDGET", 2), \
                patch("wisp.campaign.evolution.validate_champion", return_value={"passed": False}):
            self.enroll()
            held = campaign.run_campaign(self.run, 5)
        self.assertEqual((held["generation"], held["state"], held["last_review"]["decision"]), (2, "review", "review"))
        self.assertEqual(campaign.resume_campaign(self.run), held)
        self.assertEqual(campaign.run_campaign(self.run, 6), held)


if __name__ == "__main__":
    unittest.main()
