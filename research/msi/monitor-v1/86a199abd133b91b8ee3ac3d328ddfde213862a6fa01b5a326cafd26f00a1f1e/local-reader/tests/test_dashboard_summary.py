import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from wisp import campaign, dashboard_summary as dashboard


class DashboardSummaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, path, record):
        dest = self.root / path
        dest.parent.mkdir(exist_ok=True, parents=True)
        dest.write_text(json.dumps(record), encoding="utf-8")

    def watch_state(self):
        self.write("supervisor/state.json", {"version": 1, "phase": "research_review",
                   "generation": 5, "arms": {}, "message": "Ready for review."})

    def test_missing_data_remains_unknown_and_creates_no_files(self):
        result = dashboard.read_summary(self.root)
        self.assertEqual(result["activity"]["state"], "unknown")
        self.assertIsNone(result["study"])
        self.assertFalse(list(self.root.iterdir()))

    def test_completed_study_uses_paired_counts_and_excludes_raw_records(self):
        self.watch_state()
        metadata = {"protocol": "wisp-prior-grade-interference-v1", "active_path": "continuations/prior",
                    "state": "bounded_study_complete", "seeds": [4582], "source_fingerprint": "PRIVATE"}
        self.write("active_research.json", metadata)
        self.write("continuations/prior/preregistration.json", {**metadata, "state": "preregistered"})
        counts = {"given": {"total": 4, "correct": 2, "trials": ["PRIVATE"]},
                  "withheld": {"total": 4, "correct": 3}}
        self.write("continuations/prior/review.json", {"completed_at": "2026-11-27T00:20:00Z",
                   "retained_both": True, "by_split": {"confirmation": {"arms": {"intact": {"accuracy": counts}}}}})
        result = dashboard.read_summary(self.root)
        self.assertEqual(result["activity"]["state"], "awaiting_review")
        bank = result["study"]["splits"]["confirmation"]
        self.assertEqual(bank["delta"], -.25)
        self.assertEqual(bank["given"], dict(correct=2, total=4, accuracy=.5))
        self.assertIsNone(bank["boundary_only"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(result["history"][0]["state"], "bounded_study_complete")
        (self.root / "supervisor" / "paused").touch()
        self.assertEqual(dashboard.read_summary(self.root)["activity"]["state"], "paused")

    def test_stationary_banks_export_only_valid_aggregate_counts(self):
        self.watch_state()
        self.write("active_research.json", {"protocol": "stationary-localization-v1",
                   "state": "bounded_study_complete", "active_path": "continuations/stationary"})
        self.write("continuations/stationary/review.json", {"retained_both": True, "by_split": {
            "validation": {"trials_per_arm": 4, "correct": {"visible": 3, "blank": 2, "dark_only": 2,
                           "decoder_visible": 4, "decoder_blank": 2, "last_seen_blank": 4,
                           "extra": "PRIVATE"}, "bank_seed": "PRIVATE"},
            "confirmation": {"trials_per_arm": 8, "correct": {"visible": 7, "blank": 0,
                             "dark_only": True, "decoder_blank": 9}}}})
        study = dashboard.read_summary(self.root)["study"]
        self.assertNotIn("PRIVATE", json.dumps(study))
        self.assertEqual(study["name"], "Stationary target localization")
        self.assertTrue(study["retained_both"])
        validation = study["splits"]["validation"]["stationary"]
        self.assertEqual(validation["last_seen_blank"], dict(correct=4, total=4, accuracy=1.))
        confirmation = study["splits"]["confirmation"]["stationary"]
        self.assertEqual(confirmation["blank"], dict(correct=0, total=8, accuracy=0.))
        self.assertIsNone(confirmation["dark_only"])
        self.assertIsNone(confirmation["decoder_blank"])
        self.assertIsNone(confirmation["last_seen_blank"])

    def test_one_blank_review_rows_and_pinned_fresh_skills(self):
        child = "continuations/one"
        protocol = "stationary-one-blank-practice-v1"
        self.write("active_research.json", {"protocol": protocol, "active_path": child})
        valid = {"correct": 3, "total": 4, "raw": "PRIVATE"}
        cells = {"accurate": {"blank": valid, "visible": valid}, "yoked": {"blank": valid},
                 "nonplastic": {"visible": {"correct": 0, "total": 512},
                                "blank": {"correct": 3, "total": 513}},
                 "PRIVATE": {"blank": valid}}
        bank = {"held_out": cells, "post_practice": {"rates": {
            "intact": {"recall": .75, "reversal": True}, "nonplastic": {"recall": 2}}}}
        file = "one-blank-practice-validation.json"
        artifact = {"bank": {"fresh_skills": {"metrics": {"mixed_recall": valid,
                    "old_skills": {"reversal": valid}}}, "donors": ["PRIVATE"]}}
        self.write(f"{child}/{file}", artifact)
        hash_value = hashlib.sha256((self.root / child / file).read_bytes()).hexdigest()
        self.write(f"{child}/review.json", {"reports": {file: hash_value},
                   "by_split": {"validation": bank, "confirmation": {"held_out": []}}})
        def splits():
            view = dashboard.read_summary(self.root)["study"]
            self.assertNotIn("PRIVATE", json.dumps(view))
            return view["splits"]
        view = splits()
        rows = view["validation"]["comparison"]
        self.assertEqual([r["label"] for r in rows], ["Accurate feedback, one blank frame",
                         "Yoked feedback, one blank frame", "Accurate feedback, target visible",
                         "Plasticity disabled, target visible"])
        self.assertEqual(rows[-1]["score"], dict(correct=0, total=512, accuracy=0))
        skills = view["validation"]["skill_rows"]
        self.assertEqual([r["label"] for r in skills], ["Fresh reversal", "Fresh mixed-delay recall",
                         "Accurate feedback, post-practice recall"])
        self.assertEqual(skills[-1]["rate"], .75)
        self.assertEqual(view["confirmation"], {"comparison": [], "skill_rows": []})
        self.write(f"{child}/{file}", {})
        self.assertEqual(splits()["validation"]["skill_rows"], [skills[-1]])
        self.write("active_research.json", {"protocol": "unrecognized", "active_path": child})
        self.assertEqual(splits()["validation"], {})

    def test_open_study_needs_runtime_evidence(self):
        self.watch_state()
        self.write("active_research.json", {"active_path": "continuations/new", "state": "running"})
        self.assertEqual(dashboard.read_summary(self.root)["activity"]["state"], "unknown")
        self.write("active_research.json", {"active_path": "../other"})
        with self.assertRaisesRegex(ValueError, "Invalid active study"):
            dashboard.read_summary(self.root)

    def test_marked_study_displays_scores_and_distinct_history_coverage(self):
        self.write("active_research.json", {
            "active_path": "continuations/marked", "protocol": "wisp-marked-recall-v1",
            "thresholds": {"min_conflict_histories": 16},
        })
        cell = {"overall": {"correct": 5, "total": 8}, "conflict": {"correct": 1, "total": 2},
                "conflict_histories": 1, "histories": 4, "observations": ["PRIVATE"]}
        self.write("continuations/marked/review.json", {
            "by_split": {"validation": {"arms": {"intact": {
                "informative": cell, "shuffled": {**cell, "conflict": {"correct": 2, "total": 4}}
            }}}, "confirmation": {"arms": {"intact": {"informative": {
                **cell, "conflict": {"correct": 0, "total": 0}, "conflict_histories": 0
            }}}}}
        })
        study = dashboard.read_summary(self.root)["study"]
        self.assertEqual(study["name"], "Recall with a source marker")
        marked = study["splits"]["validation"]["marked"]
        self.assertEqual(marked["shuffled"]["conflict"], {"correct": 2, "total": 4, "accuracy": .5})
        self.assertEqual((marked["conflict_histories"], marked["histories"], marked["min_conflict_histories"]),
                         (1, 4, 16))
        self.assertIsNone(study["splits"]["confirmation"]["marked"]["informative"]["conflict"])
        self.assertEqual(study["splits"]["confirmation"]["marked"]["conflict_histories"], 0)
        self.assertNotIn("PRIVATE", json.dumps(study))

    def test_practice_projects_only_intact_informative_scores_and_support(self):
        self.write("active_research.json", {
            "active_path": "continuations/practice", "protocol": "wisp-marker-practice-v1",
            "thresholds": {"min_conflict_histories": 16},
        })
        cell = {"overall": {"correct": 3, "total": 4}, "conflict": {"correct": 1, "total": 2},
                "conflict_histories": 1, "histories": 2, "raw": "PRIVATE"}
        self.write("continuations/practice/review.json", {"by_split": {"confirmation": {
            "arms": {"intact": {"accurate": {"informative": cell},
                                "independent": {"informative": {**cell, "conflict_histories": 0,
                                                                "conflict": {"total": 0, "correct": 0}}}}},
            "paired": {"intact": {"informative": {"wins": 1, "losses": 2, "total": 4}}},
        }}})
        result = dashboard.read_summary(self.root)
        practice = result["study"]["splits"]["confirmation"]["practice"]
        self.assertEqual(practice["paired_overall_delta"], -.25)
        self.assertEqual(practice["accurate"]["conflict"]["accuracy"], .5)
        self.assertEqual(practice["independent"]["conflict_histories"], 0)
        self.assertIsNone(practice["independent"]["conflict"])
        self.assertEqual(practice["min_conflict_histories"], 16)
        self.assertNotIn("PRIVATE", json.dumps(result))

    @unittest.skipUnless(os.name == "nt", "Windows SQLite lock observation")
    def test_real_batch_lock_is_distinct_from_leftover_lock_file(self):
        self.watch_state()
        with campaign._batch_lock(self.root):
            self.assertEqual(dashboard.read_summary(self.root)["activity"]["state"], "running")
        self.assertTrue((self.root / "writer.lock").exists())
        self.assertEqual(dashboard.read_summary(self.root)["activity"]["state"], "awaiting_review")
