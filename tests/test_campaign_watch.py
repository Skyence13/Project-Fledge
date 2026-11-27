from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wisp import campaign, campaign_watch as watch
from wisp.lab import LabController


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "campaign"
        self.addCleanup(patch.stopall)
        patch.object(campaign, "VALIDATE_EVERY", 1).start()
        patch.object(campaign, "STAGE_BUDGET", 2).start()
        patch.object(watch, "BATCH", 1).start()
        cohort = LabController(seed=13, per_condition=2).checkpoint()
        source = dict(kind="archive", branch_id="main", branch_name="Original", generation=1, origin_branch_id="main")
        campaign.init_campaign(self.root, cohort, source, name="Primary", seed=628)

    def tick(self, token=None):
        return watch.watch_tick(self.root, tick_id=token)

    def test_duplicate_tick_holds_and_resume(self):
        self.assertIsNone(watch.read_watch_status(self.root))
        watch.set_watch_paused(self.root, True)
        self.assertTrue(self.tick()["paused"])
        self.assertEqual(campaign.read_status(self.root)["generation"], 0)
        watch.set_watch_paused(self.root, False)
        first = self.tick("first")
        self.assertEqual(first["generation"], 1)
        self.assertEqual(first, self.tick("first"))
        campaign.pause_campaign(self.root)
        self.assertEqual(self.tick("another")["generation"], 1)
        watch.set_watch_paused(self.root, True)
        watch.set_watch_paused(self.root, False)
        self.assertEqual(campaign.read_status(self.root)["state"], "paused")

    def test_interrupted_paired_tick_compares_equal_budgets_and_stops(self):
        self.tick()
        self.assertEqual(self.tick()["phase"], "comparison")
        self.assertEqual(campaign.read_status(self.root)["state"], "review")
        watch.set_watch_paused(self.root, True)
        self.assertTrue(self.tick()["paused"])
        self.assertFalse((self.root / "comparison").exists())
        watch.set_watch_paused(self.root, False)
        real = campaign.run_campaign

        def fail_second(directory, through):
            if directory.name == "trace":
                raise RuntimeError("lost power")
            return real(directory, through)

        with patch.object(campaign, "run_campaign", fail_second), self.assertRaisesRegex(RuntimeError, "lost power"):
            self.tick("paired")
        comparison = self.root / "comparison"
        self.assertEqual(campaign.read_status(comparison / "legacy")["generation"], 1)
        self.assertEqual(campaign.read_status(comparison / "trace")["generation"], 0)
        campaign.pause_campaign(comparison / "trace")
        resumed = self.tick()
        self.assertEqual(resumed["arms"]["trace"]["state"], "paused")
        self.assertEqual(resumed["arms"]["legacy"]["generation"], 1)
        campaign.resume_campaign(comparison / "trace")
        resumed = self.tick("retried")
        legacy, trace = (campaign.read_status(comparison / arm) for arm in watch.ARMS)
        self.assertEqual((legacy["generation"], trace["generation"]), (1, 1))
        self.assertEqual(legacy["parent_campaign"], trace["parent_campaign"])
        self.assertEqual(legacy["seed"], trace["seed"])
        self.assertEqual(legacy["enrollment_sha256"], trace["enrollment_sha256"])
        self.assertEqual((legacy["evolve_trace"], trace["evolve_trace"]), (False, True))
        self.assertIn("credit", resumed["arms"]["legacy"]["last_review"])
        done = self.tick()
        self.assertEqual((done["phase"], done["stop_round"]), ("research_review", 2))
        self.assertEqual([item["generation"] for item in done["arms"].values()], [2, 2])
        self.assertEqual(done, self.tick())

    def test_only_confirmed_arm_continues_and_paired_endpoint_stays_frozen(self):
        self.tick()
        self.assertEqual(self.tick()["phase"], "comparison")
        real_run = campaign.run_campaign
        real_validate = campaign.evolution.validate_champion

        def train_arm(directory, through):
            def known_outcome(*args, **kwargs):
                report = real_validate(*args, **kwargs)
                report["passed"] = directory.name == "trace"
                return report
            with patch.object(campaign.evolution, "validate_champion", known_outcome):
                return real_run(directory, through)

        with patch.object(campaign, "run_campaign", train_arm):
            self.tick()
            matched = self.tick()
            self.assertEqual((matched["phase"], matched["qualified"]), ("followup", ["trace"]))
            endpoint = matched["comparison_result"]
            self.assertEqual(endpoint["arms"]["trace"]["last_review"]["decision"], "promote")
            self.assertIsNotNone(endpoint["arms"]["trace"]["last_review"]["confirmation"])
            trace = self.root / "comparison" / "trace"
            campaign.pause_campaign(trace)
            self.assertEqual(self.tick()["arms"]["trace"]["generation"], 2)
            campaign.resume_campaign(trace)
            for _ in range(4):
                done = self.tick()
            self.assertEqual(done["phase"], "research_review")
            self.assertEqual(done["comparison_result"], endpoint)
            self.assertEqual([done["arms"][k]["generation"] for k in ("legacy", "trace")], [2, 6])
            self.assertEqual(done["arms"]["trace"]["state"], "complete")


if __name__ == "__main__":
    unittest.main()
