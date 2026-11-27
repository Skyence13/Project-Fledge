"""Compare accurate and target-independent probe feedback during marker practice."""

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import random

from .challenge_evolution import PAIRS
from .core import Genome, Wisp
from .experiment import derived_seed, identifier
from .marked_recall import CueLookup, MarkerLookup, marked_trial
from .mixed_delay import archived_nominee


PROTOCOL = "wisp-marker-practice-v1"
ARMS = ("accurate", "independent")
CONDITIONS = ("informative", "shuffled")
CREDITS = ("early", "late")


class PracticedMarkerLookup(MarkerLookup):
    """Apply the known six-choice episode grammar throughout a lifetime."""

    def choose(self, cue):
        if len(self.actions) == 6:
            self.actions.clear()
            self.answer = None
        return super().choose(cue)

    def feedback(self, sign):
        if len(self.actions) == 5:
            super().feedback(sign)
        # The reference already knows the convention and ignores the probe grade.


def practice_schedule(seed):
    """Balance the independent sign within every cue, marker and hidden target."""
    episodes = list(product((0, 1), (0, 1), (0, 1), (-1, 1)))
    random.Random(seed).shuffle(episodes)
    return episodes


def practice(agent, schedule, arm):
    if arm not in ARMS:
        raise ValueError("Unknown practice condition.")
    positive = 0
    for cue, marker, target, independent_sign in schedule:
        _, correct = marked_trial(agent, cue, marker, marker, target)
        sign = (1 if correct else -1) if arm == "accurate" else independent_sign
        agent.feedback(sign)
        positive += sign == 1
    return positive


def _count(correct, total):
    return {"correct": correct, "total": total, "accuracy": correct / total if total else None}


def _summary(counts):
    conflict = _count(counts[True, True], counts[True, True] + counts[True, False])
    agreement = _count(counts[False, True], counts[False, True] + counts[False, False])
    overall = _count(conflict["correct"] + agreement["correct"], sum(counts.values()))
    return {"conflict": conflict, "agreement": agreement, "overall": overall}


def _condition_report(tallies, by_marker, condition):
    early, late = tallies
    pooled = _summary(early + late)
    mirrors = 2 if condition == "informative" else 4
    pooled["histories"] = pooled["overall"]["total"] // mirrors
    pooled["conflict_histories"] = pooled["conflict"]["total"] // mirrors
    pooled["conflict_histories_by_marker"] = dict(zip(CREDITS, by_marker))
    by_credit = {}
    for credit, counts in zip(CREDITS, (early, late)):
        row = _summary(counts)
        row["histories"] = row["overall"]["total"] // 2
        row["conflict_histories"] = row["conflict"]["total"] // 2
        by_credit[credit] = row
    pooled["by_credit"] = by_credit
    return pooled


def _evaluate(make_agent, bank, repeats):
    tallies = {arm: {cond: [Counter(), Counter()] for cond in CONDITIONS} for arm in ARMS}
    conflicts = {arm: [0, 0] for arm in ARMS}
    practice_positives = dict.fromkeys(ARMS, 0)
    differences = {cond: Counter() for cond in CONDITIONS}
    for repeat in range(repeats):
        schedule = practice_schedule(derived_seed(bank, "practice-order", repeat))
        start = derived_seed(bank, "lifetime", repeat)
        practiced = {arm: make_agent(start) for arm in ARMS}
        for arm, agent in practiced.items():
            practice_positives[arm] += practice(agent, schedule, arm)
        # Factorially crossed test episodes are new counterfactual continuations
        # of the complete practiced state. A test never trains the next test.
        for cue, marker, occurrence, target in product((0, 1), repeat=4):
            conditions = CONDITIONS if marker == occurrence else ("shuffled",)
            correct_pair = []
            for arm, agent in practiced.items():
                conflict, correct = marked_trial(deepcopy(agent), cue, marker, occurrence, target)
                if occurrence == 0 and target == 0:
                    conflicts[arm][marker] += int(conflict)
                for cond in conditions:
                    tallies[arm][cond][occurrence][conflict, correct] += 1
                correct_pair.append(int(correct))
            for cond in conditions:
                differences[cond][correct_pair[0] - correct_pair[1]] += 1
    report = {
        arm: {cond: _condition_report(tallies[arm][cond], conflicts[arm], cond)
              for cond in CONDITIONS}
        for arm in ARMS
    }
    paired = {}
    for cond, counts in differences.items():
        total = sum(counts.values())
        paired[cond] = {"wins": counts[1], "losses": counts[-1], "ties": counts[0],
                        "total": total, "difference": (counts[1] - counts[-1]) / total}
    feedback = {arm: {"positive": n, "negative": repeats * 16 - n}
                for arm, n in practice_positives.items()}
    return report, paired, feedback


def assess(champion, seed, split):
    if split not in ("validation", "confirmation"):
        raise ValueError("Choose a held-out split.")
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("Archived nominee fingerprint mismatch.")
    bank = derived_seed(seed, PROTOCOL, split)
    repeats = PAIRS[split]
    zero = replace(genome, trace_decay=0.0)
    factories = {
        "intact": lambda start: Wisp(genome, seed=start),
        "trace_disabled": lambda start: Wisp(zero, seed=start),
        "no_plasticity": lambda start: Wisp(genome, seed=start, plastic=False),
        "cue_lookup": CueLookup,
        "marker_lookup": PracticedMarkerLookup,
    }
    arms, paired, feedback = {}, {}, {}
    for learner, make_agent in factories.items():
        arms[learner], paired[learner], feedback[learner] = _evaluate(make_agent, bank, repeats)
    return {
        "protocol": PROTOCOL, "split": split, "seed": seed, "bank_seed": str(bank),
        "pairs": repeats, "practice_episodes": 16, "fingerprint": identifier(genome),
        "design": "Both copies of each lifetime share their initial RNG and a shuffled practice "
                  "factorial of cue, public marker, hidden target and independent probe sign. "
                  "Source grades always credit the marked choice. Both copies receive a probe "
                  "grade: accurate or target-independent. The grade counts match; their polarity "
                  "can differ. After practice, each cue/marker history is crossed with both "
                  "credited occurrences and both targets on separate copies without probe feedback.",
        "estimand": "Informative conflict accuracy conditions on each practice arm's own free "
                    "choices. Compare support by credit and paired overall accuracy across all "
                    "histories. Shuffled includes all crossed episodes; informative uses the "
                    "congruent subset. All observations concern one inherited genotype.",
        "arms": arms, "paired": paired, "practice_probe_feedback": feedback,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--split", required=True, choices=("validation", "confirmation"))
    args = parser.parse_args()
    report = assess(archived_nominee(args.run, args.generation), args.seed, args.split)
    report.update(source_directory=str(args.run), source_generation=args.generation)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
