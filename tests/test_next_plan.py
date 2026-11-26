from concurrent.futures import CancelledError
from copy import deepcopy
import json
import threading
import unittest

from wisp.lab import LabController
from wisp.next_plan import (_counts, _delayed, _lesson, _retention, ASSIGNMENTS, LaggedAssociation,
                            run_next_plan)


class Recorder:
    """A blind, deterministic actor; grade events expose exactly which choice was graded."""

    def __init__(self):
        self.events = []
        self.index = 0

    def choose(self, cue):
        action = self.index % 2
        self.index += 1
        self.events.append(("choose", cue, action))
        return action

    def feedback(self, signed_grade):
        self.events.append(("feedback", signed_grade))


class NextPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = LabController(seed=13, per_condition=2)
        cls.checkpoint = cls.lab.checkpoint()
        cls.report = run_next_plan(cls.checkpoint, repeats=1, member_ids=["fixed:0", "rules_only:0"])

    def test_scores_have_known_balanced_control_values_and_denominators(self):
        report = self.report
        self.assertEqual(report["settings"]["lifetimes_per_candidate_and_agent"], 28)
        self.assertEqual(len(report["metrics"]), 16)
        self.assertEqual(report["source"]["enrolled"], 2)
        self.assertEqual(report["source"]["unique_genomes"], 1)
        association = report["summary"]["association"]
        self.assertEqual(association["retain_sham_b_first"]["accuracy"], 0.5)
        for key, row in association.items():
            if key != "retain_sham_b_first":
                self.assertEqual(row["accuracy"], 1.0, key)
            for control in ("reflex", "uninformative_feedback", "no_plasticity"):
                self.assertEqual(report["summary"][control][key]["accuracy"], 0.5, (control, key))
            self.assertEqual(report["summary"]["neural"][key]["total"], row["total"] * 2)
        for delay in (0, 1, 3):
            self.assertEqual(association[f"delay_{delay}_first"]["total"], 8)
            self.assertEqual(association[f"delay_{delay}_recall"]["total"], 32)
            self.assertEqual(association[f"delay_{delay}_opposite_recall"]["total"], 16)
        self.assertEqual(association["retain_before_first"]["total"], 8)
        for arm in ("sham", "learn_b"):
            self.assertEqual(association[f"retain_{arm}_a_first"]["total"], 8)
            self.assertEqual(association[f"retain_{arm}_a_recall"]["total"], 32)

    def test_feedback_grades_the_source_choice_after_exactly_the_assigned_number_of_actions(self):
        lesson = _lesson(41, 0)
        for delay in (0, 1, 3):
            for mapping in ASSIGNMENTS:
                agent = Recorder()
                _delayed(_counts(), agent, "neural", lesson, mapping, delay)
                events = iter(agent.events)
                for i, cue in enumerate(lesson["teaching_cues"]):
                    source = next(events)
                    self.assertEqual(source[:2], ("choose", cue))
                    for other in lesson["distractors"][i][:delay]:
                        self.assertEqual(next(events)[:2], ("choose", other))
                    self.assertEqual(next(events), ("feedback", 1 if source[2] == mapping[cue] else -1))
                self.assertEqual([(e[0], e[1]) for e in events], [("choose", c) for c in lesson["probe_cues"]])

    def test_retention_pairs_match_observations_and_probe_a_before_grading_again(self):
        lesson = _lesson(41, 1)
        sham, taught = Recorder(), Recorder()
        for agent, teach_b in ((sham, False), (taught, True)):
            _retention(_counts(), agent, "neural", lesson, (1, 0), 0, teach_b)
        self.assertEqual(sham.events[:17], taught.events[:17])
        self.assertEqual(sham.events[16][:2], ("choose", 0))
        self.assertEqual([x for x in sham.events if x[0] == "choose"],
                         [x for x in taught.events if x[0] == "choose"])
        self.assertEqual(sum(x[0] == "feedback" for x in sham.events), 8)
        self.assertEqual(sum(x[0] == "feedback" for x in taught.events), 16)
        self.assertEqual([x[:2] for x in taught.events[-8:]], [("choose", 0), ("choose", 1)] * 4)

    def test_history_baseline_updates_own_older_action(self):
        agent = LaggedAssociation(91, 1)
        older = agent.choose(0)
        agent.choose(1)
        agent.feedback(-1)
        self.assertEqual(agent.learned, {0: 1 - older})

    def test_determinism_and_independent_source_lifetime(self):
        original = deepcopy(self.checkpoint)
        report = run_next_plan(self.checkpoint, repeats=1, member_ids=["rules_only:0", "fixed:0"])
        self.assertEqual(json.loads(json.dumps(report)), self.report)
        self.assertEqual(original, self.checkpoint)
        other = self.lab.checkpoint()
        other["members"][0]["wisp"]["weights"][0] += 1
        self.assertEqual(self.report, run_next_plan(other, repeats=1, member_ids=["fixed:0", "rules_only:0"]))
        self.assertNotEqual(_lesson(41, 0), _lesson(42, 0))

    def test_limits_progress_and_interruption(self):
        for params in ({"seed": True}, {"seed": 2 ** 31}, {"repeats": 0}, {"repeats": 9},
                       {"member_ids": []}, {"member_ids": ["fixed:0", "fixed:0"]}, {"member_ids": ["bogus"]}):
            with self.assertRaises(ValueError):
                run_next_plan(self.checkpoint, **params)
        cancel = threading.Event()
        progress = []

        def after_member(done, total):
            progress.append((done, total))
            if done == 1:
                cancel.set()

        with self.assertRaises(CancelledError):
            run_next_plan(self.checkpoint, repeats=1, member_ids=["fixed:0", "fixed:1"],
                          progress=after_member, cancel_event=cancel)
        self.assertEqual(progress, [(0, 2), (1, 2)])


if __name__ == "__main__":
    unittest.main()
