"""Describe developmental evidence from a Wisp cohort's actual measurement history."""

from dataclasses import asdict
from statistics import mean

from .lab import PLAN


def _probe_evidence(lab):
    rows = []
    for phase_index, stage in enumerate(PLAN):
        if stage.teaching:
            continue
        correct, observed = lab.phase_counts[stage.id]
        expected = len(lab.lessons) * len(lab.members) * stage.trials
        elapsed = sum(row[1] == phase_index for row in lab.schedule[:lab.step]) * len(lab.members)
        if observed == expected:
            status = "complete"
        elif observed:
            status = "partial"
        elif elapsed:
            status = "unrecorded"
        else:
            status = "pending"
        rows.append({"id": stage.id, "label": stage.label, "correct": correct if observed else None,
                     "observed": observed, "expected": expected, "unrecorded": elapsed - observed,
                     "accuracy": correct / observed if observed else None, "status": status})
    return rows


def _fitness(members):
    correct = sum(m.correct for m in members)
    observed = sum(m.probes for m in members)
    scored = [m for m in members if m.probes]
    best = max(scored, key=lambda m: m.fitness) if scored else None
    return {"mean": mean(m.fitness for m in scored) if scored else None,
            "best": best.fitness if best else None,
            "best_label": best.inspect()["label"] if best else None,
            "tied_best": sum(m.fitness == best.fitness for m in scored) if best else 0,
            "correct": correct, "observed": observed, "scored_candidates": len(scored)}


def _trend(lab, fitness):
    previous = next((row for row in reversed(lab.history)
                     if row["generation"] == lab.generation - 1), {})
    prior = previous.get("mean_fitness")
    delta = fitness["mean"] - prior if lab.awaiting_next and prior is not None else None
    if delta is not None:
        text = (f"Mean fitness changed {delta * 100:+.1f} percentage points from G{lab.generation - 1}. "
                "Each generation was evaluated on its own seeded cue sequence.")
    elif not previous:
        text = "A comparison needs a previous completed generation on this lineage."
    else:
        text = "Complete this generation to compare its mean fitness with the previous one."
    return {"available": delta is not None, "previous_generation": previous.get("generation"),
            "previous_mean": prior, "delta_mean": delta, "text": text}


def _interpretation(lab, evidence, fitness):
    if any(row["unrecorded"] for row in evidence):
        return ("This checkpoint predates some phase counters. Recorded observations and combined "
                "fitness are shown separately. Missing responses cannot be reconstructed.")
    if not lab.step:
        return "Each candidate begins the two-cue task with its inherited connections. The first probes measure its initial responses."
    recall = evidence[1]
    reversal = evidence[3]
    if recall["status"] == reversal["status"] == "complete":
        return (f"After teaching, the cohort chose the assigned direction on {recall['correct']} of "
                f"{recall['observed']} probes. After the assignment was reversed and retaught, it "
                f"chose the new direction on {reversal['correct']} of {reversal['observed']} probes. "
                "Both tests measured immediate recall across two fresh lifetimes per candidate.")
    if fitness["observed"]:
        return (f"The cohort has completed {fitness['observed']} of {fitness['expected']} scored "
                "recall and reversal choices. Its score is provisional while the lesson is in progress.")
    return "The cohort is collecting its first responses and teaching feedback. Fitness becomes available during the recall probes."


def assess(lab):
    """Read live or restored state on demand. Does not advance a Wisp or sample its RNG."""
    members = lab.members
    population = len(members)
    evidence = _probe_evidence(lab)
    per_candidate_budget = len(lab.lessons) * sum(s.trials for s in PLAN if s.scored)
    fitness = _fitness(members)
    fitness["expected"] = population * per_candidate_budget
    groups = {}
    for m in members:
        groups.setdefault(m.condition, []).append(m)
    conditions = [{"id": condition, "label": condition.replace("_", " ").title(),
                   **_fitness(group), "expected": len(group) * per_candidate_budget,
                   "candidates": len(group)} for condition, group in groups.items()]
    node_counts = [len(m.genome.nodes) for m in members]
    edge_counts = [len(m.genome.edges) for m in members]
    changed = 0
    for m in members:
        weights = m.wisp.checkpoint()["weights"]
        changed += sum(w != edge.weight for w, edge in zip(weights, m.genome.edges))
    next_lifetime, phase, *_ = lab.schedule[-1 if lab.awaiting_next else lab.step]
    return {
        "generation": lab.generation, "complete": lab.awaiting_next,
        "seed": lab.seed, "evolution_seed": str(lab.evolution_seed),
        "plan": [asdict(stage) for stage in PLAN], "phase_index": phase,
        "lifetime": next_lifetime + 1, "lifetimes_per_generation": len(lab.lessons),
        "step": lab.step, "steps_per_generation": len(lab.schedule),
        "headline": ("Completed two-cue lesson" if lab.awaiting_next
                     else "Two-cue lesson in progress" if lab.step else "Ready for the first baseline probe"),
        "interpretation": _interpretation(lab, evidence, fitness), "evidence": evidence,
        "fitness": fitness, "conditions": conditions,
        "population": {"candidates": population, "unique_genomes": len({m.fingerprint for m in members}),
                       "min_nodes": min(node_counts), "max_nodes": max(node_counts),
                       "min_edges": min(edge_counts), "max_edges": max(edge_counts),
                       "total_neurons": sum(node_counts),
                       "adaptive_neurons": sum(n.adaptation > 0 for m in members for n in m.genome.nodes)},
        "memory": {"weights_changed": changed, "total_weights": sum(edge_counts),
                   "context": (f"Weights that differ from their inherited values in lifetime {lab.lifetime + 1}. "
                               "The following lifetime starts fresh. Weight changes alone do not measure recall.")},
        "trend": _trend(lab, fitness), "manual_overrides": lab.manual_overrides,
        "scope": ("Fitness averages the two no-feedback recall probes across the counterbalanced mappings. "
                  "Candidates within a generation share cue orders. These are training sequences, and "
                  "manual overrides count cohort teaching steps that differed from the planned target."),
        "untested": ["Retention after an intervening task", "Learning from delayed feedback",
                     "Transfer to a new task or physical body"],
    }
