"""Held-out delayed credit and interference assays for inherited two-receptor Wisp genomes."""

from collections import deque
from concurrent.futures import CancelledError
from itertools import product
import random

from .core import Genome, Wisp
from .experiment import CONDITIONS, FixedReflex, cue_order, derived_seed, identifier


PROTOCOL = "wisp-delay-retention-v2"
DELAYS = (0, 1, 3)
ASSIGNMENTS = tuple(product((0, 1), repeat=2))
REPEATS_LIMIT = 8
NEURAL_AGENTS = ("neural", "no_plasticity", "uninformative_feedback")
TEACH_PER_CUE = 8
RECALL_PER_CUE = 4


def _metrics():
    rows = []
    for delay in DELAYS:
        for key, name in (("first", "First response per cue"), ("recall", "Four responses per cue")):
            rows.append({"id": f"delay_{delay}_{key}", "label": f"{delay} intervening choices · {name}",
                         "task": "delay"})
        rows.append({"id": f"delay_{delay}_opposite_recall",
                     "label": f"{delay} intervening choices · Opposite cue targets", "task": "delay"})
    rows.append({"id": "retain_before_first", "label": "A immediately after learning · First response", "task": "retention"})
    for arm, name in (("sham", "B exposure only"), ("learn_b", "B taught")):
        for key, desc in (("a_first", "First A recall"), ("a_recall", "Four A recalls"), ("b_first", "First B recall")):
            rows.append({"id": f"retain_{arm}_{key}", "label": f"{name} · {desc}", "task": "retention"})
    return rows


METRICS = _metrics()


class LaggedAssociation:
    """Associative lookup with the protocol's announced constant feedback lag in choices."""

    def __init__(self, seed, delay):
        self.rng = random.Random(seed)
        self.buffer = deque(maxlen=delay + 1)
        self.learned = {}

    def choose(self, cue):
        preferred = self.learned.get(cue)
        random_guess = self.rng.randrange(2)
        action = random_guess if preferred is None else preferred
        self.buffer.append((cue, action))
        return action

    def feedback(self, sign):
        cue, own_action = self.buffer[0]
        self.learned[cue] = own_action if sign == 1 else 1 - own_action


def _agent(mode, genome, seed, delay=0):
    if mode == "association":
        return LaggedAssociation(seed, delay)
    if mode == "reflex":
        return FixedReflex()
    return Wisp(genome, seed=seed, plastic=mode != "no_plasticity")


def _balanced_signs(seed, length):
    signs = [1, -1] * (length // 2)
    random.Random(seed).shuffle(signs)
    return signs


def _lesson(seed, repeat):
    base = derived_seed(seed, PROTOCOL, "held-out", repeat)
    rng = random.Random(derived_seed(base, "distractors"))
    distractors = [[rng.randrange(2) for _ in range(max(DELAYS))] for _ in range(2 * TEACH_PER_CUE)]
    return {
        "repeat": repeat + 1, "seed": str(base),
        "delay_lifetime_seed": str(derived_seed(base, "delay-lifetime")),
        "retention_lifetime_seed": str(derived_seed(base, "retention-lifetime")),
        "teaching_cues": cue_order(base, "delay-teach", TEACH_PER_CUE),
        "probe_cues": cue_order(base, "delay-probe", RECALL_PER_CUE),
        "distractors": distractors,
        "delay_control_signs": _balanced_signs(derived_seed(base, "delay-control"), 2 * TEACH_PER_CUE),
        "a_control_signs": _balanced_signs(derived_seed(base, "a-control"), TEACH_PER_CUE),
        "b_control_signs": _balanced_signs(derived_seed(base, "b-control"), TEACH_PER_CUE),
    }


def _counts():
    return {row["id"]: [0, 0] for row in METRICS}


def _observe(counts, key, success):
    counts[key][0] += int(success)
    counts[key][1] += 1


def _pack(counts):
    return {key: {"correct": correct, "total": total, "accuracy": correct / total}
            for key, (correct, total) in counts.items()}


def _combine(aggregate, results):
    for key, row in results.items():
        aggregate[key][0] += row["correct"]
        aggregate[key][1] += row["total"]


def _grade(agent, success, mode, control_sign):
    sign = control_sign if mode == "uninformative_feedback" else (1 if success else -1)
    agent.feedback(sign)


def _delayed(counts, agent, mode, lesson, mapping, delay):
    for index, cue in enumerate(lesson["teaching_cues"]):
        source_action = agent.choose(cue)
        for other in lesson["distractors"][index][:delay]:
            agent.choose(other)
        _grade(agent, source_action == mapping[cue], mode, lesson["delay_control_signs"][index])
    seen = set()
    for cue in lesson["probe_cues"]:
        matched = agent.choose(cue) == mapping[cue]
        _observe(counts, f"delay_{delay}_recall", matched)
        if mapping[0] != mapping[1]:
            _observe(counts, f"delay_{delay}_opposite_recall", matched)
        if cue not in seen:
            _observe(counts, f"delay_{delay}_first", matched)
            seen.add(cue)


def _retention(counts, agent, mode, lesson, mapping, cue_a, teach_b):
    cue_b = 1 - cue_a
    arm = "learn_b" if teach_b else "sham"
    for control_sign in lesson["a_control_signs"]:
        _grade(agent, agent.choose(cue_a) == mapping[cue_a], mode, control_sign)
    before = agent.choose(cue_a) == mapping[cue_a]
    if not teach_b:
        # Both arms have identical prefixes. Count this pre-interference probe once.
        _observe(counts, "retain_before_first", before)
    for control_sign in lesson["b_control_signs"]:
        matched = agent.choose(cue_b) == mapping[cue_b]
        if teach_b:
            _grade(agent, matched, mode, control_sign)
    for index in range(RECALL_PER_CUE):
        recalled_a = agent.choose(cue_a) == mapping[cue_a]
        recalled_b = agent.choose(cue_b) == mapping[cue_b]
        _observe(counts, f"retain_{arm}_a_recall", recalled_a)
        if index == 0:
            _observe(counts, f"retain_{arm}_a_first", recalled_a)
            _observe(counts, f"retain_{arm}_b_first", recalled_b)


def _evaluate(mode, genome, lessons, check_cancel):
    counts = _counts()
    for lesson in lessons:
        for mapping in ASSIGNMENTS:
            check_cancel()
            for delay in DELAYS:
                agent = _agent(mode, genome, int(lesson["delay_lifetime_seed"]), delay)
                _delayed(counts, agent, mode, lesson, mapping, delay)
            for cue_a in (0, 1):
                for teach_b in (False, True):
                    agent = _agent(mode, genome, int(lesson["retention_lifetime_seed"]))
                    _retention(counts, agent, mode, lesson, mapping, cue_a, teach_b)
    return _pack(counts)


def _enroll(checkpoint, member_ids):
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
        raise ValueError("Provide a version-1 population checkpoint.")
    size = checkpoint.get("per_condition")
    if type(size) is not int or not 2 <= size <= 16:
        raise ValueError("Invalid number of candidates per condition.")
    expected = [f"{name}:{slot}" for name in CONDITIONS for slot in range(size)]
    rows = checkpoint.get("members")
    if (not isinstance(rows, list) or len(rows) != len(expected) or
            any(not isinstance(row, dict) or row.get("id") != name for name, row in zip(expected, rows))):
        raise ValueError("The checkpoint's population does not match its experimental conditions.")
    if member_ids is None:
        member_ids = expected
    if (not isinstance(member_ids, (tuple, list)) or not 1 <= len(member_ids) <= len(rows)
            or any(not isinstance(name, str) or name not in expected for name in member_ids)
            or len(set(member_ids)) != len(member_ids)):
        raise ValueError("Choose one or more distinct candidate IDs from the snapshot.")
    selected = set(member_ids)
    genomes = []
    try:
        for row in rows:
            if row["id"] in selected:
                genomes.append((row["id"], Genome.from_dict(row["wisp"]["genome"])))
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("Invalid inherited genome in population snapshot.") from error
    return genomes


def run_next_plan(checkpoint, *, seed=41, repeats=2, member_ids=None, progress=None, cancel_event=None):
    """Evaluate new lifetimes from the source genomes. The input checkpoint stays untouched."""
    if type(seed) is not int or not -(2 ** 31) <= seed < 2 ** 31:
        raise ValueError("Choose a signed 32-bit integer lesson seed.")
    if type(repeats) is not int or not 1 <= repeats <= REPEATS_LIMIT:
        raise ValueError("Choose 1 to 8 held-out lesson seeds.")

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Memory evaluation cancelled.")

    check_cancel()
    members = _enroll(checkpoint, member_ids)
    lessons = [_lesson(seed, index) for index in range(repeats)]
    if progress is not None:
        progress(0, len(members))
    baselines = {mode: _evaluate(mode, None, lessons, check_cancel) for mode in ("association", "reflex")}
    cached = {}
    totals = {mode: _counts() for mode in NEURAL_AGENTS}
    variants = []
    for done, (name, genome) in enumerate(members, 1):
        check_cancel()
        fingerprint = identifier(genome)
        if fingerprint not in cached:
            cached[fingerprint] = {mode: _evaluate(mode, genome, lessons, check_cancel) for mode in NEURAL_AGENTS}
        results = cached[fingerprint]
        for mode in NEURAL_AGENTS:
            _combine(totals[mode], results[mode])
        variants.append({"id": name, "condition": name.split(":")[0], "genome": fingerprint,
                         "results": results["neural"], "no_plasticity": results["no_plasticity"],
                         "uninformative_feedback": results["uninformative_feedback"]})
        if progress is not None:
            progress(done, len(members))
    return {
        "protocol": PROTOCOL,
        "source": {"seed": checkpoint["seed"], "evolution_seed": str(checkpoint.get("evolution_seed", checkpoint["seed"])),
                   "generation": checkpoint["generation"], "step": checkpoint["step"],
                   "enrolled": len(members), "unique_genomes": len(cached)},
        "settings": {"seed": seed, "repeats": repeats, "delays": list(DELAYS),
                     "teaching_events_per_cue": TEACH_PER_CUE, "recall_trials_per_cue": RECALL_PER_CUE,
                     "lifetimes_per_candidate_and_agent": repeats * len(ASSIGNMENTS) * (len(DELAYS) + 4)},
        "manifest": {
            "assignment_order": [list(mapping) for mapping in ASSIGNMENTS], "retention_a_roles": [0, 1],
            "lessons": lessons,
            "delay": "Each source choice receives one signed grade after 0, 1 or 3 actual distractor choices. All distractors are ungraded. The association baseline buffers its own decisions and knows the fixed block delay.",
            "retention": "Fresh paired lifetimes receive eight A teaching events and an ungraded A probe. Both see eight B cues; only the B-taught arm receives B feedback. Four ungraded A/B probe pairs follow, always A first.",
            "scoring": "First response per cue is included in the four-response recall metric. Scores count correct unprompted actions against the private mapping. Neural summaries pool candidate slots; the shared baselines are evaluated once per lesson bank.",
            "opposite_targets": "The opposite-target subset uses only (left, right) and (right, left). A cue-independent preference can solve either same-target mapping but scores 50% on balanced opposite-target probes.",
            "controls": "The independent-sign control uses balanced shuffled signs shared across mappings. The nonplastic Wisp keeps its recurrent dynamics. All agents receive the same observation schedule in each arm.",
            "scope": "This held-out lesson bank starts fresh lifetimes of inherited genomes. It measures performance on two binary cues and is never used for selection during this evaluation.",
        },
        "metrics": [row.copy() for row in METRICS], "variants": variants, "baselines": baselines,
        "summary": {**{mode: _pack(counts) for mode, counts in totals.items()}, **baselines},
    }
