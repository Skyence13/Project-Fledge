"""First source-cue response after one grade at an unannounced lag (read-only)."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import random

from .challenge_evolution import PAIRS, _agent, _count, _memory, _original, _schedule
from .core import Genome
from .experiment import derived_seed, identifier
from .mixed_delay import DELAYS, SourceOracle, archived_nominee

PROTOCOL = "wisp-single-grade-source-v1"


class LastAction:
    """Apply the sign to the last action on the next choice, irrespective of cue."""
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.answer = None

    def choose(self, cue):
        self.last = self.rng.randrange(2) if self.answer is None else self.answer
        self.answer = None
        return self.last

    def feedback(self, sign):
        self.answer = self.last if sign == 1 else 1 - self.last


def trial(agent, mode, source, lag, target, control_sign):
    """Six choices, one grade, one probe. Ordinary learners receive only cues and sign."""
    source_position = max(DELAYS) - lag
    for index in range(max(DELAYS) + 1):
        cue = source if index == source_position else 1 - source
        last = agent.choose(cue)
        if index == source_position:
            source_action = last
    grade = 1 if source_action == target else -1
    if mode == "source_oracle":
        agent.grade_source(source, source_action, grade)
    else:
        agent.feedback(control_sign if mode == "uninformative_feedback" else grade)
    agreement = "agreement" if last == source_action else "conflict"
    return agreement, agent.choose(source) == target


def lag_result(bins):
    result = {}
    for key, (correct, total) in bins.items():
        result[key] = _count(correct, total) if total else {"correct": 0, "total": 0, "accuracy": None}
    result["raw"] = _count(*(sum(column) for column in zip(*bins.values())))
    rates = [result[key]["accuracy"] for key in bins]
    result["balanced_accuracy"] = sum(rates) / 2 if None not in rates else None
    return result


def score_arm(mode, subject, lessons):
    by_lag = {}
    for lag in DELAYS:
        bins = {"agreement": [0, 0], "conflict": [0, 0]}
        for row in lessons:
            for source in (0, 1):
                for prior_lag in DELAYS:
                    if prior_lag == lag:
                        continue
                    seed = derived_seed(row["seed"], "single-grade", source, prior_lag, lag)
                    prefix = random.Random(derived_seed(seed, "prior-event"))
                    prior_source, prior_target = prefix.randrange(2), prefix.randrange(2)
                    prior_control = prefix.choice((-1, 1))
                    control = 1 if derived_seed(seed, "irrelevant-grade") % 2 else -1
                    for target in (0, 1):
                        if mode == "source_oracle":
                            agent = SourceOracle(seed, 0)
                        elif mode == "last_action":
                            agent = LastAction(seed)
                        else:
                            agent = _agent(mode, subject, seed, 0)
                        trial(agent, mode, prior_source, prior_lag, prior_target, prior_control)
                        key, correct = trial(agent, mode, source, lag, target, control)
                        bins[key][0] += correct
                        bins[key][1] += 1
        by_lag[str(lag)] = lag_result(bins)
    rates = [row["balanced_accuracy"] for row in by_lag.values()]
    return {"by_lag": by_lag, "balanced_accuracy": sum(rates) / len(rates) if None not in rates else None}


def assess(champion, seed, split):
    if split not in ("validation", "confirmation"):
        raise ValueError("Choose a held-out split.")
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("Champion fingerprint mismatch.")
    bank, lessons, old = _schedule(derived_seed(seed, PROTOCOL), 2, 1, split)
    arms = {}
    for name in ("intact", "trace_disabled", "source_oracle", "recent_association", "last_action",
                 "reflex", "no_plasticity", "uninformative_feedback"):
        mode = "neural" if name in ("intact", "trace_disabled") else name
        subject = replace(genome, trace_decay=0.0) if name == "trace_disabled" else genome
        result = score_arm(mode, subject, lessons)
        if mode == "neural":
            result["old_skills"] = _original(subject, old)
            result["fixed_one_shot"] = {str(lag): _memory(subject, lessons, lag)["credit"] for lag in DELAYS}
        arms[name] = result
    return {"protocol": PROTOCOL, "seed": seed, "bank_seed": str(bank), "split": split,
            "pairs": PAIRS[split], "fingerprint": identifier(genome), "trace_decay": genome.trace_decay,
            "design": "Two events per fresh lifetime, each with one singleton source cue among five "
                      "other-cue choices, one grade after choice six, and one ungraded source probe. "
                      "Score only event two. Its lag always differs from event one; both possible prior "
                      "lags are equally represented. The prior target is independently randomized. "
                      "Opposite final targets share their lifetime seed and full pre-grade history.",
            "estimand": "Equal weights for each lag and for observed source/last-action agreement and "
                        "conflict within lag. Empty cells yield null. Actions are freely chosen, so cell "
                        "membership can differ between learners. Raw denominators count responses, "
                        "including both members of each opposite-target pair. This measures behavioral "
                        "recall; accumulating cue-specific eligibility can also solve the task.",
            "arms": arms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--split", required=True, choices=("validation", "confirmation"))
    args = parser.parse_args()
    report = assess(archived_nominee(args.run, args.generation), args.seed, args.split)
    report.update(source_directory=str(args.run), source_generation=args.generation)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
