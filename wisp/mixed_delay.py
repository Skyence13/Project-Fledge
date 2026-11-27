"""Read-only transfer probe for a preselected campaign champion."""

import argparse
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import random

from .challenge_evolution import COMPARATORS, PAIRS, _agent, _count, _memory, _original, _schedule
from .campaign import _readonly, _unpack
from .core import Genome
from .experiment import derived_seed, identifier
from .next_plan import LaggedAssociation

PROTOCOL = "wisp-mixed-delay-transfer-v1"
DELAYS = (1, 3, 5)


class SourceOracle(LaggedAssociation):
    """A privileged positive control given the actual source choice at grading."""

    def grade_source(self, cue, action, sign):
        self.learned[cue] = action if sign == 1 else 1 - action


def schedules(seed, length):
    # Every teaching position sees each lag once across the three rotations.
    offsets = [i % len(DELAYS) for i in range(length)]
    random.Random(derived_seed(seed, "lag-order")).shuffle(offsets)
    for rotation, lag in enumerate(DELAYS):
        yield [lag] * length, [DELAYS[(offset + rotation) % 3] for offset in offsets]


def block(genome, mode, row, lags, target):
    seed = int(row["delay_lifetime_seed"])
    if mode == "source_oracle":
        agent = SourceOracle(seed, 0)
    else:
        agent = _agent(mode, genome, seed, 0)
    for i, (cue, lag) in enumerate(zip(row["teaching_cues"], lags, strict=True)):
        action = agent.choose(cue)
        for other in row["distractors"][i][:lag]:
            agent.choose(other)
        sign = 1 if action == (cue ^ target) else -1
        if mode == "source_oracle":
            agent.grade_source(cue, action, sign)
        else:
            agent.feedback(row["delay_control_signs"][i] if mode == "uninformative_feedback" else sign)
    return sum(agent.choose(cue) == (cue ^ target) for cue in row["probe_cues"])


def transfer(champion, seed, split):
    if split not in ("validation", "confirmation"):
        raise ValueError("Select a held-out bank.")
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("Champion fingerprint mismatch.")
    bank, lessons, old = _schedule(derived_seed(seed, PROTOCOL), 2, 1, split)
    arms = {"intact": ("neural", genome), "trace_disabled": ("neural", replace(genome, trace_decay=0.0))}
    for mode in (*[m for m in COMPARATORS if m != "lag_aware_association"], "source_oracle"):
        arms[mode] = mode, genome
    results = {}
    for name, (mode, subject) in arms.items():
        fixed = {lag: 0 for lag in DELAYS}
        mixed = total = 0
        for row in lessons:
            for constant, varying in schedules(row["seed"], len(row["teaching_cues"])):
                for target in (0, 1):
                    fixed[constant[0]] += block(subject, mode, row, constant, target)
                    mixed += block(subject, mode, row, varying, target)
                    total += len(row["probe_cues"])
        results[name] = {"mixed_recall": _count(mixed, total),
                         "fixed_recall": {lag: _count(n, total // 3) for lag, n in fixed.items()}}
        if name in ("intact", "trace_disabled"):
            results[name]["old_skills"] = _original(subject, old)
            results[name]["fixed_one_shot"] = {lag: _memory(subject, lessons, lag)["credit"] for lag in DELAYS}
    return {"protocol": PROTOCOL, "fingerprint": identifier(genome), "trace_decay": genome.trace_decay,
            "seed": seed, "bank_seed": str(bank), "split": split, "pairs": PAIRS[split], "arms": results}


def archived_nominee(directory, generation):
    with closing(_readonly(directory / "campaign.sqlite3")) as db:
        row = db.execute("SELECT population,report FROM rounds WHERE generation=?", (generation,)).fetchone()
    if row is None:
        raise ValueError("Requested round has no committed population.")
    record = json.loads(row[1])
    group, slot = record["champion_id"].split(":")
    return {"fingerprint": record["fingerprint"], "genome": _unpack(row[0])[group][int(slot)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="Source campaign directory")
    parser.add_argument("--generation", required=True, type=int, help="Preselected archived round")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--split", choices=("validation", "confirmation"), default="validation")
    args = parser.parse_args()
    report = transfer(archived_nominee(args.run, args.generation), args.seed, args.split)
    report.update(source_directory=str(args.run), source_generation=args.generation)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
