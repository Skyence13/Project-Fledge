"""Synthetic 12-vs-24 neuron pilot. Run with python -m wisp.capacity_pilot.

Start both arms at the ordinary ceiling to exercise the cap intervention in a
short run. This is a sensitivity test, not an estimate for natural founders.
The existing 64-edge limit and all mutation/selection settings stay matched.
The runner uses no campaign files and prints only aggregate counts and IDs.
"""
import argparse
from collections import Counter
from dataclasses import replace
import json
import random

from .challenge_evolution import _memory, _original, _schedule
from .core import Connection, MAX_NODES, MOTORS, PILOT_MAX_NODES, SENSORS, random_genome
from .experiment import derived_seed, identifier, next_generation


PROTOCOL = "wisp-neuron-cap-pilot-v1"
SIZE = 6
ROUNDS = 6


def founders(seed):
    """Split eight randomly selected edges per fixture, without selecting on scores."""
    population = []
    for index in range(SIZE):
        rng = random.Random(derived_seed(seed, "fixture", index))
        genome = random_genome(rng)
        nodes, edges = list(genome.nodes), list(genome.edges)
        for new_id in range(len(nodes), MAX_NODES):
            edge = edges.pop(rng.randrange(len(edges)))
            nodes.append(replace(rng.choice(nodes[2:]), id=new_id))
            edges.extend((Connection(edge.source, new_id, 1.0),
                          Connection(new_id, edge.target, edge.weight)))
        population.append(replace(genome, nodes=tuple(nodes), edges=tuple(edges)))
    return population


def pathway_nodes(genome):
    """Count nodes on a nonzero-weight directed walk from sensor to motor."""
    edges = [e for e in genome.edges if e.weight != 0]

    def reachable(start, reverse):
        visited = set(start)
        frontier = list(start)
        while frontier:
            current = frontier.pop()
            for edge in edges:
                source, target = (edge.target, edge.source) if reverse else (edge.source, edge.target)
                if source == current and target not in visited:
                    visited.add(target)
                    frontier.append(target)
        return visited

    return len(reachable(SENSORS, False) & reachable(MOTORS, True) - set(SENSORS))


def arm(population, seed, ceiling):
    exposures = Counter()
    max_size = max_path = 0
    for generation in range(1, ROUNDS + 1):
        _, lessons, _ = _schedule(seed, 2, generation, "selection")
        scores = []
        for genome in population:
            metrics = _memory(genome, lessons, 5)
            scores.append((metrics["credit"]["accuracy"] + metrics["delayed"]["accuracy"]) / 2)
            size, path = len(genome.nodes), pathway_nodes(genome)
            max_size, max_path = max(size, max_size), max(path, max_path)
            exposures["candidate_generations"] += 1
            exposures["at_ceiling"] += size == ceiling
            exposures["above_12"] += size > MAX_NODES
            exposures["path_above_12"] += path > MAX_NODES
            exposures["node_sum"] += size
            exposures["edge_sum"] += len(genome.edges)
        if generation < ROUNDS:
            population = next_generation(population, scores, (True, True), seed, generation,
                                         evolve_trace=True, node_limit=ceiling)
    nominee = population[max(range(len(scores)), key=scores.__getitem__)]
    banks = {}
    for split in ("validation", "confirmation"):
        _, lessons, old = _schedule(seed, 2, ROUNDS, split)
        banks[split] = {**_memory(nominee, lessons, 5), **_original(nominee, old)}
    return {"ceiling": ceiling, "exposure": dict(exposures), "max_nodes": max_size,
            "max_path_nodes": max_path, "nominee_nodes": len(nominee.nodes),
            "nominee_path_nodes": pathway_nodes(nominee), "nominee_id": identifier(nominee),
            "banks": banks}


def paired_run(seed):
    experiment_seed = derived_seed(seed, PROTOCOL)
    initial = founders(experiment_seed)
    return {"seed": seed, "founders": [identifier(g) for g in initial],
            "arms": [arm(initial, experiment_seed, cap) for cap in (MAX_NODES, PILOT_MAX_NODES)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=72841)
    parser.add_argument("--pairs", type=int, choices=range(1, 9), default=3)
    args = parser.parse_args()
    print(json.dumps({"protocol": PROTOCOL, "population_per_arm": SIZE, "rounds": ROUNDS,
                      "replicates": [paired_run(args.seed + i) for i in range(args.pairs)]}, indent=2))


if __name__ == "__main__":
    main()
