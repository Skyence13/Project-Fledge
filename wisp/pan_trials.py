"""Matched pan histories and calibrated references for an archived-nominee test."""

from random import Random

from .pan_prediction import (
    BLANK, NOMINAL_GAIN, TARGET_SPEEDS, PanHistory, fit_gain, observe, projected_frame,
)


def make_trials(seed, repeats, gain):
    """Mirror complete speed/command factorials, then shuffle the entire phase."""
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if int(gain) != gain or not 0 < gain <= 6:
        raise ValueError("expected an integer motor gain in [1, 6]")
    rng = Random(seed)
    rows = []
    for _ in range(repeats):
        for speed in TARGET_SPEEDS:
            for command in (1, 2):
                # Odd dyadics span (-9, 9) and cannot reach zero after any
                # integer displacement. The target stays in view at the reveal.
                resolution = 2 ** 24
                start = (2 * rng.randrange(9 * resolution) + 1) / resolution - 9
                for sign in (-1, 1):
                    history = PanHistory(observe(sign * start), observe(sign * (start + speed)),
                                         sign * command, BLANK)
                    bearing = sign * (start + 3 * speed - gain * command)
                    rows.append((history, observe(bearing), bearing, sign * speed))
    rng.shuffle(rows)
    return rows


def _public_bearings(history):
    # The projected two-channel voltage encodes angle linearly for an illuminated target.
    first_left, first_right = projected_frame(history.first)
    before_left, before_right = projected_frame(history.before_pan)
    first = 30 * (first_right - first_left)
    before = 30 * (before_right - before_left)
    return before, before + 2 * (before - first)


class GradeOnly:
    """Receive projected-geometry estimates and one binary grade per practice choice."""

    def __init__(self):
        self.errors = {2: 0, 4: 0, 6: 0}

    def gain(self):
        return min(self.errors, key=lambda k: (self.errors[k], abs(k - NOMINAL_GAIN), k))

    def feedback(self, inertial, command, choice, grade):
        target_side = choice if grade == 1 else not choice
        for hypothesis in self.errors:
            self.errors[hypothesis] += (inertial - command * hypothesis >= 0) != target_side


def reference_predictions(practice, tests):
    """Return signed predictions for each test in order. Only the instrument fit sees reveals."""
    grade_only = GradeOnly()
    for history, _, truth, _ in practice:
        _, inertial = _public_bearings(history)
        command = history.pan_command
        choice = inertial - command * grade_only.gain() >= 0
        grade = 1 if choice == (truth >= 0) else -1
        grade_only.feedback(inertial, command, choice, grade)

    fitted = fit_gain((history, reveal) for history, reveal, _, _ in practice)
    estimates = {name: [] for name in (
        "last_seen", "motion_only", "nominal_body", "grade_only", "privileged_reveal",
    )}
    for history, _, _, _ in tests:
        before, inertial = _public_bearings(history)
        command = history.pan_command
        estimates["last_seen"].append(before)
        estimates["motion_only"].append(inertial)
        estimates["nominal_body"].append(inertial - NOMINAL_GAIN * command)
        estimates["grade_only"].append(inertial - grade_only.gain() * command)
        estimates["privileged_reveal"].append(inertial - fitted * command)
    return estimates
