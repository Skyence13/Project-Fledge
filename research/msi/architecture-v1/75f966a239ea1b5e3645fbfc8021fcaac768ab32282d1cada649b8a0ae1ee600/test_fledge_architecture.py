from copy import deepcopy
from pathlib import Path
from random import Random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent/"fledge-one-blank-pr13"))
from wisp.core import Connection, Genome, Neuron, random_genome
import fledge_architecture as metrics
import fledge_blank_breeding as original


class ArchitectureTests(unittest.TestCase):
    def test_directed_reachability_with_cycle_and_isolated_motor(self):
        neurons = tuple(Neuron(i,.5,1.,0.) for i in range(6))
        edges = tuple(Connection(a,b,.2) for a,b in ((-1,2),(2,3),(3,2),(3,0),(-2,4),(5,1)))
        genome = Genome(neurons,edges,.3)
        row = metrics.topology(genome)
        self.assertEqual((3,2,1),(row["sensor_to_motor_nodes"],row["sensor_to_motor_interneurons"],
                                  row["connected_sensor_motor_pairs"]))
        self.assertEqual([3,None,None,None],row["shortest_edges_by_sensor_motor"])

    def test_cap_filters_only_actual_wiring_births_and_no_phantom_final_generation(self):
        genome = Genome(tuple(Neuron(i,.5,1.,0.) for i in range(12)), (Connection(-1,0,.2),),.3).to_dict()
        members = [dict(group=g,slot=0,genome=genome) for g in ("wired","rules")]
        summary = dict(generation=0,members=[dict(group=g,slot=0,score=n) for n,g in enumerate(("wired","rules"))])
        children = [dict(group=g,parent_slot=0,genome=genome,birth_generation=b)
                    for g,b in (("wired",1),("wired",0),("rules",1))]
        args = ({"wired":True,"rules":False}, lambda r:r["score"])
        m = metrics.measure_generation(members,summary,children,*args)
        self.assertEqual("rules",m["top_by_training_rank"][0]["group"])
        counts = m["breeding_to_next"]
        self.assertEqual((1,1,2,0),(counts["wiring_mutations"],counts["neuron_cap_filtered_draws"],
                                    counts["node_growth_menu_entries_removed_by_neuron_cap_alone"],
                                    counts["rejected_selected_growth_attempts"]))
        self.assertIsNone(metrics.measure_generation(members,summary,None,*args)["breeding_to_next"])

    def test_wrapper_leaves_original_offspring_nominee_and_scores_identical(self):
        rng = Random(383)
        members = original.enroll({group:[random_genome(rng).to_dict() for _ in range(4)] for group in original.CONDITIONS})
        before = deepcopy(members)
        expected = original.generation(members,0)
        children,report,nominee = metrics.generation(members,0)
        arch = report.pop("architecture")
        self.assertEqual((children,report,nominee),expected)
        self.assertEqual(members,before)
        self.assertEqual((16,4,6),(arch["count"],len(arch["top_by_training_rank"]),arch["breeding_to_next"]["wiring_mutations"]))
