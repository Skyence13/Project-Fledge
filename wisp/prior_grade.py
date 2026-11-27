"""Measure the effect of the preceding grade on one-shot source recall."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import random

from .challenge_evolution import PAIRS, _agent, _count, _schedule
from .core import Genome
from .experiment import derived_seed, identifier
from .mixed_delay import DELAYS, SourceOracle, archived_nominee
from .source_attribution import trial

PROTOCOL = "wisp-prior-grade-interference-v1"
CONTRASTS = (("given", "withheld"), ("given", "boundary_only"), ("boundary_only", "withheld"))
CONTEXTS = ("same_cue_same_target", "same_cue_opposite_target", "different_cue")


class PriorBoundary:
    """Intercept exactly the first event's feedback while preserving its cue stream."""

    def __init__(self, agent, intervention):
        self.agent = agent
        self.intervention = intervention

    def choose(self, cue):
        return self.agent.choose(cue)

    def feedback(self, sign):
        if self.intervention == "boundary_only":
            # Native feedback clears a persistent trace, consumes the pending grade and
            # advances the grade counter. Suppress only its synaptic weight update.
            plastic = self.agent.plastic
            try:
                self.agent.plastic = False
                self.agent.feedback(sign)
            finally:
                self.agent.plastic = plastic
        elif self.intervention != "withheld":
            raise ValueError("Unknown prior-grade intervention.")


def response(subject, mode, intervention, seed, prior, final):
    agent = SourceOracle(seed, 0) if mode == "source_oracle" else _agent(mode, subject, seed, 0)
    if intervention == "given":
        trial(agent, mode, *prior, control_sign=1)
    else:
        trial(PriorBoundary(agent, intervention), "neural", *prior, control_sign=1)
    _, correct = trial(agent, mode, *final, control_sign=1)
    return int(correct)


def contrast(differences):
    wins = sum(value > 0 for value in differences)
    losses = sum(value < 0 for value in differences)
    total = len(differences)
    return {"wins": wins, "losses": losses, "ties": total - wins - losses, "total": total,
            "delta": sum(differences) / total if total else None}


def summarize(rows, interventions):
    total = len(rows)
    accuracy = {arm: _count(sum(row[arm] for row in rows), total) if total else
                {"correct": 0, "total": 0, "accuracy": None} for arm in interventions}
    paired = {f"{left}_minus_{right}": contrast([row[left] - row[right] for row in rows])
              for left, right in CONTRASTS if left in interventions and right in interventions}
    return {"accuracy": accuracy, "paired": paired}


def score_arm(subject, mode, lessons):
    interventions = ("given", "withheld") if mode == "source_oracle" else ("given", "withheld", "boundary_only")
    rows = []
    histories = []
    for lesson in lessons:
        for source in (0, 1):
            for lag in DELAYS:
                for prior_lag in DELAYS:
                    if lag == prior_lag:
                        continue
                    seed = derived_seed(lesson["seed"], "single-grade", source, prior_lag, lag)
                    rng = random.Random(derived_seed(seed, "prior-event"))
                    prior_source, prior_target = rng.randrange(2), rng.randrange(2)
                    prior = (prior_source, prior_lag, prior_target)
                    history = {arm: 0 for arm in interventions}
                    for target in (0, 1):
                        context = "different_cue"
                        if prior_source == source:
                            context = "same_cue_same_target" if target == prior_target else "same_cue_opposite_target"
                        row = {"lag": lag, "context": context}
                        for arm in interventions:
                            row[arm] = response(subject, mode, arm, seed, prior, (source, lag, target))
                            history[arm] += row[arm] / 2
                        rows.append(row)
                    histories.append(history)
    return {"overall": summarize(rows, interventions),
            "by_lag": {str(lag): summarize([r for r in rows if r["lag"] == lag], interventions) for lag in DELAYS},
            "by_context": {key: summarize([r for r in rows if r["context"] == key], interventions) for key in CONTEXTS},
            "history_pairs": {f"{left}_minus_{right}": contrast([h[left] - h[right] for h in histories])
                              for left, right in CONTRASTS if left in interventions and right in interventions}}


def assess(champion, seed, split):
    if split not in ("validation", "confirmation"):
        raise ValueError("Choose a held-out split.")
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("Champion fingerprint mismatch.")
    bank, lessons, _ = _schedule(derived_seed(seed, PROTOCOL), 2, 1, split)
    arms = {}
    for name in ("intact", "trace_disabled", "no_plasticity", "source_oracle"):
        mode = "neural" if name in ("intact", "trace_disabled") else name
        subject = replace(genome, trace_decay=0.0) if name == "trace_disabled" else genome
        arms[name] = score_arm(subject, mode, lessons)
    return {"protocol": PROTOCOL, "seed": seed, "split": split, "bank_seed": str(bank),
            "pairs": PAIRS[split], "fingerprint": identifier(genome), "trace_decay": genome.trace_decay,
            "estimand": "Paired final-probe accuracy difference, left minus right. Each history_pairs "
                        "entry compares the average of its two opposite-target probe responses. Each "
                        "event contains six choices and a source probe. The two source lags differ. "
                        "All conditions receive an authentic final grade and see identical scheduled "
                        "cues and hidden targets. Interventions can change the intervening actions and "
                        "the sign of the final grade. The boundary_only intervention delivers the prior "
                        "grade with weight updates temporarily disabled; withheld skips it entirely.",
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
