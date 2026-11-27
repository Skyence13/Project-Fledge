"""Paired decision rules and independent earlier-skill controls for the archived pan test."""

import math
import random
from statistics import fmean, quantiles

from .core import Wisp
from .experiment import Lesson, PROBE_ROUNDS, run_lesson
from .mixed_delay import DELAYS, transfer


def fresh_skills(champion, earlier_seed, split):
    """Run the established mixed-transfer bank on fresh copies of the nominee."""
    report = transfer(champion, earlier_seed, split)
    metrics = report["arms"]["intact"]
    fixed = [metrics["fixed_recall"][lag] for lag in DELAYS]
    pooled = sum(row["correct"] for row in fixed) / sum(row["total"] for row in fixed)
    drop = pooled - metrics["mixed_recall"]["accuracy"]
    gates = {
        "mixed": metrics["mixed_recall"]["accuracy"] >= .90,
        "each_fixed": all(row["accuracy"] >= .90 for row in fixed),
        "pooled_fixed_minus_mixed": drop <= .05 + 1e-12,
        "each_fixed_one_shot": all(metrics["fixed_one_shot"][lag]["accuracy"] >= .75 for lag in DELAYS),
        "immediate": metrics["old_skills"]["immediate"]["accuracy"] >= .90,
        "reversal": metrics["old_skills"]["reversal"]["accuracy"] >= .90,
    }
    return {"passed": all(gates.values()), "gates": gates, "metrics": metrics,
            "earlier_seed": earlier_seed, "bank_seed": report["bank_seed"], "split": split,
            "protocol": report["protocol"], "pooled_fixed_minus_mixed": drop}


def post_practice_pair(intact, nonplastic, lesson_seed):
    """Branch from both practiced states; test both binary assignments on fresh clones."""
    checkpoints = {"intact": intact.checkpoint(), "nonplastic": nonplastic.checkpoint()}
    if (checkpoints["intact"]["rng"] != checkpoints["nonplastic"]["rng"]
            or checkpoints["intact"]["genome"] != checkpoints["nonplastic"]["genome"]):
        raise ValueError("Post-practice controls require the same genome and matched random streams.")
    scores = {}
    for arm, checkpoint in checkpoints.items():
        # Resume each acquired state, including synapses and recurrent activity.
        # Re-enable learning for the prior lesson, independently for each mapping.
        episodes = [run_lesson(Wisp.restore(dict(checkpoint, plastic=True)), Lesson(lesson_seed, bit))
                    for bit in (0, 1)]
        scores[arm] = {
            "recall": fmean(row["after_teaching"] for row in episodes),
            "reversal": fmean(row["after_reteaching"] for row in episodes),
        }
    return scores


def post_practice_summary(pairs):
    if not pairs:
        raise ValueError("Provide matched post-practice pairs.")
    rates = {
        arm: {phase: fmean(pair[arm][phase] for pair in pairs) for phase in ("recall", "reversal")}
        for arm in ("intact", "nonplastic")
    }
    difference = {phase: rates["intact"][phase] - rates["nonplastic"][phase]
                  for phase in ("recall", "reversal")}
    return {"passed": all(delta >= -.05 - 1e-12 for delta in difference.values()),
            "rates": rates, "difference": difference, "body_lifetime_pairs": len(pairs),
            "probes_per_phase_per_arm": len(pairs) * 2 * (2 * PROBE_ROUNDS)}


def paired_advantage(intact_values, control_values, bootstrap_seed):
    """One macro-score per lifetime; resample the paired lifetime vectors together."""
    if len(intact_values) != len(control_values) or not intact_values:
        raise ValueError("Provide one intact and control macro-score per matched lifetime.")
    deltas = [a - b for a, b in zip(intact_values, control_values)]
    if not all(0 <= value <= 1 for arm in (intact_values, control_values) for value in arm):
        raise ValueError("Expected finite lifetime accuracies.")
    rng = random.Random(bootstrap_seed)
    draws = [fmean(rng.choices(deltas, k=len(deltas))) for _ in range(2000)]
    percentiles = quantiles(draws, n=40, method="inclusive")
    return {"difference": fmean(deltas), "ci95": [percentiles[0], percentiles[-1]],
            "lifetimes": len(deltas), "draws": len(draws), "unit": "paired lifetime"}


def bank_decision(intact_cells, margins, oracle_ok, fresh_ok, post_ok):
    """Apply the preregistered point-estimate gates independently on each bank."""
    cells = list(intact_cells.values() if isinstance(intact_cells, dict) else intact_cells)
    if len(cells) != 4 or not all(0 <= v <= 1 for v in cells):
        raise ValueError("Expected four finite changed-gain by motion accuracies.")
    deltas = {}
    for arm in ("nonplastic", "independent"):
        value = margins[arm]
        delta = value["difference"] if isinstance(value, dict) else value
        if not math.isfinite(delta) or abs(delta) > 1:
            raise ValueError("Invalid matched advantage.")
        deltas[arm] = delta
    macro = fmean(cells)
    gates = {"reference_geometry": oracle_ok, "macro_accuracy": macro >= .75 - 1e-12,
             "minimum_cell_accuracy": min(cells) >= .65 - 1e-12,
             "advantage_nonplastic": deltas["nonplastic"] >= .10 - 1e-12,
             "advantage_independent": deltas["independent"] >= .10 - 1e-12,
             "fresh_earlier_skills": fresh_ok, "post_practice_earlier_skills": post_ok}
    return {"passed": all(gates.values()), "gates": gates, "changed_gain_macro_accuracy": macro}
