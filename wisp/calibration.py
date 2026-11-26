"""Measure the current cohort on a disposable clone and budget live clock work."""

from concurrent.futures import CancelledError
from contextlib import closing
from math import ceil, floor, isfinite
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, perf_counter

from .lab import LabController
from .lineage import LineageArchive


MAX_RATE = 65_536
MAX_CALIBRATION_TRIALS = 16_384
HEADROOM = 0.30
LIVE_MAX_BATCH = 32
LIVE_MAX_BATCH_SECONDS = 0.012
LIVE_YIELD_SECONDS = 0.001


def _presets(cap):
    rates = [1]
    while rates[-1] * 2 < cap:
        rates.append(rates[-1] * 2)
    if rates[-1] != cap:
        rates.append(cap)
    return rates


def _estimate(rates, representative):
    # Complete generation windows include training, probes, selection and archive work.
    # A lower quartile is less sensitive to unusually fast windows. With few observations
    # leave more time for the UI, durable storage and changes in host contention.
    ordered = sorted(rates)
    lower = ordered[(len(ordered) - 1) // 4]
    headroom = HEADROOM if representative and len(ordered) >= 3 else 0.50
    cap = max(1, min(MAX_RATE, floor(lower * (1 - headroom))))
    return cap, headroom


def calibrate(checkpoint, *, duration=0.35, cancel_event=None, archive_dir=None):
    """Return a conservative cohort-trial rate. A real-time trial spans one simulated second.

    The temporary SQLite file uses the production archive and commit path. Supplying the
    lineage directory places the test on the same volume. Between batches, release the
    CPU with the same minimum wait used by the service; timer granularity affects throughput.
    """
    if not isfinite(duration) or not 0.05 <= duration <= 1.0:
        raise ValueError("Choose a calibration duration of 0.05 to 1 second.")

    cancellation = cancel_event if cancel_event is not None else Event()

    def check_cancel():
        if cancellation.is_set():
            raise CancelledError("Laboratory calibration cancelled.")

    check_cancel()
    clone = LabController.restore(checkpoint)
    clone.command({"action": "configure", "mode": "auto", "running": True, "auto_advance": True})
    completed_at_start = clone.completed
    generation_at_start = clone.generation
    population = len(clone.members)
    feedback_at_start = clone.total_feedback
    window_size = len(clone.schedule)
    rates = []
    trials = 0
    with TemporaryDirectory(prefix="fledge-calibration-", dir=archive_dir) as directory:
        with closing(LineageArchive(Path(directory) / "measurement.sqlite3")) as archive:
            branch, _ = archive.start(checkpoint)
            start = window_start = perf_counter()
            while trials < MAX_CALIBRATION_TRIALS:
                check_cancel()
                batch_start = monotonic()
                for _ in range(min(LIVE_MAX_BATCH, window_size - trials % window_size)):
                    before = clone.completed
                    clone.tick()
                    if clone.completed > before:
                        # Private generations are numbered from one regardless of the source.
                        archive.record(branch, clone.completed - completed_at_start,
                                       clone.checkpoint(), clone.history[-1])
                    trials += 1
                    if monotonic() - batch_start >= LIVE_MAX_BATCH_SECONDS:
                        break
                cancellation.wait(LIVE_YIELD_SECONDS)
                check_cancel()
                now = perf_counter()
                elapsed = now - start
                if trials % window_size == 0 and now > window_start:
                    rates.append(window_size / (now - window_start))
                    window_start = now
                if elapsed >= duration and (len(rates) >= 3 or elapsed >= min(1.0, duration + 0.40)):
                    break
    elapsed = max(now - start, 1e-9)
    training_trials = (clone.total_feedback - feedback_at_start) // population
    representative = (0 < training_trials < trials and clone.generation > generation_at_start
                      and clone.completed > completed_at_start and bool(rates))
    sample_rates = rates or [trials / elapsed]
    cap, headroom = _estimate(sample_rates, representative)
    if not representative:
        # A prefix with no measured full cycle cannot establish a durable sustained speed.
        # Retain real-time pacing until a fresh probe includes both selection and a commit.
        cap = 1
    return {
        "cap": cap, "presets": _presets(cap), "measured_trials_per_second": trials / elapsed,
        "samples": len(rates), "min_trials_per_second": min(sample_rates),
        "median_trials_per_second": median(sample_rates), "max_trials_per_second": max(sample_rates),
        "trials": trials, "training_trials": training_trials, "probe_trials": trials - training_trials,
        "generations": clone.completed - completed_at_start, "elapsed_seconds": elapsed,
        "headroom": headroom, "representative": representative,
        "archive_location": "lineage volume" if archive_dir is not None else "system temporary directory",
        "scope": "Current cohort clone, teaching, probes, breeding, checkpoint compression, durable SQLite generation commits and cooperative batch waits.",
    }


class AdaptivePacer:
    """Estimate sustainable active work, derate quickly, and recover toward a tested ceiling."""

    def __init__(self, cap):
        if type(cap) is not int or not 1 <= cap <= MAX_RATE:
            raise ValueError("Invalid calibrated pacing limit.")
        self.cap = cap
        self.capacity = cap
        self._cost = (1 - HEADROOM) / cap
        self._trials = 0
        self._seconds = 0.0

    def effective_rate(self, requested):
        if type(requested) is not int or requested < 1:
            raise ValueError("Requested pace must be a positive integer.")
        return min(requested, self.capacity)

    def observe(self, trials, elapsed_seconds, requested, *, overrun_seconds=0.0):
        """Observe active work including checkpoint writes, excluding deliberate pacing waits.

        Optional overrun is excess dispatch latency before the batch began. Bound a single
        host-suspend event so a long pause cannot depress the estimator indefinitely.
        """
        if type(trials) is not int or trials < 1 or type(requested) is not int or requested < 1:
            raise ValueError("Pacing observations need positive trials and requested rate.")
        if not (isfinite(elapsed_seconds) and elapsed_seconds >= 0
                and isfinite(overrun_seconds) and overrun_seconds >= 0):
            raise ValueError("Pacing times must be finite and nonnegative.")
        self._trials += trials
        self._seconds += elapsed_seconds + min(overrun_seconds, max(0.05, trials / requested))
        if self._trials < 32 and self._seconds < 0.05:
            return
        cost = max(self._seconds / self._trials, 1e-9)
        # Rising work per trial (e.g. denser circuits) influences the estimate faster.
        weight = 0.65 if cost > self._cost else 0.20
        self._cost = (1 - weight) * self._cost + weight * cost
        estimate = max(1, min(self.cap, floor((1 - HEADROOM) / self._cost)))
        self.capacity = min(estimate, self.capacity + max(1, ceil(self.capacity / 10)))
        self._trials = 0
        self._seconds = 0.0
