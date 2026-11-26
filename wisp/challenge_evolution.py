"""Evolve on delayed feedback and test earlier-cue learning on distinct lesson banks."""

from dataclasses import replace
from statistics import mean
import random

from .core import Genome, Wisp
from .experiment import (CONDITIONS, PROBE_ROUNDS, AssociationLearner, FixedReflex,
                         derived_seed, evaluate, identifier, lesson_bank, next_generation)
from .next_plan import LaggedAssociation, _delayed, _enroll, _lesson


PROTOCOL = "wisp-delayed-evolution-v1"
STAGES = tuple({"id": f"delay-{delay}", "label": f"Delayed feedback · {delay} intervening "
                + ("choice" if delay == 1 else "choices"), "delay": delay} for delay in (1, 3, 5))
PAIRS = {"selection": 4, "validation": 16, "confirmation": 32}
THRESHOLDS = {"credit": .75, "delayed": .90, "immediate": .90, "reversal": .90}
WEIGHTS = {"credit": .40, "delayed": .30, "immediate": .15, "reversal": .15}
COMPARATORS = ("recent_association", "lag_aware_association", "reflex",
               "no_plasticity", "uninformative_feedback")


def population_from_checkpoint(checkpoint):
    """Carry only inherited genomes into the new selection experiment."""
    population = {name: [] for name in CONDITIONS}
    for member_id, genome in _enroll(checkpoint, None):
        population[member_id.split(":")[0]].append(genome.to_dict())
    return population


def _population(data):
    if not isinstance(data, dict) or set(data) != set(CONDITIONS):
        raise ValueError("A challenge population requires all four experimental conditions.")
    groups = list(data.values())
    if (not all(isinstance(group, list) for group in groups) or not 2 <= len(groups[0]) <= 16
            or any(len(group) != len(groups[0]) for group in groups)):
        raise ValueError("Use equally sized conditions with 2 to 16 genomes each.")
    return {name: [Genome.from_dict(row) for row in data[name]] for name in CONDITIONS}


def seed_trace_population(population, seed):
    """Initialize a matched trace comparison with each archived zero-decay genome once."""
    if type(seed) is not int:
        raise ValueError("Provide an integer comparison seed.")
    groups = _population(population)
    if any(g.trace_decay for group in groups.values() for g in group):
        raise ValueError("Start the matched eligibility-trace experiment from zero-decay genomes.")
    return {name: [g.to_dict() for g in group] for name, group in groups.items()}


def _schedule(seed, stage, generation, split):
    if type(seed) is not int or type(generation) is not int or generation < 1:
        raise ValueError("Provide an integer seed and a positive campaign generation.")
    if type(stage) is not int or not 0 <= stage < len(STAGES) or split not in PAIRS:
        raise ValueError("Choose a defined challenge stage and bank.")
    bank = derived_seed(seed, PROTOCOL, stage, generation, split)
    recall = [_lesson(bank, index) for index in range(PAIRS[split])]
    # The standalone memory assay defines the first three distractors. Extend that
    # schedule with independently drawn cues for the longer challenge.
    for row in recall:
        rng = random.Random(derived_seed(row["seed"], "extended-distractors"))
        for choices in row["distractors"]:
            choices.extend(rng.randrange(2) for _ in range(STAGES[-1]["delay"] - len(choices)))
    return bank, recall, lesson_bank(bank, "original-reversal", PAIRS[split])


def _agent(mode, genome, seed, delay):
    if mode == "recent_association":
        return AssociationLearner(seed)
    if mode == "lag_aware_association":
        return LaggedAssociation(seed, delay)
    if mode == "reflex":
        return FixedReflex()
    return Wisp(genome, seed=seed, plastic=mode != "no_plasticity")


def _count(correct, total):
    return {"correct": correct, "total": total, "accuracy": correct / total}


def _memory(genome, lessons, delay, mode="neural"):
    # The second cue's choices are distractors. Their identity and the grade do not
    # reveal which action the source originally took, or its private correct action.
    credit_correct = 0
    delayed_counts = {f"delay_{delay}_{key}": [0, 0] for key in ("first", "recall", "opposite_recall")}
    for lesson in lessons:
        for assignment in (0, 1):
            mapping = (assignment, 1 - assignment)
            agent = _agent(mode, genome, int(lesson["delay_lifetime_seed"]), delay)
            _delayed(delayed_counts, agent, mode, lesson, mapping, delay)
        for source in (0, 1):
            # Share agent RNG and irrelevant control sign across the opposite target pair.
            seed = derived_seed(lesson["seed"], "one-shot", source)
            control_sign = 1 if derived_seed(seed, "irrelevant-grade") % 2 else -1
            for target in (0, 1):
                agent = _agent(mode, genome, seed, delay)
                original = agent.choose(source)
                for _ in range(delay):
                    agent.choose(1 - source)
                grade = 1 if original == target else -1
                agent.feedback(control_sign if mode == "uninformative_feedback" else grade)
                credit_correct += agent.choose(source) == target
    return {"credit": _count(credit_correct, 4 * len(lessons)),
            "delayed": _count(*delayed_counts[f"delay_{delay}_opposite_recall"])}


def _original(genome, lessons):
    rows = evaluate(genome, lessons)["episodes"]
    probes = 2 * PROBE_ROUNDS
    return {key: _count(sum(round(row[phase] * probes) for row in rows), len(rows) * probes)
            for key, phase in (("immediate", "after_teaching"), ("reversal", "after_reteaching"))}


def score_generation(population, *, seed, stage, generation):
    """Score every inherited candidate on matched development trials and nominate one."""
    groups = _population(population)
    bank, lessons, old = _schedule(seed, stage, generation, "selection")
    scores = {}
    rows = []
    cached = {}
    champion = None
    for condition in CONDITIONS:
        scores[condition] = []
        for slot, genome in enumerate(groups[condition]):
            fingerprint = identifier(genome)
            if fingerprint not in cached:
                cached[fingerprint] = {**_memory(genome, lessons, STAGES[stage]["delay"]),
                                       **_original(genome, old)}
            metrics = cached[fingerprint]
            fitness = sum(WEIGHTS[key] * metrics[key]["accuracy"] for key in WEIGHTS)
            row = {"id": f"{condition}:{slot}", "fingerprint": fingerprint,
                   "fitness": fitness, **metrics}
            if genome.trace_decay:
                row["trace_decay"] = genome.trace_decay
            rows.append(row)
            scores[condition].append(fitness)
            if champion is None or fitness > champion["fitness"]:
                champion = {"id": row["id"], "fitness": fitness, "genome": genome.to_dict(),
                            "fingerprint": fingerprint}
    summary = {key: mean(row[key]["accuracy"] for row in rows) for key in THRESHOLDS}
    summary.update(mean_fitness=mean(row["fitness"] for row in rows), best_fitness=champion["fitness"])
    return {"protocol": PROTOCOL, "bank_seed": str(bank), "champion": champion, "fitness": scores,
            "summary": summary, "candidates": rows}


def breed_population(population, scores, *, seed, generation, evolve_trace=False):
    if type(evolve_trace) is not bool:
        raise ValueError("The trace-mutation option must be a boolean.")
    groups = _population(population)
    fitness = scores["fitness"]
    if (set(fitness) != set(CONDITIONS) or
            any(len(fitness[name]) != len(groups[name]) or
                any(type(value) not in (float, int) or not 0 <= value <= 1 for value in fitness[name])
                for name in CONDITIONS)):
        raise ValueError("Provide one selection score per member of this population.")
    reproduction_seed = derived_seed(seed, PROTOCOL, "reproduction")
    return {name: [g.to_dict() for g in next_generation(groups[name], fitness[name], flags,
                                                        reproduction_seed, generation, evolve_trace=evolve_trace)]
            for name, flags in CONDITIONS.items()}


def validate_champion(champion, *, seed, stage, generation, split="validation"):
    """Assess the preselected nominee. Each review uses its own gate and confirmation banks."""
    if split not in ("validation", "confirmation"):
        raise ValueError("Qualification requires a validation or confirmation bank.")
    bank, lessons, old = _schedule(seed, stage, generation, split)
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("The nominee's genome does not match its fingerprint.")
    delay = STAGES[stage]["delay"]
    metrics = {**_memory(genome, lessons, delay), **_original(genome, old)}
    previous = {s["id"]: _memory(genome, lessons, s["delay"]) for s in STAGES[:stage]}
    controls = {mode: _memory(genome, lessons, delay, mode) for mode in COMPARATORS}
    passed = all(row["accuracy"] >= THRESHOLDS[key] for key, row in metrics.items())
    retained = all(row["accuracy"] >= THRESHOLDS[key]
                   for earlier in previous.values() for key, row in earlier.items())
    report = {"protocol": PROTOCOL, "split": split, "bank_seed": str(bank), "pairs": PAIRS[split],
              "stage": stage, "generation": generation, "fingerprint": champion["fingerprint"],
              "thresholds": dict(THRESHOLDS), "passed": passed and retained, **metrics,
              "previous_stages": previous, "controls": controls}
    if genome.trace_decay:
        disabled = replace(genome, trace_decay=0.0)
        report["trace_ablation"] = {"inherited_decay": genome.trace_decay,
                                    **_memory(disabled, lessons, delay), **_original(disabled, old)}
    return report
