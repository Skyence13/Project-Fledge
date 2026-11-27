"""Synthetic graded vision and active-pan prediction benchmark for reference models."""

import argparse
from dataclasses import dataclass
import json
from math import fsum
from random import Random

from .core import Wisp, random_genome


RECEPTOR_ANGLES = (-30, -15, 0, 15, 30)
BLANK = (0.0,) * len(RECEPTOR_ANGLES)
NOMINAL_GAIN = 4.0
PAN_COMMANDS = (-2, -1, 1, 2)
TARGET_SPEEDS = (-1, 0, 1)
WINDOW_DEGREES = 1.0


def observe(bearing):
    """Five triangular receptor responses; out-of-field and shuttered frames are dark."""
    if abs(bearing) > RECEPTOR_ANGLES[-1]:
        return BLANK
    return tuple(max(0.0, 1 - abs(bearing - angle) / 15) for angle in RECEPTOR_ANGLES)


def centroid(frame):
    brightness = fsum(frame)
    if not brightness:
        raise ValueError("A dark frame contains no bearing")
    return fsum(angle * light for angle, light in zip(RECEPTOR_ANGLES, frame)) / brightness


@dataclass(frozen=True)
class PanHistory:
    first: tuple
    before_pan: tuple
    pan_command: int
    shuttered: tuple


def trials(seed, bank, phase, repetitions, gain):
    """Common trajectories across reference models and gains; disjoint random streams by role."""
    random = Random(f"graded-pan-v1/{seed}/{bank}/{phase}")
    conditions = [(speed, command) for speed in TARGET_SPEEDS for command in PAN_COMMANDS]
    for _ in range(repetitions):
        random.shuffle(conditions)
        for speed, command in conditions:
            start = random.uniform(-9, 9)
            # Camera is still through t=1, then pans once. Target motion persists.
            history = PanHistory(observe(start), observe(start + speed), command, BLANK)
            at_reveal = start + 3 * speed - gain * command
            yield history, observe(at_reveal), at_reveal, speed


def inertial_bearing(history):
    before = centroid(history.before_pan)
    return before + 2 * (before - centroid(history.first))


def fit_gain(calibration):
    """Estimate degrees per command using visible calibration reveals and own commands."""
    numerator = denominator = 0.0
    for history, reveal in calibration:
        command = history.pan_command
        numerator += command * (inertial_bearing(history) - centroid(reveal))
        denominator += command ** 2
    return numerator / denominator


def predict(history, model, gain=0.0):
    """Return the predicted bearing before the first post-shutter frame is visible."""
    if model == "last_seen":
        return centroid(history.before_pan)
    if model == "motion_only":
        return inertial_bearing(history)
    if model in ("nominal_body", "calibrated_body"):
        return inertial_bearing(history) - gain * history.pan_command
    raise ValueError(model)


def summarize(errors):
    return {
        "trials": len(errors),
        "mean_absolute_error_deg": fsum(errors) / len(errors),
        "inside_one_degree": sum(error <= WINDOW_DEGREES for error in errors),
    }


def projected_frame(frame):
    # Linear public body adapter: two overlapping hemifields preserve graded bearing.
    right = fsum(level * (angle + 30) / 60 for level, angle in zip(frame, RECEPTOR_ANGLES))
    left = fsum(level * (30 - angle) / 60 for level, angle in zip(frame, RECEPTOR_ANGLES))
    return left, right


def wisp_turn(wisp, history):
    wisp.choose_levels(*projected_frame(history.first))
    wisp.choose_levels(*projected_frame(history.before_pan))
    # Signed motor-command copy on its own tick. The gaze decision is sampled
    # on the dark tick, before the camera can see the target again.
    command = history.pan_command / 2
    wisp.choose_levels(max(0., -command), max(0., command))
    return wisp.choose_levels(*projected_frame(history.shuttered))


def founder_turn_scores(seed, bank, gain, plastic):
    founder = random_genome(Random(f"graded-pan-v1/{seed}/genome"))
    learner = Wisp(founder, seed=seed, plastic=plastic)
    for history, reveal, _, _ in trials(seed, bank, "calibration", 1, gain):
        turn = wisp_turn(learner, history)
        learner.feedback(1 if turn == int(centroid(reveal) >= 0) else -1)
    correct = total = right_targets = 0
    for history, _, target, _ in trials(seed, bank, "test", 4, gain):
        right = int(target >= 0)
        correct += wisp_turn(learner, history) == right
        right_targets += right
        total += 1
    return {"gain": gain, "plastic": plastic, "correct_first_turn": correct,
            "trials": total, "right_targets": right_targets}


def evaluate(seed, bank):
    if bank not in ("development", "validation", "confirmation"):
        raise ValueError("Unknown trajectory bank")
    rows = []
    founder_scores = []
    for gain in (2.0, NOMINAL_GAIN, 6.0):
        for plastic in (False, True):
            founder_scores.append(founder_turn_scores(seed, bank, gain, plastic))
        calibration = trials(seed, bank, "calibration", 1, gain)
        measured = fit_gain((history, reveal) for history, reveal, _, _ in calibration)
        tests = list(trials(seed, bank, "test", 4, gain))
        for model, assumed in (("last_seen", 0), ("motion_only", 0),
                               ("nominal_body", NOMINAL_GAIN), ("calibrated_body", measured)):
            errors = {"stationary": [], "moving": []}
            correct_turns = 0
            for history, _, target, speed in tests:
                estimate = predict(history, model, assumed)
                correct_turns += (estimate >= 0) == (target >= 0)
                errors["moving" if speed else "stationary"].append(abs(estimate - target))
            rows.append({
                "actual_gain": gain, "model": model,
                "correct_first_turn": correct_turns, "turn_trials": len(tests),
                "scores": {group: summarize(values) for group, values in errors.items()},
            })
    return {
        "protocol": "graded-pan-v1", "seed": seed, "bank": bank, "rows": rows,
        "synthetic_founder_turns": founder_scores,
        "calibration_examples_per_body": len(PAN_COMMANDS) * len(TARGET_SPEEDS),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--bank", choices=("development", "validation", "confirmation"), required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.seed, args.bank), indent=2))


if __name__ == "__main__":
    main()
