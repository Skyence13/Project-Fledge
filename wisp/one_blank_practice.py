"""Matched one-blank practice on a stationary target, with independent donor grades."""

import argparse
from collections import Counter
import json
from pathlib import Path
from random import Random

from .archived_pan import verified_nominee
from .core import Genome, Wisp, random_genome
from .experiment import derived_seed, identifier
from .pan_prediction import observe, projected_frame
from .pan_review import fresh_skills, paired_advantage, post_practice_pair, post_practice_summary
from .stationary_localization import DARK

PROTOCOL = "stationary-one-blank-practice-v1"
LIFETIMES = {"validation": 32, "confirmation": 64}
ARMS = ("accurate", "yoked", "nonplastic")
CONDITIONS = ("visible", "blank", "dark_only")


def practice_targets(seed):
    rng = Random(seed)
    targets = [side * (rng.uniform(1, 7) + 7 * band)
               for band in range(4) for side in (-1, 1) for _ in range(4)]
    rng.shuffle(targets)
    return targets


def train(genome, targets, action_seed, *, plastic=True, grades=None):
    """Assign exactly one grade, after the second turn. Carry state between episodes."""
    if grades is not None and len(grades) != len(targets):
        raise ValueError("Provide one donor grade per target")
    agent = Wisp(genome, seed=action_seed, plastic=plastic)
    turns, applied = [], []
    for index, angle in enumerate(targets):
        agent.choose_levels(*projected_frame(observe(angle)))
        turn = agent.choose_levels(*DARK)
        truthful = 1 if turn == int(angle > 0) else -1
        grade = truthful if grades is None else grades[index]
        agent.feedback(grade)
        applied.append(grade)
        turns.append(turn)
    return agent, applied, turns


def train_pair(genome, bank, pair_index):
    """Complete both donors before assigning either recipient's shuffled grades."""
    lives = []
    for index in range(2 * pair_index, 2 * pair_index + 2):
        seed = derived_seed(bank, index, "practice-actions")
        targets = practice_targets(derived_seed(bank, index, "practice-targets"))
        agent, grades, turns = train(genome, targets, seed)
        lives.append(dict(index=index, action_seed=seed, targets=targets, agents={"accurate": agent},
                          grades={"accurate": grades}, turns={"accurate": turns}))
    for recipient, donor in zip(lives, reversed(lives)):
        recipient["donor"] = donor["index"]
        shuffled = donor["grades"]["accurate"].copy()
        Random(derived_seed(bank, recipient["index"], "grade-permutation")).shuffle(shuffled)
        for arm, plastic, grades in (("yoked", True, shuffled), ("nonplastic", False, None)):
            agent, applied, turns = train(genome, recipient["targets"], recipient["action_seed"],
                                          plastic=plastic, grades=grades)
            recipient["agents"][arm] = agent
            recipient["grades"][arm] = applied
            recipient["turns"][arm] = turns
    return lives


def probe_turns(agent, levels, seed):
    state = dict(agent.checkpoint(), rng=Random(seed).getstate())
    turns = {}
    for arm, first, second in (("visible", levels, levels), ("blank", levels, DARK),
                                ("dark_only", DARK, DARK)):
        clone = Wisp.restore(state)
        clone.choose_levels(*first)
        turns[arm] = clone.choose_levels(*second)
    return turns


def counts_by_side(counts):
    """Joint choices and labels retain right-turn bias separately on each side."""
    total = sum(counts.values())
    correct = counts[0, 0] + counts[1, 1]
    return {"correct": correct, "total": total, "accuracy": correct / total,
            "right_turns_by_target": {
                label: {"right_turns": counts[side, 1], "total": counts[side, 0] + counts[side, 1]}
                for side, label in enumerate(("left", "right"))}}


def probe_lifetime(life, bank, test, reference):
    blank_correct = Counter()
    for pair in range(8):
        seed = derived_seed(bank, life["index"], "probe-actions", pair)
        rng = Random(derived_seed(bank, life["index"], "probe-angle", pair))
        angle = rng.uniform(1, 7) + 7 * (pair % 4)
        for side, bearing in enumerate((-angle, angle)):
            levels = projected_frame(observe(bearing))
            # The visible decoder compares receptor amplitudes. The one-frame cache
            # keeps that answer. On a tie the stateless decoder chooses left.
            current = int(levels[1] > levels[0])
            dark = int(DARK[1] > DARK[0])
            for name, turn in (("visible_decoder", current), ("last_seen_blank", current),
                               ("stateless_blank", dark)):
                reference[name][side, turn] += 1
            for arm, agent in life["agents"].items():
                for condition, turn in probe_turns(agent, levels, seed).items():
                    test[arm][condition][side, turn] += 1
                    if condition == "blank":
                        blank_correct[arm] += turn == side
    return blank_correct


def evaluate(champion, seed, earlier_seed, split):
    genome = Genome.from_dict(champion["genome"])
    if identifier(genome) != champion["fingerprint"] or seed == earlier_seed:
        raise ValueError("Verify nominee fingerprint and provide distinct sensory and earlier-skill seeds")
    bank = derived_seed(seed, PROTOCOL, split)
    starts = LIFETIMES[split]
    practice = {arm: Counter() for arm in ARMS}
    approvals = Counter()
    test = {arm: {cond: Counter() for cond in CONDITIONS} for arm in ARMS}
    refs = {name: Counter() for name in ("visible_decoder", "last_seen_blank", "stateless_blank")}
    clusters = {arm: [] for arm in ARMS}
    retention = []
    assignments = []
    for pair in range(starts // 2):
        lives = train_pair(genome, bank, pair)
        pair_correct = Counter()
        for life, partner in zip(lives, reversed(lives)):
            if (life["donor"] != partner["index"] or life["donor"] == life["index"] or
                    Counter(life["grades"]["yoked"]) != Counter(partner["grades"]["accurate"])):
                raise ValueError("Yoked donor or approval balance mismatch")
            assignments.append(life["donor"])
            for arm in ARMS:
                approvals[arm] += life["grades"][arm].count(1)
                for angle, turn in zip(life["targets"], life["turns"][arm], strict=True):
                    practice[arm][int(angle > 0), turn] += 1
            pair_correct.update(probe_lifetime(life, bank, test, refs))
            lesson = derived_seed(earlier_seed, PROTOCOL, split, life["index"], "post-practice")
            retention.append(post_practice_pair(life["agents"]["accurate"], life["agents"]["nonplastic"], lesson))
        for arm in ARMS:
            clusters[arm].append(pair_correct[arm] / 32)
    scores = {arm: {c: counts_by_side(obs) for c, obs in cells.items()} for arm, cells in test.items()}
    reference = {name: counts_by_side(obs) for name, obs in refs.items()}
    trials = 16 * starts
    instrument = {
        "balanced_grades": approvals["accurate"] == approvals["yoked"],
        "unique_donors": sorted(assignments) == list(range(starts)),
        "probe_budget": all(cell["total"] == trials for cells in scores.values() for cell in cells.values()),
        "visible_decoder": reference["visible_decoder"]["correct"] == trials,
        "last_seen_blank": reference["last_seen_blank"]["correct"] == trials,
        "stateless_blank": reference["stateless_blank"]["correct"] == trials // 2,
        "dark_only": all(cells["dark_only"]["correct"] == trials // 2 for cells in scores.values()),
    }
    margins = {}
    for arm in ("yoked", "nonplastic"):
        diff = paired_advantage(clusters["accurate"], clusters[arm], derived_seed(bank, "bootstrap"))
        margins[arm] = {"difference": diff["difference"], "ci95": diff["ci95"], "draws": diff["draws"],
                        "unit": "disjoint donor pair", "pairs": starts // 2}
    old = fresh_skills(champion, earlier_seed, split)
    post = post_practice_summary(retention)
    visible = scores["accurate"]["visible"]["accuracy"]
    gates = {
        "instrumentation": all(instrument.values()), "blank_advantage_yoked": margins["yoked"]["difference"] >= .10,
        "positive_lower_bound_yoked": margins["yoked"]["ci95"][0] > 0,
        "blank_advantage_nonplastic": margins["nonplastic"]["difference"] >= .05,
        "visible_accuracy": visible >= .75,
        "visible_retained": visible >= scores["nonplastic"]["visible"]["accuracy"] - .05 - 1e-12,
        "fresh_earlier_skills": old["passed"], "post_practice_skills": post["passed"]}
    return {"split": split, "bank_seed": bank, "lifetimes": starts, "donor_assignments": assignments,
            "practice": {arm: counts_by_side(obs) for arm, obs in practice.items()},
            "approvals": dict(approvals), "held_out": scores, "references": reference,
            "blank_accurate_minus_control": margins, "fresh_skills": old, "post_practice": post,
            "instrumentation": instrument, "instrumentation_passed": gates["instrumentation"],
            "gates": gates, "passed": all(gates.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--synthetic", action="store_true")
    mode.add_argument("--run", type=Path)
    parser.add_argument("--generation", type=int)
    parser.add_argument("--fingerprint")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--earlier-seed", type=int, required=True)
    args = parser.parse_args()
    if args.synthetic:
        if args.generation is not None or args.fingerprint is not None:
            parser.error("Synthetic fixtures require no archive coordinates")
        genome = random_genome(Random(derived_seed(args.seed, PROTOCOL, "fixture")))
        champion = {"genome": genome.to_dict(), "fingerprint": identifier(genome)}
    else:
        if args.generation is None or not args.fingerprint:
            parser.error("Provide both registered archive coordinates")
        champion = verified_nominee(args.run, args.generation, args.fingerprint)
    banks = []
    for split in LIFETIMES:
        banks.append(evaluate(champion, args.seed, args.earlier_seed, split))
        if not banks[-1]["instrumentation_passed"]:
            break
    print(json.dumps({"protocol": PROTOCOL, "synthetic": args.synthetic, "fingerprint": champion["fingerprint"],
                      "generation": args.generation, "seed": args.seed, "earlier_seed": args.earlier_seed,
                      "banks": banks, "both_banks_passed": len(banks) == 2 and all(b["passed"] for b in banks)},
                     allow_nan=False))


if __name__ == "__main__":
    main()
