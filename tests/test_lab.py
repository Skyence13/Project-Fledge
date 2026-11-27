import json
import unittest

from wisp.experiment import evaluate, lesson_bank
from wisp.lab import LabController


def configure(lab, **settings):
    lab.command({"action": "configure", **settings})


def finish_manually(lab):
    while not lab.awaiting_next:
        lab.command({"action": "step"})


class LabTests(unittest.TestCase):
    def test_completed_generation_is_identical_to_batch_and_retained_for_review(self):
        lab = LabController(seed=13, per_condition=2)
        lessons = lesson_bank(13, "train-0", 1)
        expected = [evaluate(m.genome, lessons)["summary"]["fitness"] for m in lab.members]
        finish_manually(lab)
        state = lab.state()
        self.assertEqual(expected, [v["fitness"] for v in state["variants"]])
        self.assertEqual(state["stats"]["total_choices"], 128 * 8)
        self.assertEqual(state["stats"]["total_feedback"], 64 * 8)
        self.assertEqual(state["stats"]["unique_genomes"], 2)
        self.assertEqual(state["history"][0]["best_fitness"], max(expected))
        self.assertTrue(state["awaiting_next"])
        with self.assertRaises(ValueError):
            lab.command({"action": "step"})
        self.assertEqual(state, lab.state())
        lab.command({"action": "next_generation"})
        offspring = lab.state()
        self.assertEqual(offspring["generation"], 2)
        self.assertEqual(offspring["step"], 0)
        self.assertEqual(offspring["stats"]["total_choices"], 128 * 8)
        self.assertGreater(offspring["stats"]["unique_genomes"], 2)
        for member in lab.members:
            self.assertEqual(member.wisp.trials, 0)

    def test_speed_and_inspection_preserve_outcomes_and_auto_stops_at_generation(self):
        slow = LabController(per_condition=2)
        fast = LabController(per_condition=2)
        idle = fast.state()
        fast.tick()
        self.assertEqual(fast.state(), idle)
        configure(slow, mode="auto", running=True)
        configure(fast, mode="auto", speed="accelerated", running=True)
        fast.command({"action": "select", "id": "rules_only:1"})
        for _ in range(140):
            fast.tick()
            slow.tick()
        self.assertFalse(fast.running)
        self.assertFalse(slow.running)
        self.assertEqual(fast.state()["variants"], slow.state()["variants"])
        self.assertEqual(fast.state()["history"], slow.state()["history"])
        self.assertEqual(fast.selected, "rules_only:1")
        with self.assertRaises(ValueError):
            configure(fast, running=True)
        configure(fast, auto_advance=True, running=True)
        for _ in range(129):
            fast.tick()
        self.assertEqual(fast.generation, 3)
        self.assertEqual(fast.step, 1)
        configure(fast, running=False)
        fast.tick()
        self.assertEqual(fast.step, 1)

    def test_human_can_supply_the_correction_to_every_member(self):
        lab = LabController(per_condition=2)
        with self.assertRaises(ValueError):
            lab.command({"action": "step", "target": 0})
        for _ in range(8):
            lab.command({"action": "step"})
        expected = lab.state()["next_target"]
        other = LabController.restore(lab.checkpoint())
        lab.command({"action": "step", "target": 1 - expected})
        other.command({"action": "step", "target": expected})
        self.assertEqual(lab.step, 9)
        self.assertEqual(lab.state()["stats"]["manual_overrides"], 1)
        for manual, planned in zip(lab.members, other.members):
            self.assertEqual(manual.last_action, planned.last_action)
            self.assertNotEqual(manual.wisp.checkpoint()["weights"], planned.wisp.checkpoint()["weights"])

    def test_tip_checkpoint_continues_exactly_at_each_lifetime_boundary(self):
        original = LabController(per_condition=2)
        configure(original, mode="auto", running=True, auto_advance=True)
        for step in range(130):
            if step in (0, 57, 64, 65, 128, 129):
                saved = json.loads(json.dumps(original.checkpoint()))
                restored = LabController.restore(saved)
                self.assertEqual(restored.state(), original.state())
                reference = LabController.restore(saved)
                for _ in range(10):
                    restored.tick()
                    reference.tick()
                self.assertEqual(restored.checkpoint(), reference.checkpoint())
                restored = LabController.restore(saved)
                restored.tick()
                original.tick()
                self.assertEqual(original.checkpoint(), restored.checkpoint())
            else:
                original.tick()

    def test_branch_has_distinct_heritable_mutations_but_the_same_lesson(self):
        parent = LabController(per_condition=2)
        with self.assertRaises(ValueError):
            parent.fork("experiment-b")
        finish_manually(parent)
        before = parent.checkpoint()
        child = parent.fork("experiment-b")
        sibling = parent.fork("experiment-c")
        self.assertEqual(child.checkpoint(), parent.fork("experiment-b").checkpoint())
        self.assertEqual(parent.checkpoint(), before)
        self.assertEqual(sibling.schedule, child.schedule)
        self.assertEqual(child.generation, 2)
        self.assertEqual(child.step, 0)
        self.assertFalse(child.running)
        self.assertNotEqual([m.fingerprint for m in child.members],
                            [m.fingerprint for m in sibling.members])
        self.assertEqual(child.state()["evolution_seed"], str(child.evolution_seed))
        self.assertNotEqual(child.state()["evolution_seed"], sibling.state()["evolution_seed"])
        for m in child.members:
            self.assertEqual(m.choices, 0)
            self.assertEqual(m.wisp.checkpoint()["weights"], [edge.weight for edge in m.genome.edges])

    def test_rejected_commands_and_speed_change_are_atomic(self):
        lab = LabController(per_condition=2)
        original = lab.state()
        for data in ({"action": "configure", "speed": "accelerated", "running": "yes"},
                     {"action": "step", "target": True}, {"action": "select", "id": "missing"},
                     {"action": "next_generation"}):
            with self.assertRaises(ValueError):
                lab.command(data)
            self.assertEqual(lab.state(), original)
        configure(lab, mode="auto", running=True)
        configure(lab, mode="manual")
        self.assertFalse(lab.running)

    def test_preferred_rate_configuration_checkpoint_and_legacy_default(self):
        lab = LabController(per_condition=2)
        configure(lab, rate=37)
        self.assertEqual(lab.state()["preferred_rate"], 37)
        self.assertEqual(LabController.restore(lab.checkpoint()).preferred_rate, 37)
        for invalid in (False, 0, -1, 100000, 1.5, "20"):
            with self.assertRaises(ValueError):
                configure(lab, rate=invalid)
            self.assertEqual(lab.preferred_rate, 37)
        old = lab.checkpoint()
        del old["preferred_rate"]
        self.assertIsNone(LabController.restore(old).preferred_rate)
        configure(lab, rate=None)
        self.assertIsNone(lab.preferred_rate)


if __name__ == "__main__":
    unittest.main()
