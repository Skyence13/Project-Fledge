"""Test whether accurate marker-practice feedback changes free choices at equal approval rates."""

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import random
from statistics import quantiles

from .core import Genome, Wisp
from .experiment import derived_seed, identifier
from .marked_recall import CueLookup, marked_trial
from .marker_practice import CONDITIONS, PracticedMarkerLookup, _condition_report, practice_schedule
from .mixed_delay import archived_nominee

PROTOCOL = "wisp-marker-practice-yoked-v1"
STARTS = {"validation": 128, "confirmation": 256}
ARMS = ("accurate", "yoked", "independent")
RECALL_CONTRASTS = (("accurate", "yoked"), ("accurate", "independent"), ("yoked", "independent"))


def train(agent, schedule, supplied=None):
    """Always grade the credited source truthfully. Only the probe grade varies."""
    if supplied is not None and len(supplied) != len(schedule):
        raise ValueError("Need one supplied grade per probe.")
    grades = []
    for i, (cue, marker, target, _) in enumerate(schedule):
        _, correct = marked_trial(agent, cue, marker, marker, target)
        sign = (1 if correct else -1) if supplied is None else supplied[i]
        agent.feedback(sign)
        grades.append(sign)
    return grades


def donated_grades(tapes, bank, pair, member):
    grades = tapes[1 - member].copy()
    random.Random(derived_seed(bank, "donor-order", pair, member)).shuffle(grades)
    return grades


def practice_pair(factory, bank, pair):
    schedules, agents, tapes = [], [], []
    for member in (0, 1):
        start = derived_seed(bank, "lifetime", pair, member)
        schedule = practice_schedule(derived_seed(bank, "practice-order", pair, member))
        group = {arm: factory(start) for arm in ARMS}
        tapes.append(train(group["accurate"], schedule))
        schedules.append(schedule)
        agents.append(group)
    for member, (group, schedule) in enumerate(zip(agents, schedules)):
        yoked = donated_grades(tapes, bank, pair, member)
        balanced = [sign for _, _, _, sign in schedule]
        train(group["yoked"], schedule, yoked)
        train(group["independent"], schedule, balanced)
        yield group, {"accurate": tapes[member], "yoked": yoked, "independent": balanced}


def paired_interval(pair_counts, seed):
    # Each donor/recipient pair contributes eight prefixes, even if none conflict.
    rng = random.Random(seed)
    n = len(pair_counts)
    bootstrap = [sum(rng.choices(pair_counts, k=n)) / (8 * n) for _ in range(2000)]
    limits = quantiles(bootstrap, n=40, method="inclusive")
    return {"difference": sum(pair_counts) / (8 * n), "ci95": [limits[0], limits[-1]],
            "pairs": n, "histories": 8 * n, "bootstrap_draws": len(bootstrap)}


def _evaluate(factory, bank, starts, checkpoint=None):
    if starts < 2 or starts % 2:
        raise ValueError("Need complete donor/recipient pairs.")
    tallies = {arm: {cond: [Counter(), Counter()] for cond in CONDITIONS} for arm in ARMS}
    conflicts = {arm: [0, 0] for arm in ARMS}
    positives = Counter()
    pair_counts = {"yoked_minus_accurate": [], "independent_minus_yoked": []}
    recall = {(a, b): {cond: Counter() for cond in CONDITIONS} for a, b in RECALL_CONTRASTS}
    for pair in range(starts // 2):
        if checkpoint is not None:
            checkpoint()
        counts = Counter()
        for member, (group, grades) in enumerate(practice_pair(factory, bank, pair)):
            for arm, tape in grades.items():
                positives[arm] += tape.count(1)
            for cue, marker in product((0, 1), repeat=2):
                # Begin each prefix on matched test draws, independently of practice.
                test_seed = derived_seed(bank, "test", pair, member, cue, marker)
                for agent in group.values():
                    rng = agent._rng if isinstance(agent, Wisp) else agent.rng
                    rng.seed(test_seed)
                for occurrence, target in product((0, 1), repeat=2):
                    conditions = CONDITIONS if marker == occurrence else ("shuffled",)
                    correct = {}
                    for arm, agent in group.items():
                        conflict, success = marked_trial(deepcopy(agent), cue, marker, occurrence, target)
                        correct[arm] = int(success)
                        if occurrence == target == 0:
                            conflicts[arm][marker] += conflict
                            counts[arm] += conflict
                        for cond in conditions:
                            tallies[arm][cond][occurrence][conflict, success] += 1
                    for a, b in RECALL_CONTRASTS:
                        for cond in conditions:
                            recall[a, b][cond][correct[a] - correct[b]] += 1
        pair_counts["yoked_minus_accurate"].append(counts["yoked"] - counts["accurate"])
        pair_counts["independent_minus_yoked"].append(counts["independent"] - counts["yoked"])
    if positives["accurate"] != positives["yoked"] or positives["independent"] != starts * 8:
        raise ValueError("Probe grade supplies failed calibration.")
    arms = {arm: {cond: _condition_report(tallies[arm][cond], conflicts[arm], cond)
                  for cond in CONDITIONS} for arm in ARMS}
    primary = {key: paired_interval(rows, derived_seed(bank, "pair-bootstrap"))
               for key, rows in pair_counts.items()}
    paired = {}
    for (a, b), conditions in recall.items():
        comparisons = {}
        for cond, counts in conditions.items():
            total = sum(counts.values())
            comparisons[cond] = {"wins": counts[1], "losses": counts[-1], "ties": counts[0],
                                 "total": total, "difference": (counts[1] - counts[-1]) / total}
        paired[f"{a}_minus_{b}"] = comparisons
    feedback = {arm: {"positive": positives[arm], "negative": 16 * starts - positives[arm]}
                for arm in ARMS}
    return arms, primary, paired, feedback


def assess(champion, seed, split, *, checkpoint=None):
    if split not in STARTS:
        raise ValueError("Choose a held-out split.")
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("Archived nominee fingerprint mismatch.")
    bank = derived_seed(seed, PROTOCOL, split)
    zero = replace(genome, trace_decay=0)
    factories = {"intact": lambda s: Wisp(genome, seed=s),
                 "trace_disabled": lambda s: Wisp(zero, seed=s),
                 "no_plasticity": lambda s: Wisp(genome, seed=s, plastic=False),
                 "cue_lookup": CueLookup, "marker_lookup": PracticedMarkerLookup}
    arms, primary, paired, feedback = {}, {}, {}, {}
    for name, factory in factories.items():
        arms[name], primary[name], paired[name], feedback[name] = _evaluate(factory, bank, STARTS[split], checkpoint)
    return {"protocol": PROTOCOL, "split": split, "seed": seed, "bank_seed": str(bank),
            "fingerprint": identifier(genome), "starts": STARTS[split], "practice_episodes": 16,
            "arms": arms, "primary": primary, "paired": paired, "practice_probe_feedback": feedback}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--split", choices=tuple(STARTS), required=True)
    args = parser.parse_args()
    champion = archived_nominee(args.run, args.generation)
    print(json.dumps(assess(champion, args.seed, args.split), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
