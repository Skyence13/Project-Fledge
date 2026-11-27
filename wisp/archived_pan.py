"""Read-only graded-pan diagnostic for one preselected archive nominee."""

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
from random import Random
from statistics import fmean

from .campaign import _readonly, read_status
from .core import Genome, Wisp, random_genome
from .experiment import derived_seed, identifier
from .mixed_delay import archived_nominee
from .pan_prediction import wisp_turn
from .pan_review import (
    bank_decision, fresh_skills, paired_advantage, post_practice_pair, post_practice_summary,
)
from .pan_trials import make_trials, reference_predictions


PROTOCOL = "archived-graded-pan-v1"
STARTS = {"validation": 16, "confirmation": 32}
ARMS = ("intact", "nonplastic", "trace_disabled", "independent")
REFERENCE_ARMS = ("grade_only", "last_seen", "motion_only", "nominal_body")


def verified_nominee(directory, generation, fingerprint):
    with closing(_readonly(directory / "campaign.sqlite3")) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported campaign schema")
    read_status(directory)  # Includes the campaign format version check.
    if generation < 1:
        raise ValueError("Choose a committed generation")
    champion = archived_nominee(directory, generation)
    if champion["fingerprint"] != fingerprint or identifier(Genome.from_dict(champion["genome"])) != fingerprint:
        raise ValueError("Registered nominee fingerprint mismatch")
    return champion


def practice_arms(genome, practice, start, sign_seed):
    agents = {
        "intact": Wisp(genome, seed=start),
        "nonplastic": Wisp(genome, seed=start, plastic=False),
        "trace_disabled": Wisp(replace(genome, trace_decay=0.), seed=start),
        "independent": Wisp(genome, seed=start),
    }
    signs = [1, -1] * (len(practice) // 2)
    Random(sign_seed).shuffle(signs)
    for (history, _, truth, _), independent in zip(practice, signs, strict=True):
        for name, agent in agents.items():
            response = wisp_turn(agent, history)
            truthful = 1 if response == int(truth >= 0) else -1
            agent.feedback(independent if name == "independent" else truthful)
    return agents


def test_turns(agent, tests, test_seeds):
    snapshot = agent.checkpoint()
    turns = []
    for (history, _, _, _), seed in zip(tests, test_seeds, strict=True):
        # Restore the practiced network and give this single test its own RNG.
        probe = Wisp.restore(dict(snapshot, rng=Random(seed).getstate()))
        turns.append(wisp_turn(probe, history))
    return turns


def cell_counts(tests, turns):
    counts = Counter()
    for (history, _, truth, speed), turn in zip(tests, turns, strict=True):
        right = int(truth > 0)
        key = ("moving" if speed else "stationary", history.pan_command, right)
        counts[key, "n"] += 1
        counts[key, "k"] += turn == right
    return counts


def accuracy(counts, motion):
    return sum(v for ((group, _, _), metric), v in counts.items() if group == motion and metric == "k") / sum(
        v for ((group, _, _), metric), v in counts.items() if group == motion and metric == "n")


def count_rows(counts):
    return [
        {"motion": m, "command": c, "target_side": s, "correct": counts[(m,c,s), "k"],
         "total": counts[(m,c,s), "n"]}
        for (m,c,s), metric in sorted(counts) if metric == "n"
    ]


def run_bank(champion, sensory_seed, earlier_seed, split, *, synthetic=False):
    if earlier_seed == sensory_seed:
        raise ValueError("Use distinct sensory and earlier-skill seeds")
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"]:
        raise ValueError("Nominee genome does not match its fingerprint")
    gains = (4, 2, 6) if synthetic else (4, 3, 5)
    bank = derived_seed(sensory_seed, PROTOCOL, split)
    names = ARMS + REFERENCE_ARMS
    totals = {g: {name: Counter() for name in names} for g in gains}
    clusters = {name: [] for name in names}
    retention = []
    oracle_errors = []
    for lifetime in range(STARTS[split]):
        start = derived_seed(bank, lifetime, "start")
        sign_seed = derived_seed(bank, lifetime, "grade-stream")
        macro_cells = {name: [] for name in names}
        for g in gains:
            practice = make_trials(derived_seed(bank, lifetime, "practice"), 1, g)
            tests = make_trials(derived_seed(bank, lifetime, "test"), 4, g)
            predictions = reference_predictions(practice, tests)
            oracle = predictions.pop("privileged_reveal")
            truths = [row[2] for row in tests]
            errors = [abs(p - truth) for p, truth in zip(oracle, truths, strict=True)]
            if (any(truth == 0 or (p > 0) != (truth > 0) for truth, p in zip(truths, oracle))
                    or not fmean(errors) < 1e-8):
                return {"split": split, "instrumentation_passed": False, "reason": "geometry"}
            oracle_errors.extend(errors)
            agents = practice_arms(genome, practice, start, sign_seed)
            test_seeds = [derived_seed(bank, lifetime, "probe", i) for i in range(len(tests))]
            turns = {name: test_turns(agent, tests, test_seeds) for name, agent in agents.items()}
            turns.update({name: [int(b >= 0) for b in values] for name, values in predictions.items()})
            for name in names:
                counts = cell_counts(tests, turns[name])
                totals[g][name].update(counts)
                if g != 4:
                    macro_cells[name].extend(accuracy(counts, motion) for motion in ("moving", "stationary"))
            retention.append(post_practice_pair(
                agents["intact"], agents["nonplastic"],
                derived_seed(earlier_seed, PROTOCOL, split, lifetime, "post-practice")))
        for name in names:
            clusters[name].append(fmean(macro_cells[name]))
    contrasts = {name: paired_advantage(clusters["intact"], clusters[name],
                                        derived_seed(bank, "cluster-bootstrap"))
                 for name in names if name != "intact"}
    fresh = fresh_skills(champion, earlier_seed, split)
    post = post_practice_summary(retention)
    intact_cells = [accuracy(totals[g]["intact"], motion) for g in gains[1:]
                    for motion in ("stationary", "moving")]
    return {
        "split": split, "starts": STARTS[split], "instrumentation_passed": True,
        "privileged_instrument": {"signed_bearing_mae_deg": fmean(oracle_errors),
                                  "correct_side": len(oracle_errors), "total": len(oracle_errors)},
        "cells": {g: {n: count_rows(totals[g][n]) for n in names} for g in gains},
        "changed_gain_macro_accuracy": {n: fmean(clusters[n]) for n in names},
        "matched_advantages": contrasts, "fresh_skills": fresh, "post_practice": post,
        "decision": bank_decision(intact_cells, contrasts, True, fresh["passed"], post["passed"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true", help="Disposable genome; fixture gains 2,4,6")
    source.add_argument("--run", type=Path, help="Read-only source archive")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--fingerprint")
    parser.add_argument("--sensory-seed", type=int, required=True)
    parser.add_argument("--earlier-seed", type=int, required=True)
    args = parser.parse_args()
    if args.synthetic:
        if args.generation is not None or args.fingerprint is not None:
            parser.error("Synthetic fixtures do not use archive coordinates")
        genome = replace(random_genome(Random(derived_seed(args.sensory_seed, "fixture"))), trace_decay=.5)
        champion = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
    else:
        if args.generation is None or not args.fingerprint:
            parser.error("An archive requires --generation and --fingerprint")
        champion = verified_nominee(args.run, args.generation, args.fingerprint)
    banks = []
    for split in STARTS:
        bank = run_bank(champion, args.sensory_seed, args.earlier_seed, split, synthetic=args.synthetic)
        banks.append(bank)
        if not bank["instrumentation_passed"]:
            break
    print(json.dumps({"protocol": PROTOCOL, "synthetic_fixture": args.synthetic,
                      "source_generation": args.generation, "fingerprint": champion["fingerprint"],
                      "sensory_seed": args.sensory_seed, "earlier_seed": args.earlier_seed,
                      "banks": banks, "both_banks_passed": len(banks) == 2 and
                      all(bank.get("decision", {}).get("passed", False) for bank in banks)},
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
