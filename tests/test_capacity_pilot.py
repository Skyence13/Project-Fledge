import random
import unittest
from dataclasses import replace

from wisp.capacity_pilot import SIZE, founders, pathway_nodes
from wisp.core import Connection, Genome, MAX_EDGES, MAX_NODES, mutate
from wisp.experiment import identifier, next_generation


class CapacityPilotTests(unittest.TestCase):
    def test_identical_founders_and_legacy_mutation(self):
        population = founders(200)
        self.assertEqual(population, founders(200))
        self.assertEqual(len(population), SIZE)
        self.assertTrue(all(len(g.nodes) == MAX_NODES for g in population))
        self.assertEqual(len({identifier(g) for g in population}), SIZE)
        scores = list(range(SIZE))
        self.assertEqual(next_generation(population, scores, (True, True), 0, 1),
                         next_generation(population, scores, (True, True), 0, 1, node_limit=12))

    def test_growth_is_explicit_and_keeps_edge_budget(self):
        parent = founders(112)[0]
        for seed in range(100):
            larger = mutate(parent, random.Random(seed), rules=True, wiring=True, node_limit=24)
            if len(larger.nodes) > 12:
                break
        self.assertEqual(len(larger.nodes), 13)
        self.assertLessEqual(len(larger.edges), MAX_EDGES)
        self.assertEqual(Genome.from_dict(larger.to_dict(), node_limit=24), larger)
        with self.assertRaises(ValueError):
            Genome.from_dict(larger.to_dict())
        with self.assertRaises(ValueError):
            mutate(larger, random.Random(0), rules=True, wiring=True)
        with self.assertRaises(ValueError):
            mutate(parent, random.Random(0), rules=True, wiring=True, node_limit=25)

    def test_directed_paths_exclude_disconnected_and_zero_weight_edges(self):
        genome = founders(0)[0]
        edges = (Connection(-1, 2, 1), Connection(2, 0, 1), Connection(-2, 3, 1),
                 Connection(3, 1, 0), Connection(4, 1, 1))
        self.assertEqual(pathway_nodes(replace(genome, edges=edges)), 2)
