"""Bounded one-blank population evolution. The caller supplies the pinned Wisp checkout."""

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
from random import Random
from statistics import fmean, pstdev

from wisp import campaign
from wisp.core import Genome, Wisp, mutate
from wisp.experiment import CONDITIONS, Lesson, PROBE_ROUNDS, derived_seed, identifier, run_lesson
from wisp.one_blank_practice import practice_targets, probe_lifetime, train
from wisp.pan_review import fresh_skills, paired_advantage


PROTOCOL = "one-blank-stationary-breeding-v1"
GENERATIONS = 4
TRAIN_LIFETIMES = 6
LIFETIMES = {"validation": 32, "confirmation": 64}
SEEDS = {"training": 28112751, "validation": 28112752, "confirmation": 28112753,
         "validation_earlier": 28112754, "confirmation_earlier": 28112755}
THRESHOLDS = {"min_blank_gain_each_side": .05, "positive_paired_lower_bound_each_side": True,
              "min_visible_accuracy": .75, "max_visible_drop": .05, "max_earlier_drop": .05,
              "min_mixed_fixed_immediate_reversal": .90, "min_fixed_one_shot": .75,
              "max_pooled_fixed_minus_mixed": .05}
SOURCE_FINGERPRINT = "b6d09864da20dde330fa8712acf97b0a8a11ab3ea90f55625e46bfe235243616"
ARMS = ("finalist", "frozen_ancestor", "last_visible_decoder")
CELLS = ("blank", "left_target", "right_target", "visible", "dark_only")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def enroll(population):
    require(set(population) == set(CONDITIONS), "Unexpected source selection groups")
    members = []
    for group in sorted(population):
        require(len(population[group]) == 4, "Expected four founders per group")
        for slot, data in enumerate(population[group]):
            genome = Genome.from_dict(data)
            fingerprint = identifier(genome)
            members.append(dict(group=group, slot=slot, genome=genome.to_dict(), fingerprint=fingerprint,
                                founder_slot=slot, founder_fingerprint=fingerprint, parent_fingerprint=None,
                                parent_slot=None, birth_generation=0))
    return members


def load_founders(source_dir, population_sha256, record_sha256):
    with closing(campaign._readonly(Path(source_dir) / "campaign.sqlite3")) as db:
        require(db.execute("PRAGMA user_version").fetchone()[0] == 1, "Unexpected archive version")
        row = db.execute("SELECT population,report FROM rounds WHERE generation=5").fetchone()
    require(row is not None, "Missing frozen G5 cohort")
    population, record = campaign._unpack(row[0]), json.loads(row[1])
    require(campaign._digest(population) == population_sha256 and campaign._digest(record) == record_sha256,
            "Frozen source digest differs")
    members = enroll(population)
    nominee = next(m for m in members if f'{m["group"]}:{m["slot"]}' == record["champion_id"])
    require(record["generation"] == 5 and nominee["fingerprint"] == record["fingerprint"] == SOURCE_FINGERPRINT,
            "Frozen source nominee differs")
    return members


def score(correct, total):
    return {"correct": correct, "total": total, "accuracy": correct / total}


def measures(choices):
    left = choices["blank"][0, 0]
    right = choices["blank"][1, 1]
    counts = {key: choices[key][0, 0] + choices[key][1, 1] for key in ("blank", "visible", "dark_only")}
    return dict(counts, left_target=left, right_target=right)


def one_lifetime(genomes, seed, index):
    targets = practice_targets(derived_seed(seed, index, "practice-targets"))
    action_seed = derived_seed(seed, index, "practice-actions")
    endpoints = {arm: train(genome, targets, action_seed)[0] for arm, genome in genomes.items()}
    tests = {arm: {c: Counter() for c in ("visible", "blank", "dark_only")} for arm in endpoints}
    refs = {key: Counter() for key in ("visible_decoder", "last_seen_blank", "stateless_blank")}
    probe_lifetime({"agents": endpoints, "index": index}, seed, tests, refs)
    # Cache the response to the actual first-frame sensory amplitudes; on dark/dark a tie chooses left.
    cells = {"blank": refs["last_seen_blank"], "visible": refs["visible_decoder"], "dark_only": refs["stateless_blank"]}
    outputs = {}
    for arm, choices in {**tests, "last_visible_decoder": cells}.items():
        for counts in choices.values():
            require(all(counts[side, 0] + counts[side, 1] == 8 for side in (0, 1)), "Unbalanced mirror budget")
        outputs[arm] = measures(choices)
        require(outputs[arm]["dark_only"] == 8, "Dark-only mirror calibration failed")
    require(outputs["last_visible_decoder"] == dict(blank=16, left_target=8, right_target=8, visible=16, dark_only=8),
            "Last-visible reference calibration failed")
    return outputs, endpoints


def cell_budget(key):
    return 8 if key in ("left_target", "right_target") else 16


def average_cells(rows):
    return {cell: score(sum(row[cell] for row in rows), cell_budget(cell) * len(rows)) for cell in CELLS}


def training_rank(row):
    cells = row["scores"]
    return (min(cells["left_target"]["correct"], cells["right_target"]["correct"]),
            cells["blank"]["correct"], cells["visible"]["correct"])


def generation(members, generation_index):
    """Score one cohort; next_members is None after the fourth offspring cohort."""
    require(type(generation_index) is int and 0 <= generation_index <= GENERATIONS, "Unexpected generation")
    expected = {(group, slot) for group in CONDITIONS for slot in range(4)}
    require(len(members) == len(expected) and {(m["group"], m["slot"]) for m in members} == expected,
            "Missing or duplicated population slots")
    bank = derived_seed(SEEDS["training"], PROTOCOL, "selection", generation_index)
    entries = []
    for m in sorted(members, key=lambda row: (row["group"], row["slot"])):
        genome = Genome.from_dict(m["genome"])
        require(identifier(genome) == m["fingerprint"], "Candidate fingerprint differs")
        lives = [one_lifetime({"candidate": genome}, bank, i)[0]["candidate"] for i in range(TRAIN_LIFETIMES)]
        entries.append(dict({k: v for k, v in m.items() if k != "genome"}, scores=average_cells(lives),
                            neurons=len(genome.nodes), connections=len(genome.edges)))
    members_by_slot = {(m["group"], m["slot"]): m for m in members}
    ranked = sorted(entries, key=training_rank, reverse=True)
    eligible = [row for row in ranked if row["fingerprint"] != row["founder_fingerprint"]]
    require(generation_index < GENERATIONS or eligible, "Final cohort has no changed descendant")
    chosen = (eligible if generation_index == GENERATIONS else ranked)[0]
    nominee = members_by_slot[chosen["group"], chosen["slot"]]
    accuracy = [row["scores"]["blank"]["accuracy"] for row in entries]
    population = dict(count=len(entries), mean_accuracy=fmean(accuracy), accuracy_sd=pstdev(accuracy),
                      mean_neurons=fmean(r["neurons"] for r in entries),
                      mean_connections=fmean(r["connections"] for r in entries), generation=generation_index,
                      basis="One-blank training probes pooled within each Wisp; spread is population SD.")
    summary = dict(generation=generation_index, population=population, members=entries, nominee=chosen,
                   lifetimes_per_genome=TRAIN_LIFETIMES, training_bank_seed=str(bank))
    if generation_index == GENERATIONS:
        return None, summary, nominee
    children = []
    for group, (rules, wiring) in sorted(CONDITIONS.items()):
        parents = [row for row in ranked if row["group"] == group][:2]
        children.append(dict(members_by_slot[group, parents[0]["slot"]], slot=0))
        for slot in range(1, 4):
            seed = derived_seed(SEEDS["training"], PROTOCOL, "breed", generation_index + 1, group, slot)
            selected = Random(derived_seed(seed, "parent")).choice(parents)
            parent = members_by_slot[group, selected["slot"]]
            genome = mutate(Genome.from_dict(parent["genome"]), Random(derived_seed(seed, "mutate")),
                            rules=rules, wiring=wiring, evolve_trace=True)
            children.append(dict(group=group, slot=slot, genome=genome.to_dict(), fingerprint=identifier(genome),
                                 founder_slot=parent["founder_slot"], founder_fingerprint=parent["founder_fingerprint"],
                                 parent_fingerprint=parent["fingerprint"], parent_slot=parent["slot"],
                                 birth_generation=generation_index + 1))
    return children, summary, nominee


def endpoint_skills(agent, seed):
    rows = [run_lesson(Wisp.restore(agent.checkpoint()), Lesson(seed, bit)) for bit in (0, 1)]
    return {name: fmean(row[phase] for row in rows)
            for name, phase in (("recall", "after_teaching"), ("reversal", "after_reteaching"))}


def bank_gates(bank):
    thresholds = THRESHOLDS
    outcomes = bank["held_out"]
    contrasts = bank["visible_contrasts"]
    gates = {f"{side}_gain": contrasts[side]["difference"] >= thresholds["min_blank_gain_each_side"]
             and contrasts[side]["ci95"][0] > 0 for side in ("left_target", "right_target")}
    gates.update(instrumentation=bank["instrumentation_passed"],
                 visible=outcomes["finalist"]["visible"]["accuracy"] >= thresholds["min_visible_accuracy"],
                 visible_retained=contrasts["visible"]["difference"] >= -thresholds["max_visible_drop"] - 1e-12,
                 fresh_earlier_skills=bank["fresh_skills"]["passed"],
                 earlier_skills_retained=all(d >= -thresholds["max_earlier_drop"] - 1e-12
                                             for d in bank["fresh_minus_ancestor"].values()),
                 post_practice_skills=bank["post_practice"]["passed"])
    return gates


def retained(bank):
    return all(bank["gates"][key] for key in
               ("visible", "visible_retained", "fresh_earlier_skills", "earlier_skills_retained", "post_practice_skills"))


def older_rates(fresh):
    metrics = fresh["metrics"]
    return dict(mixed_recall=metrics["mixed_recall"]["accuracy"],
                **{name: value["accuracy"] for name, value in metrics["old_skills"].items()})


def evaluate_bank(nominee_member, original_founders, split):
    require(split in LIFETIMES, "Unknown held-out bank")
    nominee = nominee_member
    ancestors = [m for m in original_founders if (m["group"], m["slot"]) ==
                 (nominee["group"], nominee["founder_slot"])]
    require(len(ancestors) == 1, "Ambiguous or missing founder")
    ancestor = ancestors[0]
    arms = {"finalist": nominee, "frozen_ancestor": ancestor}
    genomes = {arm: Genome.from_dict(m["genome"]) for arm, m in arms.items()}
    require(all(identifier(genomes[arm]) == m["fingerprint"] for arm, m in arms.items()), "Genome hash mismatch")
    require(nominee["birth_generation"] > 0 and ancestor["birth_generation"] == 0 and
            ancestor["fingerprint"] == nominee["founder_fingerprint"] != nominee["fingerprint"],
            "Expected a changed, traceable descendant and its frozen founder")
    n = LIFETIMES[split]
    seed = derived_seed(SEEDS[split], PROTOCOL, split)
    earlier = SEEDS[split + "_earlier"]
    samples = {arm: [] for arm in ARMS}
    retention = {arm: [] for arm in arms}
    for i in range(n):
        counts, endpoints = one_lifetime(genomes, seed, i)
        for arm in ARMS:
            samples[arm].append(counts[arm])
        for arm, agent in endpoints.items():
            retention[arm].append(endpoint_skills(agent, derived_seed(earlier, PROTOCOL, split, i, "post-practice")))
    scores = {arm: average_cells(rows) for arm, rows in samples.items()}
    contrasts = {cell: paired_advantage(
        [r[cell] / cell_budget(cell) for r in samples["finalist"]],
        [r[cell] / cell_budget(cell) for r in samples["frozen_ancestor"]], derived_seed(seed, cell, "bootstrap"))
        for cell in ("left_target", "right_target", "blank", "visible")}
    rates = {arm: {phase: fmean(r[phase] for r in rows) for phase in ("recall", "reversal")}
             for arm, rows in retention.items()}
    drops = {phase: rate - rates["frozen_ancestor"][phase] for phase, rate in rates["finalist"].items()}
    fresh = {arm: fresh_skills(m, earlier, split) for arm, m in arms.items()}
    before, after = older_rates(fresh["frozen_ancestor"]), older_rates(fresh["finalist"])
    bank = dict(split=split, lifetimes=n, bank_seed=str(seed), held_out=scores, visible_contrasts=contrasts,
                finalist_fingerprint=nominee["fingerprint"], founder_fingerprint=ancestor["fingerprint"],
                founder_group=ancestor["group"], founder_slot=ancestor["slot"], instrumentation_passed=True,
                fresh_skills=fresh["finalist"], ancestor_fresh_skills=fresh["frozen_ancestor"],
                fresh_minus_ancestor={k: after[k] - before[k] for k in after},
                post_practice=dict(rates=rates, difference=drops, body_lifetime_pairs=n,
                                   probes_per_phase_per_arm=n * 4 * PROBE_ROUNDS,
                                   passed=all(d >= -THRESHOLDS["max_earlier_drop"] - 1e-12 for d in drops.values())))
    bank["gates"] = bank_gates(bank)
    bank["passed"] = all(bank["gates"].values())
    validate_bank(bank, split)
    return bank


def validate_bank(bank, split):
    require(split in LIFETIMES and bank["split"] == split and bank["lifetimes"] == LIFETIMES[split],
            "Bank identity or lifetime budget differs")
    require(set(bank["held_out"]) == set(ARMS) and bank["instrumentation_passed"] is True, "Missing matched control")
    for arm, cells in bank["held_out"].items():
        require(set(cells) == set(CELLS), "Missing probe condition")
        for key, value in cells.items():
            hits, trials = value["correct"], cell_budget(key) * LIFETIMES[split]
            require(type(hits) is int and 0 <= hits <= trials and value == score(hits, trials), "Invalid probe count")
            if key == "dark_only":
                require(2 * hits == trials, "Dark-only imbalance")
            elif arm == "last_visible_decoder":
                require(hits == trials, "Reference failed")
        require(cells["left_target"]["correct"] + cells["right_target"]["correct"] == cells["blank"]["correct"],
                "Blank aggregate differs from side scores")
    for key in ("left_target", "right_target", "blank", "visible"):
        actual = bank["visible_contrasts"][key]
        gap = bank["held_out"]["finalist"][key]["accuracy"] - bank["held_out"]["frozen_ancestor"][key]["accuracy"]
        lo, hi = actual["ci95"]
        require(abs(actual["difference"] - gap) < 1e-12 and -1 <= lo <= hi <= 1 and actual["lifetimes"] == LIFETIMES[split],
                "Invalid paired contrast")
    fresh = bank["fresh_skills"]
    ancestor = bank["ancestor_fresh_skills"]
    for row in (fresh, ancestor):
        require(set(row["gates"]) == {"mixed", "each_fixed", "pooled_fixed_minus_mixed", "each_fixed_one_shot",
                                     "immediate", "reversal"} and row["passed"] == all(row["gates"].values()) and
                row["split"] == split and row["earlier_seed"] == SEEDS[split + "_earlier"], "Invalid earlier-skill bank")
    before, after = older_rates(ancestor), older_rates(fresh)
    require(bank["fresh_minus_ancestor"] == {k: after[k] - before[k] for k in before}, "Earlier-skill contrast differs")
    post = bank["post_practice"]
    require(post["body_lifetime_pairs"] == LIFETIMES[split] and
            post["probes_per_phase_per_arm"] == LIFETIMES[split] * 4 * PROBE_ROUNDS, "Earlier-lesson budget differs")
    for arm in ("finalist", "frozen_ancestor"):
        require(all(0 <= post["rates"][arm][k] <= 1 for k in ("recall", "reversal")), "Invalid post-practice rate")
    delta = {k: post["rates"]["finalist"][k] - post["rates"]["frozen_ancestor"][k] for k in ("recall", "reversal")}
    require(post["difference"] == delta and post["passed"] == all(v >= -THRESHOLDS["max_earlier_drop"] - 1e-12
                                                                for v in delta.values()), "Post-practice retention differs")
    require(bank["gates"] == bank_gates(bank) and bank["passed"] == all(bank["gates"].values()), "Bank decision differs")
