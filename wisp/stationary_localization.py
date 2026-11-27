"""Read-only two-frame stationary localization assay. No practice, pan or grades."""

import argparse
from collections import Counter
import json
from pathlib import Path
from random import Random

from .archived_pan import verified_nominee
from .core import Genome, Wisp, random_genome
from .experiment import derived_seed, identifier
from .pan_prediction import observe, projected_frame
from .pan_review import fresh_skills, paired_advantage

PROTOCOL = "stationary-localization-v1"
DARK = (0., 0.)


def responses(genome, levels, seed):
    """Reset both the lifetime and the action RNG between counterfactuals."""
    outputs = {}
    for arm, first, second in (
        ("visible", levels, levels), ("blank", levels, DARK), ("dark_only", DARK, DARK)
    ):
        agent = Wisp(genome, seed=seed)
        agent.choose_levels(*first)
        outputs[arm] = agent.choose_levels(*second)
    # Both references see exactly the same two receptor levels as the Wisp.
    side = int(levels[1] > levels[0])
    outputs.update(decoder_visible=side, decoder_blank=0, last_seen_blank=side)
    return outputs


def evaluate(champion, seed, earlier_seed, split):
    genome = Genome.from_dict(champion["genome"])
    bank = derived_seed(seed, PROTOCOL, split)
    count = {"validation": 128, "confirmation": 256}[split]
    totals = Counter()
    pairs = []
    for pair in range(count):
        # Four eccentricity bands, equally represented, mirrored about zero.
        angle = Random(derived_seed(bank, pair, "angle")).uniform(1, 7) + 7 * (pair % 4)
        action_seed = derived_seed(bank, pair, "action")
        scores = Counter()
        for sign in (-1, 1):
            levels = projected_frame(observe(sign * angle))
            for arm, turn in responses(genome, levels, action_seed).items():
                scores[arm] += int(turn == int(sign > 0))
        totals.update(scores)
        pairs.append(scores)
    trials = 2 * count
    contrasts = {}
    for arm, control in (("visible", "dark_only"), ("blank", "dark_only"), ("visible", "blank")):
        diff = paired_advantage([p[arm] / 2 for p in pairs], [p[control] / 2 for p in pairs],
                                derived_seed(bank, "bootstrap"))
        contrasts[f"{arm}_minus_{control}"] = {
            "difference": diff["difference"], "ci95": diff["ci95"], "resampling_unit": "mirror pair"}
    return {"split": split, "bank_seed": bank, "trials_per_arm": trials,
            "correct": dict(totals), "contrasts": contrasts,
            "instrumentation_passed": totals["decoder_visible"] == totals["last_seen_blank"] == trials
            and totals["decoder_blank"] == totals["dark_only"] == count,
            "earlier_skills": fresh_skills(champion, earlier_seed, split)}


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
            parser.error("Specify the registered generation and fingerprint")
        champion = verified_nominee(args.run, args.generation, args.fingerprint)
    banks = [evaluate(champion, args.seed, args.earlier_seed, split)
             for split in ("validation", "confirmation")]
    print(json.dumps({"protocol": PROTOCOL, "synthetic": args.synthetic, "seed": args.seed,
                      "earlier_seed": args.earlier_seed, "generation": args.generation,
                      "fingerprint": champion["fingerprint"], "banks": banks}, allow_nan=False))


if __name__ == "__main__":
    main()
