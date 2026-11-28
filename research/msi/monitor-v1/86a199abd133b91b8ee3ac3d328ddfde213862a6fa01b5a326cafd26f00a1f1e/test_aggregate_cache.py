import json
from hashlib import sha256
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aggregate_cache import BREEDING, UNSCORED, cached, collect, refresh
from fledge_events import emit
import relay


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def collect(self, _):
        return {"activity": {"state": "waiting", "label": "Waiting"}, "campaign": {"arms": []}, "history": [],
                "last_completed": {"name": "Predecessor", "completed_at": "2020-01-01T00:00:00+00:00"}}

    def event(self, phase, complete):
        emit(phase, "Fixture study", "Does the fixture recover?", complete, 2, directory=self.directory)

    def test_legacy_study_uses_verified_current_scores_only(self):
        child = self.directory / "closed"
        child.mkdir()
        def store(path, record):
            content = json.dumps(record).encode()
            path.write_bytes(content)
            return sha256(content).hexdigest()
        reports = {}
        for split, n in (("validation", 8), ("confirmation", 16)):
            name = split + ".json"
            reports[name] = store(child / name, {"bank": {"split": split, "lifetimes": n, "held_out": {
                "control": {"visible": {"correct": n//2, "total": n}},
                "trained": {"visible": {"correct": n-1, "total": n}}}}})
        review_hash = store(child / "review.json", {"reports": reports, "by_split": {
            "validation": {}, "confirmation": {}}, "result": "measured", "completed_at": "2026-01-01T00:00:00Z"})
        closed = {"state": "bounded_study_complete", "active_path": "closed", "protocol": "fixture",
                  "review_sha256": review_hash, "reports": reports, "controls": ["control", "trained"],
                  "thresholds": {"contrasts": {}}, "objective": "Test a fixture."}
        closed_hash = store(child / "registration.json", closed)
        registry = self.directory / "active_research.json"
        store(registry, closed)
        with patch("aggregate_cache.dashboard_summary.read_summary",
                   side_effect=lambda _: {"campaign": {"generation": 1}, "history": [], "study": {"splits": {}}}), \
                patch("aggregate_cache.population", return_value=None):
            result = collect(self.directory)
            for split, n in (("validation", 8), ("confirmation", 16)):
                bank = result["study"]["splits"][split]
                self.assertEqual(bank, result["last_completed"]["splits"][split])
                self.assertEqual([row["score"]["correct"] for row in bank["comparison"]], [n//2, n-1])
            store(registry, {"state": "preregistered", "active_path": "next", "protocol": "next-question",
                             "objective": "New fixture", "previous_review": "closed/registration.json",
                             "previous_review_sha256": closed_hash})
            result = collect(self.directory)
            self.assertEqual(result["study"]["name"], "next question")
            self.assertEqual(result["study"]["splits"], {})
            self.assertIsNone(result["study"]["result"])
            self.assertEqual(result["last_completed"]["splits"]["confirmation"]["lifetimes"], 16)
            unscored_dir = self.directory / "next"
            unscored_dir.mkdir()
            empty = {"reports": {}, "by_split": {}, "retained_both": None,
                     "result": "unscored_insufficient_matching_support", "unscored_reason": "empty_eligible_pool",
                     "completed_at": "2026-01-01T02:00:00Z"}
            cursor = json.loads(registry.read_bytes())
            cursor.update(empty, state=UNSCORED, review_sha256=store(unscored_dir / "review.json", empty))
            store(registry, cursor)
            result = collect(self.directory)
            self.assertEqual(result["study"]["score_status"], "unscored")
            self.assertEqual(result["study"]["scored_banks"], 0)
            self.assertEqual(result["study"]["splits"], {})
            self.assertIsNone(result["study"]["retained_both"])
            self.assertEqual(result["last_completed"]["name"], "fixture")
            cursor_sha = store(unscored_dir / "review-at-completion.json", cursor)
            store(registry, {"state": "preregistered", "active_path": "breeding", "protocol": BREEDING,
                             "study_kind": "evolution", "objective": "Select on blank trials.", "generation_budget": 4,
                             "previous_review": "next/review-at-completion.json", "previous_review_sha256": cursor_sha})
            result = collect(self.directory)
            self.assertEqual(result["study"]["splits"], {})
            self.assertEqual(result["last_completed"]["name"], "fixture")
            self.assertIsNone(result["specimen"])
            self.assertIsNone(result["evolution"]["population"])
            (child / "confirmation.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                collect(self.directory)

    def test_breeding_aggregate_separates_population_arms_sides_and_retention(self):
        child = self.directory / "breeding"
        child.mkdir()
        def write(path, record):
            raw = json.dumps(record).encode()
            path.write_bytes(raw)
            return sha256(raw).hexdigest()
        sample = dict(count=16, generation=4, mean_accuracy=.67, accuracy_sd=.06,
                      mean_neurons=6, mean_connections=11, raw_ids="private-identity")
        write(child / "training-progress.json", dict(generation=4, generation_budget=4, population=sample))
        reports = {}
        def cell(n):
            return {"correct": n, "total": 10}
        def old(n):
            return {"metrics": {"mixed_recall": cell(n), "old_skills": {"immediate": cell(n), "reversal": cell(n)},
                              **{k: {str(i): cell(n) for i in (1,3,5)} for k in ("fixed_recall","fixed_one_shot")}}}
        for split in ("validation","confirmation"):
            held = {arm: {cond: cell(n) for cond in ("blank","left_target","right_target","visible","dark_only")}
                    for arm,n in (("finalist",7),("frozen_ancestor",6),("last_visible_decoder",10))}
            metrics = dict(split=split, lifetimes=32 if split=="validation" else 64, held_out=held,
                           fresh_skills=old(9), ancestor_fresh_skills=old(8), founder_fingerprint="private-identity",
                           post_practice={"rates": {a: {"recall": .9, "reversal": .8} for a in ("finalist","frozen_ancestor")}})
            reports[split+".json"] = write(child / (split+".json"), {"bank":metrics})
        review = dict(reports=reports, by_split={"validation":{}, "confirmation":{}}, retained_both=True,
                      result="criteria_met", completed_at="2026-01-01T02:00:00Z")
        reg = dict(protocol=BREEDING, active_path="breeding", study_kind="evolution", generation_budget=4,
                   objective="Select on blank trials.", state="bounded_study_complete", reports=reports,
                   review_sha256=write(child/"review.json",review))
        write(self.directory/"active_research.json",reg)
        with patch("aggregate_cache.dashboard_summary.read_summary", return_value={"campaign":{"generation":60}}), \
                patch("aggregate_cache.population", return_value={"count":12}):
            result=collect(self.directory)
            self.assertEqual(result["population"]["count"],12)
            self.assertEqual(result["evolution"]["population"]["count"],16)
            self.assertIn("Training generation 4 of 4",result["study"]["objective"])
            bank=result["study"]["splits"]["confirmation"]
            rows={r["label"]:r["score"]["correct"] for r in bank["comparison"]}
            self.assertEqual(rows["Selected offspring, one blank, right target"],7)
            self.assertEqual(rows["Frozen ancestor, one blank, left target"],6)
            self.assertEqual(rows["Last-visible decoder, one blank, pooled"],10)
            self.assertEqual(len(rows),15)
            self.assertEqual(bank["lifetimes"],64)
            skills={r["label"]:r for r in bank["skill_rows"]}
            self.assertEqual(skills["Selected offspring, fresh immediate recall"]["score"]["correct"],9)
            self.assertEqual(skills["Frozen ancestor, fresh immediate recall"]["score"]["correct"],8)
            self.assertEqual(skills["Selected offspring, post-practice reversal"]["rate"],.8)
            self.assertNotIn("private-identity",json.dumps(result))

    def test_generation_event_uses_generations(self):
        emit("evolving","Fixture","One-blank breeding",1,4,directory=self.directory)
        refresh(self.directory,collect_base=self.collect)
        doc=json.loads(cached(self.directory))
        self.assertEqual(doc["progress"]["unit"],"generations")
        self.assertIn("1/4 generations",doc["activity"]["label"])
        emit("review_complete","Fixture","One-blank breeding",2,2,directory=self.directory)
        refresh(self.directory,collect_base=self.collect)
        self.assertEqual(json.loads(cached(self.directory))["progress"]["unit"],"banks")

    def test_quick_study_history_restart_and_retry(self):
        stages = [("running_validation", 0), ("running_confirmation", 1), ("reviewing_results", 2)]
        for stage, count in stages:
            self.event(stage, count)
            self.event(stage, count)
        refresh(self.directory, collect_base=self.collect)
        self.assertFalse(refresh(self.directory, collect_base=self.collect))
        current = json.loads(cached(self.directory))
        self.assertEqual([e["phase"] for e in current["transitions"]], [s for s, _ in reversed(stages)])
        self.assertEqual(current["last_completed"]["name"], "Predecessor")
        endpoint = "https://fixture.invalid/api/summary"
        sent = []
        def healthy(opener, method, url, token, body=None):
            if method == "PUT":
                sent.append(body)
                return 204, b""
            return 200, sent[-1]
        with patch.object(relay, "request", return_value=(0, b"")):
            self.assertEqual(relay.publish(None, endpoint, "fixture", True, self.directory)["put"], 0)
        with patch.object(relay, "request", side_effect=healthy):
            for stage, _ in stages:
                self.assertEqual(relay.publish(None, endpoint, "fixture", True, self.directory)["verified"], 1)
                self.assertEqual(json.loads(sent[-1])["progress"]["phase"], stage)
            self.assertEqual(relay.publish(None, endpoint, "fixture", False, self.directory), {"idle": 1})
            self.assertEqual(json.loads(sent[-1]), current)

    def test_pause_integrity_and_heartbeat(self):
        self.event("running_validation", 0)
        def paused(root):
            base = self.collect(root)
            base["campaign"]["paused"] = True
            return base
        refresh(self.directory, collect_base=paused)
        good = json.loads(cached(self.directory))
        self.assertEqual(good["progress"]["phase"], "paused")
        def corrupt(_):
            raise ValueError("fixture hash differs")
        refresh(self.directory, force=True, collect_base=corrupt)
        stopped = json.loads(cached(self.directory))
        self.assertEqual(stopped["activity"]["state"], "blocked")
        self.assertEqual(stopped["last_completed"]["name"], "Predecessor")
        self.assertEqual(stopped["observed_at"], good["observed_at"])
        self.assertGreater(stopped["heartbeat_at"], good["heartbeat_at"])
        self.assertEqual(len(stopped["transitions"]), 1)
        with self.assertRaises(ValueError):
            self.event("running_confirmation", 3)

    def test_two_distinct_uploads_require_exact_private_readback(self):
        endpoint = "https://fixture.invalid/api/summary"
        self.event("running_validation", 0)
        refresh(self.directory, collect_base=self.collect)
        first = cached(self.directory)
        self.event("running_confirmation", 1)
        refresh(self.directory, collect_base=self.collect)
        second = cached(self.directory)
        with patch.object(relay, "request", side_effect=[
                (204, b""), (200, b"{}"), (204, b""), (200, first), (204, b""), (200, second)]) as request:
            results = [relay.publish(None, endpoint, "fixture", True, self.directory) for _ in range(3)]
            self.assertEqual([r["verified"] for r in results], [0, 1, 1])
            self.assertEqual([call.args[-1] for call in request.call_args_list[::2]], [first, first, second])
        with closing(sqlite3.connect(self.directory / "monitor.sqlite3")) as db:
            stamps = [r[0] for r in db.execute("SELECT observed_at FROM relay_readbacks ORDER BY snapshot_id")]
            self.assertEqual(stamps, [json.loads(body)["observed_at"] for body in (first, second)])
            self.assertLess(stamps[0], stamps[1])
            self.assertEqual(db.execute("SELECT count(*) FROM snapshots WHERE id>(SELECT delivered FROM delivery)").fetchone()[0], 0)

    def test_retry_history_and_private_diagnostics(self):
        monitor = self.directory / "monitor"
        state = self.directory / "fledge-coordinator-state"
        state.mkdir()
        emit("running_validation", "Fixture", "Question", 0, 2, directory=monitor)
        for _ in range(20):
            for phase in ("preparing_successor", "blocked"):
                emit(phase, "Fixture", "Question", None, None, directory=monitor)
        (state / "status.json").write_text(json.dumps({"status": "Codex invocation incomplete: private error"}))
        refresh(monitor, collect_base=self.collect)
        doc = json.loads(cached(monitor))
        self.assertEqual(doc["activity"]["reason"], "Codex has not returned a decision.")
        self.assertNotIn("private error", str(doc))
        self.assertEqual([e["phase"] for e in doc["transitions"]],
                         ["blocked", "preparing_successor", "running_validation"])
        (state / "status.json").write_text(json.dumps({"status": "unrecognized private error"}))
        refresh(monitor, force=True, collect_base=self.collect)
        self.assertNotIn("reason", json.loads(cached(monitor))["activity"])

    def test_no_scientific_timestamp_until_first_read(self):
        def unavailable(_):
            raise OSError("unreadable")
        refresh(self.directory, collect_base=unavailable)
        doc = json.loads(cached(self.directory))
        self.assertIsNone(doc["observed_at"])
        self.assertTrue(doc["heartbeat_at"])

    def test_latest_decision_on_private_cards_while_upgrade_is_pending(self):
        state=self.directory/"fledge-coordinator-state"
        monitor=self.directory/"monitor"
        state.mkdir()
        for name,data in (("standing-authority",{}),("service-session",{"account":"fixture"}),
                          ("latest-decision",{"study":"old-study","question":"Which side changed?",
                            "notice":"One side gained.","recorded_at":"2026-01-02T00:00:00Z","secret":"private"})):
            (state/(name+".json")).write_text(json.dumps(data))
        emit("blocked","new-study","Which side changed?",None,None,directory=monitor)
        refresh(monitor,collect_base=self.collect)
        doc=json.loads(cached(monitor))
        self.assertIn("waiting for the installed coordinator",doc["activity"]["reason"])
        self.assertIn("One side gained.",doc["last_completed"]["objective"])
        self.assertNotIn("secret",doc["latest_decision"])


if __name__ == "__main__":
    unittest.main()
