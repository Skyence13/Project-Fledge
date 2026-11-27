"""Trial-by-trial view of the same binary reversal experiment as the batch runner."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import random
from statistics import mean

from .core import Genome, Wisp, random_genome
from .experiment import (CONDITIONS, PROBE_ROUNDS, TEACH_ROUNDS, cue_order, derived_seed,
                         identifier, lesson_bank, next_generation)


@dataclass(frozen=True)
class Stage:
    id: str
    label: str
    description: str
    trials: int
    teaching: bool = False
    reversed_rule: bool = False
    scored: bool = False


PLAN = (
    Stage("before_teaching", "Baseline", "Observe first responses before teaching.", 2 * PROBE_ROUNDS),
    Stage("teach", "Teach", "Give feedback on the first cue-to-gaze rule.", 2 * TEACH_ROUNDS, teaching=True),
    Stage("after_teaching", "Recall", "Score the learned rule with feedback removed.", 2 * PROBE_ROUNDS, scored=True),
    Stage("before_reteaching", "Reverse rule", "Measure first responses after swapping the rule.", 2 * PROBE_ROUNDS, reversed_rule=True),
    Stage("reteach", "Reteach", "Give feedback using the opposite rule.", 2 * TEACH_ROUNDS, teaching=True, reversed_rule=True),
    Stage("after_reteaching", "Reversal recall", "Score the reversed rule with feedback removed.", 2 * PROBE_ROUNDS, reversed_rule=True, scored=True),
)
HISTORY_LIMIT = 64
PROBE_PHASES = tuple(stage.id for stage in PLAN if not stage.teaching)


@dataclass
class Member:
    condition: str
    slot: int
    genome: Genome
    fingerprint: str
    wisp: Wisp
    choices: int = 0
    feedback: int = 0
    probes: int = 0
    correct: int = 0
    last_cue: int | None = None
    last_action: int | None = None

    @property
    def id(self):
        return f"{self.condition}:{self.slot}"

    @property
    def fitness(self):
        return self.correct / self.probes if self.probes else None

    def inspect(self):
        return {
            "id": self.id, "label": f"{self.condition.replace('_', ' ').title()} {self.slot + 1}",
            "condition": self.condition, "slot": self.slot, "genome": self.fingerprint,
            "nodes": len(self.genome.nodes), "edges": len(self.genome.edges),
            "adaptive": sum(node.adaptation > 0 for node in self.genome.nodes),
            "learning_rate": self.genome.learning_rate, "fitness": self.fitness,
            "correct": self.correct, "probes": self.probes, "choices": self.choices,
            "feedback": self.feedback, "last_cue": self.last_cue, "last_action": self.last_action,
        }


class LabController:
    """One seeded experiment. Its owner serializes calls and supplies the clock."""

    def __init__(self, seed=7, per_condition=4):
        if type(per_condition) is not int or not 2 <= per_condition <= 16:
            raise ValueError("Choose 2 to 16 candidates per condition.")
        self.seed = seed
        self.evolution_seed = seed
        self.per_condition = per_condition
        self.mode = "manual"
        self.speed = "realtime"
        self.preferred_rate = None
        self.running = False
        self.auto_advance = False
        self.history = []
        self.completed = 0
        self.total_choices = 0
        self.total_feedback = 0
        self.selected = "fixed:0"
        founders = [random_genome(random.Random(derived_seed(seed, "founder", i)))
                    for i in range(per_condition)]
        self._begin_generation({name: founders for name in CONDITIONS})

    def _begin_generation(self, genomes):
        self.generation = self.completed + 1
        self.lessons = lesson_bank(self.seed, f"train-{self.generation - 1}", 1)
        self.schedule = []
        for lifetime, lesson in enumerate(self.lessons):
            for phase, stage in enumerate(PLAN):
                cues = cue_order(lesson.seed, stage.id, stage.trials // 2)
                for index, cue in enumerate(cues):
                    target = cue ^ lesson.assignment ^ int(stage.reversed_rule)
                    self.schedule.append((lifetime, phase, index, cue, target))
        self.step = 0
        self.manual_overrides = 0
        # Correct / observed choices pooled across the cohort, excluding teaching trials.
        self.phase_counts = {name: [0, 0] for name in PROBE_PHASES}
        self.lifetime = 0
        lifetime_seed = derived_seed(self.lessons[0].seed, "lifetime")
        self.members = [Member(name, index, genome, identifier(genome), Wisp(genome, lifetime_seed))
                        for name in CONDITIONS for index, genome in enumerate(genomes[name])]

    @property
    def awaiting_next(self):
        return self.step == len(self.schedule)

    def _summary(self):
        scores = [m.fitness for m in self.members if m.probes]
        return {"mean_fitness": mean(scores) if scores else None,
                "best_fitness": max(scores, default=None),
                "unique_genomes": len({m.fingerprint for m in self.members})}

    def _finish_generation(self):
        self.completed += 1
        self.history.append({
            "generation": self.generation, **self._summary(), "manual_overrides": self.manual_overrides,
            "phase_counts": deepcopy(self.phase_counts),
            "by_condition": {name: mean(m.fitness for m in self.members if m.condition == name)
                             for name in CONDITIONS},
        })
        self.history = self.history[-HISTORY_LIMIT:]
        if not self.auto_advance:
            self.running = False

    def _next_generation(self):
        if not self.awaiting_next:
            raise ValueError("Finish the current generation before breeding its offspring.")
        genomes = {}
        for name, flags in CONDITIONS.items():
            candidates = [m for m in self.members if m.condition == name]
            genomes[name] = next_generation([m.genome for m in candidates],
                                           [m.fitness for m in candidates],
                                           flags, self.evolution_seed, self.generation - 1)
        self._begin_generation(genomes)

    def _advance(self, manual_target=None):
        lifetime, phase, _, cue, lesson_target = self.schedule[self.step]
        stage = PLAN[phase]
        if manual_target is not None and not stage.teaching:
            raise ValueError("Feedback is disabled during this probe.")
        if lifetime != self.lifetime:
            self.lifetime = lifetime
            lifetime_seed = derived_seed(self.lessons[lifetime].seed, "lifetime")
            for member in self.members:
                member.wisp = Wisp(member.genome, lifetime_seed)
        target = lesson_target if manual_target is None else manual_target
        self.manual_overrides += int(target != lesson_target)
        for member in self.members:
            action = member.wisp.choose(cue)
            member.last_cue, member.last_action = cue, action
            member.choices += 1
            if stage.teaching:
                member.wisp.feedback(1 if action == target else -1)
                member.feedback += 1
            if stage.scored:
                member.probes += 1
                member.correct += int(action == lesson_target)
            if not stage.teaching:
                self.phase_counts[stage.id][0] += int(action == lesson_target)
                self.phase_counts[stage.id][1] += 1
        self.total_choices += len(self.members)
        if stage.teaching:
            self.total_feedback += len(self.members)
        self.step += 1
        if self.awaiting_next:
            self._finish_generation()

    def tick(self):
        """One paced cohort trial. Paused calls consume no schedule or random state."""
        if self.mode != "auto" or not self.running:
            return
        if self.awaiting_next:
            if not self.auto_advance:
                self.running = False
                return
            self._next_generation()
        self._advance()

    def checkpoint(self):
        """Archive this exact timeline tip, including the RNG and learned weights of each Wisp."""
        return {
            "version": 1, "seed": self.seed, "evolution_seed": self.evolution_seed,
            "per_condition": self.per_condition, "generation": self.generation, "step": self.step,
            "lifetime": self.lifetime, "selected": self.selected, "mode": self.mode,
            "speed": self.speed, "preferred_rate": self.preferred_rate,
            "running": self.running, "auto_advance": self.auto_advance,
            "phase_counts": deepcopy(self.phase_counts),
            "manual_overrides": self.manual_overrides, "total_choices": self.total_choices,
            "total_feedback": self.total_feedback, "history": deepcopy(self.history),
            "members": [{"id": m.id, "choices": m.choices, "feedback": m.feedback,
                         "probes": m.probes, "correct": m.correct, "wisp": m.wisp.checkpoint()}
                        for m in self.members],
        }

    @classmethod
    def restore(cls, data):
        if data.get("version") != 1:
            raise ValueError("Unsupported laboratory checkpoint.")
        lab = cls(data["seed"], data["per_condition"])
        generation, step = data["generation"], data["step"]
        if (type(generation) is not int or generation < 1 or type(step) is not int
                or not 0 <= step <= len(lab.schedule)):
            raise ValueError("Invalid generation or trial in laboratory checkpoint.")
        if [row["id"] for row in data["members"]] != [m.id for m in lab.members]:
            raise ValueError("Invalid population in laboratory checkpoint.")
        wisps = [Wisp.restore(row["wisp"]) for row in data["members"]]
        genomes = {name: [] for name in CONDITIONS}
        for member, wisp in zip(lab.members, wisps):
            genomes[member.condition].append(wisp.genome)
        lab.completed = generation - 1
        lab._begin_generation(genomes)
        lab.step = step
        lab.lifetime = data["lifetime"]
        if lab.lifetime != lab.schedule[max(0, step - 1)][0]:
            raise ValueError("Invalid lifetime in laboratory checkpoint.")
        lab.completed += int(lab.awaiting_next)
        lab.evolution_seed = data["evolution_seed"]
        lab.history = deepcopy(data["history"][-HISTORY_LIMIT:])
        lab.total_choices = data["total_choices"]
        lab.total_feedback = data["total_feedback"]
        lab.manual_overrides = data["manual_overrides"]
        if "phase_counts" in data:
            counts = data["phase_counts"]
            if not isinstance(counts, dict) or set(counts) != set(PROBE_PHASES):
                raise ValueError("Invalid phase evidence in laboratory checkpoint.")
            for stage_index, stage in enumerate(PLAN):
                if stage.teaching:
                    continue
                seen = sum(row[1] == stage_index for row in lab.schedule[:step]) * len(lab.members)
                values = counts[stage.id]
                if (not isinstance(values, (tuple, list)) or len(values) != 2
                        or any(type(n) is not int for n in values)
                        or not 0 <= values[0] <= values[1] <= seen or values[1] % len(lab.members)):
                    raise ValueError("Invalid phase counts in laboratory checkpoint.")
                lab.phase_counts[stage.id] = list(values)
        for member, row, wisp in zip(lab.members, data["members"], wisps):
            member.wisp = wisp
            member.last_cue, member.last_action = wisp.last_cue, wisp.last_action
            member.choices, member.feedback = row["choices"], row["feedback"]
            member.probes, member.correct = row["probes"], row["correct"]
            if (member.choices != step or not 0 <= member.correct <= member.probes <= step
                    or not 0 <= member.feedback <= step):
                raise ValueError("Invalid trial counts in laboratory checkpoint.")
        lab.command({"action": "select", "id": data["selected"]})
        settings = {key: data[key] for key in ("mode", "speed", "running", "auto_advance")}
        lab._configure(dict(settings, rate=data.get("preferred_rate")))
        return lab

    def fork(self, branch_key: str):
        """Create fresh offspring of the evaluated cohort in a new reproduction RNG stream."""
        if not self.awaiting_next:
            raise ValueError("Branches start from a completed generation.")
        if not isinstance(branch_key, str) or not branch_key.strip():
            raise ValueError("A new branch requires a unique branch key.")
        child = type(self).restore(self.checkpoint())
        child.evolution_seed = derived_seed(self.evolution_seed, "branch", self.generation, branch_key)
        child.running = False
        child._next_generation()
        return child

    def _configure(self, options):
        if not options or options.keys() - {"mode", "speed", "rate", "running", "auto_advance"}:
            raise ValueError("Unknown or empty lab configuration.")
        mode = options.get("mode", self.mode)
        speed = options.get("speed", self.speed)
        rate = options.get("rate", self.preferred_rate)
        if rate is not None and (type(rate) is not int or not 1 <= rate <= 65536):
            raise ValueError("Choose an automatic rate or an integer from 1 to 65536 trials per second.")
        auto_advance = options.get("auto_advance", self.auto_advance)
        running = options.get("running", self.running if mode == "auto" else False)
        if mode not in ("auto", "manual") or speed not in ("realtime", "accelerated"):
            raise ValueError("Choose a valid training mode and speed.")
        if type(auto_advance) is not bool or type(running) is not bool:
            raise ValueError("Run and auto-advance settings must be booleans.")
        if running and mode != "auto":
            raise ValueError("Switch to automatic training to run the clock.")
        if running and self.awaiting_next and not auto_advance:
            if options.get("running") is True:
                raise ValueError("Choose Next generation or enable auto-advance to continue.")
            running = False
        self.mode, self.speed, self.auto_advance, self.running = mode, speed, auto_advance, running
        self.preferred_rate = rate

    def command(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("action"), str):
            raise ValueError("Provide a lab action.")
        action = data["action"]
        args = {key: value for key, value in data.items() if key != "action"}
        if action == "configure":
            self._configure(args)
        elif action == "step" and not args.keys() - {"target"}:
            if self.mode != "manual":
                raise ValueError("Switch to manual training to step a trial.")
            if self.awaiting_next:
                raise ValueError("Choose Next generation to continue.")
            if "target" in args and (type(args["target"]) is not int or args["target"] not in (0, 1)):
                raise ValueError("A teaching target must be left (0) or right (1).")
            self._advance(args.get("target"))
        elif action == "select" and args.keys() == {"id"}:
            if not any(m.id == args["id"] for m in self.members):
                raise ValueError("Choose a variation from the current population.")
            self.selected = args["id"]
        elif action == "next_generation" and not args:
            self._next_generation()
        elif action == "reset" and not args:
            self.__init__(self.seed, self.per_condition)
        else:
            raise ValueError("Unknown lab command or field.")

    def state(self):
        finished = self.awaiting_next
        lifetime, phase, index, cue, target = self.schedule[-1 if finished else self.step]
        stage = PLAN[phase]
        variants = [member.inspect() for member in self.members]
        scores = sorted((v["fitness"] for v in variants if v["fitness"] is not None), reverse=True)
        ranks = {}
        for rank, score in enumerate(scores, 1):
            ranks.setdefault(score, rank)
        for variant in variants:
            variant["rank"] = ranks.get(variant["fitness"])
        return {
            "seed": self.seed, "evolution_seed": str(self.evolution_seed),
            "mode": self.mode, "speed": self.speed, "preferred_rate": self.preferred_rate, "running": self.running,
            "auto_advance": self.auto_advance, "awaiting_next": finished, "complete": finished,
            "generation": self.generation, "max_generations": None, "selected": self.selected,
            "population": len(variants), "step": self.step, "steps_per_generation": len(self.schedule),
            "lifetime": lifetime + 1, "lifetimes_per_generation": len(self.lessons),
            "phase": stage.id, "phase_index": phase, "phase_step": stage.trials if finished else index,
            "phase_total": stage.trials, "next_cue": None if finished else cue,
            "next_target": None if finished else target, "teaching": stage.teaching and not finished,
            "plan": [asdict(s) for s in PLAN], "variants": variants, "history": list(self.history),
            "stats": {**self._summary(), "total_choices": self.total_choices,
                      "total_feedback": self.total_feedback, "completed_generations": self.completed,
                      "manual_overrides": self.manual_overrides},
        }
