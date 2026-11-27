"""Read-only test of source recall when the same cue has two candidate actions."""

import argparse
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import random

from .challenge_evolution import PAIRS, _count
from .core import Genome, Wisp
from .experiment import derived_seed, identifier
from .mixed_delay import DELAYS, archived_nominee
from .source_attribution import trial as singleton_trial


PROTOCOL = "wisp-marked-recall-v1"


class CueLookup:
    """Hold a grade until the next observed cue, then use the last action on that cue."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.last_by_cue = {}
        self.pending_sign = None

    def choose(self, cue):
        if self.pending_sign is None or cue not in self.last_by_cue:
            action = self.rng.randrange(2)
        else:
            previous = self.last_by_cue[cue]
            action = previous if self.pending_sign == 1 else 1 - previous
        self.pending_sign = None
        self.last_by_cue[cue] = action
        return action

    def feedback(self, sign):
        self.pending_sign = sign


class MarkerLookup:
    """Knows the public grammar. Sees the same cue stream and grade as a Wisp."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.actions = []
        self.marker = None
        self.answer = None

    def choose(self, cue):
        if not self.actions:
            self.marker = cue
        action = self.rng.randrange(2) if self.answer is None else self.answer
        self.actions.append(action)
        return action

    def feedback(self, sign):
        # Zero-based public choice sequence: marker, candidate, filler, candidate, filler.
        selected = self.actions[1 + 2 * self.marker]
        self.answer = selected if sign == 1 else 1 - selected


def marked_trial(agent, cue, marker, occurrence, target):
    agent.choose(marker)
    early = agent.choose(cue)
    agent.choose(1 - cue)
    late = agent.choose(cue)
    agent.choose(1 - cue)
    credited = (early, late)[occurrence]
    agent.feedback(1 if credited == target else -1)
    return early != late, agent.choose(cue) == target


def _score(make_agent, bank, repeats):
    counts = {condition: {"agreement": [0, 0], "conflict": [0, 0]}
              for condition in ("informative", "shuffled")}
    conflict_histories = 0
    for repeat, cue in product(range(repeats), (0, 1)):
        # The public marker is the only pre-grade difference between its two levels.
        seed = derived_seed(bank, "lifetime", repeat, cue)
        for marker in (0, 1):
            for occurrence, target in product((0, 1), repeat=2):
                conflict, correct = marked_trial(make_agent(seed), cue, marker, occurrence, target)
                cell = "conflict" if conflict else "agreement"
                # Full crossing gives an exactly uninformative marker on identical prefixes.
                # Congruent cells also form the informative condition.
                conditions = ("shuffled", "informative") if occurrence == marker else ("shuffled",)
                for condition in conditions:
                    tally = counts[condition][cell]
                    tally[0] += int(correct)
                    tally[1] += 1
                if occurrence == 0 and target == 0:
                    conflict_histories += int(conflict)
    result = {}
    for condition, cells in counts.items():
        result[condition] = {
            key: _count(correct, total) if total else {"correct": 0, "total": 0, "accuracy": None}
            for key, (correct, total) in cells.items()
        }
        result[condition].update(overall=_count(*map(sum, zip(*cells.values()))),
                                 histories=4 * repeats, conflict_histories=conflict_histories)
    return result


def _singleton(bank, repeats):
    correct = total = 0
    for repeat, lag, cue in product(range(repeats), DELAYS, (0, 1)):
        seed = derived_seed(bank, "singleton", repeat, lag, cue)
        for target in (0, 1):
            actor = CueLookup(seed)
            _, success = singleton_trial(actor, "cue_lookup", cue, lag, target, control_sign=1)
            correct += int(success)
            total += 1
    return _count(correct, total)


def assess(champion, seed, split):
    if split not in ("validation", "confirmation"):
        raise ValueError("Choose a held-out split.")
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("Archived nominee fingerprint mismatch.")
    bank = derived_seed(seed, PROTOCOL, split)
    repeats = PAIRS[split]
    zero_trace = replace(genome, trace_decay=0.0)
    learners = {
        "intact": lambda rng_seed: Wisp(genome, seed=rng_seed),
        "trace_disabled": lambda rng_seed: Wisp(zero_trace, seed=rng_seed),
        "no_plasticity": lambda rng_seed: Wisp(genome, seed=rng_seed, plastic=False),
        "cue_lookup": CueLookup,
        "marker_lookup": MarkerLookup,
    }
    return {
        "protocol": PROTOCOL, "split": split, "seed": seed, "bank_seed": str(bank),
        "pairs": repeats, "fingerprint": identifier(genome),
        "design": "Each fresh lifetime receives a binary public marker (0 = early, 1 = late), "
                  "the probe cue, its opposite, the probe cue again, its opposite, a signed "
                  "grade for one candidate action, then the probe cue. All actions are free. "
                  "The marker-aware reference is supplied the public convention. The nominee "
                  "receives no practice on this convention; its score measures spontaneous transfer.",
        "estimand": "First-probe accuracy when candidate actions differ. Shuffled crosses both "
                    "hidden occurrences with both targets for every public history; informative "
                    "uses the congruent subset. Opposite targets share the entire pre-grade history. "
                    "Histories count cue/marker/replicate prefixes once, not independent genotypes. "
                    "Agreement can be solved without the marker. Empty conflict cells are inconclusive.",
        "singleton_cue_lookup": _singleton(bank, repeats),
        "arms": {name: _score(factory, bank, repeats) for name, factory in learners.items()},
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
