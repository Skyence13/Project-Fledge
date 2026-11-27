"""Matched, seeded experiments on learning a two-cue rule during a lifetime."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
from statistics import mean
from time import perf_counter

from .core import MAX_NODES, Genome, Wisp, mutate, random_genome


CONDITIONS = {
    "fixed": (False, False),
    "rules_only": (True, False),
    "wiring_only": (False, True),
    "rules_and_wiring": (True, True),
}
TEACH_ROUNDS = 8   # One presentation of each cue per round.
PROBE_ROUNDS = 4
PHASES = (
    "before_teaching", "after_teaching", "before_reteaching", "after_reteaching"
)


def derived_seed(seed, *parts):
    """Stable independent streams, including when work is scheduled in parallel."""
    key = json.dumps([seed, *parts], separators=(",", ":")).encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def identifier(genome):
    payload = json.dumps(genome.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Lesson:
    seed: int
    assignment: int  # Expected action = cue XOR assignment, then reverse.


def lesson_bank(seed, split, pairs):
    lessons = []
    for index in range(pairs):
        episode_seed = derived_seed(seed, "lessons", split, index)
        # The two fresh siblings get identical cue orders and initial random state.
        lessons.extend((Lesson(episode_seed, 0), Lesson(episode_seed, 1)))
    return lessons


def cue_order(seed, phase, rounds):
    rng = random.Random(derived_seed(seed, "presentation", phase))
    cues = []
    for _ in range(rounds):
        first = rng.randrange(2)
        cues.extend((first, 1 - first))
    return cues


def train_block(agent, lesson, phase, assignment, feedback):
    cues = cue_order(lesson.seed, phase, TEACH_ROUNDS)
    # This control has the same count and timing of feedback opportunities.
    # Its balanced signs are generated independently of choices and answers.
    independent = [1, -1] * TEACH_ROUNDS
    random.Random(derived_seed(lesson.seed, "uninformative", phase)).shuffle(independent)
    decisions = []
    for index, cue in enumerate(cues):
        matched = agent.choose(cue) == (cue ^ assignment)
        decisions.append(int(matched))
        value = (1 if matched else -1) if feedback == "informative" else independent[index]
        agent.feedback(value)
    return decisions


def probe(agent, lesson, phase, assignment):
    cues = cue_order(lesson.seed, phase, PROBE_ROUNDS)
    return sum(agent.choose(cue) == (cue ^ assignment) for cue in cues) / len(cues)


def run_lesson(agent, lesson, feedback="informative"):
    """A fresh agent is supplied by the caller. No probe decision receives feedback."""
    assignment = lesson.assignment
    before = probe(agent, lesson, "before_teaching", assignment)
    teaching_curve = train_block(agent, lesson, "teach", assignment, feedback)
    after = probe(agent, lesson, "after_teaching", assignment)
    reversed_rule = 1 - assignment
    before_reversal = probe(agent, lesson, "before_reteaching", reversed_rule)
    reversal_curve = train_block(agent, lesson, "reteach", reversed_rule, feedback)
    after_reversal = probe(agent, lesson, "after_reteaching", reversed_rule)
    return {
        **dict(zip(PHASES, (before, after, before_reversal, after_reversal))),
        "teaching_curve": teaching_curve, "reversal_curve": reversal_curve,
    }


class AssociationLearner:
    """Positive or corrective feedback identifies the current binary action."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.preferred = {}
        self.last = None

    def choose(self, cue):
        action = self.preferred.get(cue)
        if action is None:
            action = self.rng.randrange(2)
        self.last = (cue, action)
        return action

    def feedback(self, value):
        cue, action = self.last
        self.preferred[cue] = action if value > 0 else 1 - action


class FixedReflex:
    def choose(self, cue):
        return cue

    def feedback(self, value):
        pass


def summarize(episodes):
    rates = {phase: mean(row[phase] for row in episodes) for phase in PHASES}
    curves = {key: [mean(step) for step in zip(*(row[key] for row in episodes))]
              for key in ("teaching_curve", "reversal_curve")}
    return {
        **rates, **curves,
        "learning_gain": rates["after_teaching"] - rates["before_teaching"],
        "reversal_gain": rates["after_reteaching"] - rates["before_reteaching"],
        "fitness": (rates["after_teaching"] + rates["after_reteaching"]) / 2,
    }


def evaluate(genome, lessons, mode="neural"):
    records = []
    for lesson in lessons:
        seed = derived_seed(lesson.seed, "lifetime")
        if mode == "association":
            agent = AssociationLearner(seed)
        elif mode == "reflex":
            agent = FixedReflex()
        else:
            agent = Wisp(genome, seed=seed, plastic=mode != "no_plasticity")
        feedback = "uninformative" if mode == "uninformative_feedback" else "informative"
        records.append({**asdict(lesson), **run_lesson(agent, lesson, feedback)})
    return {"summary": summarize(records), "episodes": records}


def score_candidate(args):
    data, lessons = args
    return evaluate(Genome.from_dict(data), lessons)["summary"]["fitness"]


def next_generation(population, scores, flags, seed, generation, *, evolve_trace=False, node_limit=MAX_NODES):
    ranked = sorted(range(len(population)), key=lambda i: -scores[i])
    parents = ranked[:max(1, len(ranked) // 2)]
    next_pop = [population[ranked[0]]]
    for index in range(1, len(population)):
        parent_rng = random.Random(derived_seed(seed, "parent", generation, index))
        mutation_rng = random.Random(derived_seed(seed, "mutate", generation, index))
        parent = population[parent_rng.choice(parents)]
        child = mutate(Genome.from_dict(parent.to_dict(), node_limit=node_limit), mutation_rng,
                       rules=flags[0], wiring=flags[1], evolve_trace=evolve_trace, node_limit=node_limit)
        next_pop.append(child)
    return next_pop


def check_bounds(population, generations, workers, train_pairs, test_pairs):
    for name, value, lower, upper in (
        ("population", population, 2, 128), ("generations", generations, 1, 100),
        ("workers", workers, 1, 16), ("train_pairs", train_pairs, 1, 32),
        ("test_pairs", test_pairs, 1, 64),
    ):
        if not lower <= value <= upper:
            raise ValueError(f"{name} must be between {lower} and {upper}")


def run_experiment(seed=7, population=8, generations=3, workers=1,
                   train_pairs=2, test_pairs=4, *, timing=None):
    check_bounds(population, generations, workers, train_pairs, test_pairs)
    founders = [random_genome(random.Random(derived_seed(seed, "founder", i)))
                for i in range(population)]
    banks = [lesson_bank(seed, f"train-{g}", train_pairs) for g in range(generations)]
    held_out = lesson_bank(seed, "held-out", test_pairs)
    report = {
        "protocol": "wisp-binary-reversal-v1",
        "settings": dict(seed=seed, population=population, generations=generations,
                         workers=workers, train_pairs=train_pairs, test_pairs=test_pairs,
                         feedback_trials_per_block=2 * TEACH_ROUNDS,
                         no_feedback_trials_per_probe=2 * PROBE_ROUNDS),
        "selection_budget_per_condition": {
            "genome_evaluations": population * generations,
            "lifetimes": population * generations * train_pairs * 2,
            "choices_per_lifetime": 4 * TEACH_ROUNDS + 8 * PROBE_ROUNDS,
            "feedback_events_per_lifetime": 4 * TEACH_ROUNDS,
        },
        "seed_manifest": {
            "training": [[asdict(x) for x in bank] for bank in banks],
            "held_out": [asdict(x) for x in held_out],
        },
        "founder_genome_hashes": [identifier(g) for g in founders],
        "limits": [
            "Abstract binary cues and deterministic feedback. Only two assignments exist.",
            "Held-out lessons vary cue order and lifetime randomness within the same task family.",
            "Selection uses post-teaching and post-reversal accuracy; final tests never select a genome.",
            "All conditions select and mutate initial weights and the plasticity rate; fixed means fixed neuron dynamics and topology.",
            "Fitness measures short-term adaptation. There is no overnight retention, vision or body transfer test.",
            "Budgets match task interactions. Compute time and network complexity can differ.",
        ],
        "curve_definition": "Index zero is the first choice in a block. Values are accuracy before that interaction's feedback, pooled across lifetimes in summaries.",
        "conditions": {},
    }
    champions = {}
    selection_seconds = 0.0
    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for name, flags in CONDITIONS.items():
            cohort = list(founders)
            history = []
            for generation, bank in enumerate(banks):
                jobs = [(g.to_dict(), bank) for g in cohort]
                started = perf_counter()
                scores = list(pool.map(score_candidate, jobs) if pool else map(score_candidate, jobs))
                selection_seconds += perf_counter() - started
                best = max(range(len(cohort)), key=scores.__getitem__)
                history.append({"generation": generation + 1, "scores": scores,
                                "best_index": best, "mean_fitness": mean(scores)})
                if generation + 1 < generations:
                    cohort = next_generation(cohort, scores, flags, seed, generation)
            winner = cohort[best]
            champions[name] = winner
            report["conditions"][name] = {
                "evolve_rules": flags[0], "evolve_wiring": flags[1],
                "history": history, "champion_hash": identifier(winner),
                "champion_size": {"nodes": len(winner.nodes), "edges": len(winner.edges)},
                "selection_fitness": scores[best], "held_out": evaluate(winner, held_out),
                "no_plasticity": evaluate(winner, held_out, "no_plasticity"),
                "uninformative_feedback": evaluate(winner, held_out, "uninformative_feedback"),
            }
    finally:
        if pool is not None:
            pool.shutdown()

    report["baselines"] = {mode: evaluate(None, held_out, mode)
                           for mode in ("association", "reflex")}
    champion_name = max(CONDITIONS, key=lambda n: report["conditions"][n]["selection_fitness"])
    demo = Lesson(derived_seed(seed, "demonstration"), 0)
    demonstrator = Wisp(champions[champion_name], seed=derived_seed(demo.seed, "lifetime"))
    train_block(demonstrator, demo, "teach", demo.assignment, "informative")
    report["checkpoint"] = {
        "file": "champion.json", "condition": champion_name,
        "genome_hash": identifier(champions[champion_name]), "lesson": asdict(demo),
        "origin": "Fresh lifetime taught cue 0 -> action 0 and cue 1 -> action 1 for one block.",
        "selection": "Largest final training fitness, ties resolved by condition order.",
    }
    if timing is not None:
        lifetimes = report["selection_budget_per_condition"]["lifetimes"] * len(CONDITIONS)
        timing.update(selection_lifetimes=lifetimes,
                      selection_evaluation_seconds=selection_seconds,
                      selection_lifetimes_per_second=lifetimes / selection_seconds)
    return report, demonstrator.checkpoint()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--train-pairs", type=int, default=2)
    parser.add_argument("--test-pairs", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path("runs/demo"))
    args = parser.parse_args(argv)
    timing = {}
    started = perf_counter()
    try:
        report, checkpoint = run_experiment(args.seed, args.population, args.generations,
                                            args.workers, args.train_pairs, args.test_pairs,
                                            timing=timing)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, payload in (("champion.json", checkpoint), ("report.json", report)):
        (args.output / name).write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n",
                                        encoding="utf-8")
    timing["wall_seconds"] = perf_counter() - started
    timing["definition"] = ("Wall time covers the experiment and scientific output writes. "
                            "Selection throughput counts only scored training lifetimes, using "
                            "wall time inside population evaluations, including worker dispatch.")
    (args.output / "timing.json").write_text(json.dumps(timing, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output / 'report.json'}")
    print(f"Taught individual: {args.output / 'champion.json'}")
    for name, condition in report["conditions"].items():
        print(f"{name}: held-out mean post-teaching/reversal accuracy "
              f"{condition['held_out']['summary']['fitness']:.3f}")


if __name__ == "__main__":
    main()
