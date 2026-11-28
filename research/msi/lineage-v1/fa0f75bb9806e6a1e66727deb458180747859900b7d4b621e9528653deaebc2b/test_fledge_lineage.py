"""Disposable fixtures for contrast attribution, paired controls and inherited ancestry."""
from pathlib import Path
from dataclasses import replace
import json
from random import Random
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent/"fledge-one-blank-pr13"))
import fledge_lineage_science as s
from wisp.core import Connection, Genome, Neuron, Wisp, random_genome
from wisp.experiment import identifier


def member(genome):
    return {"genome":genome.to_dict(),"fingerprint":identifier(genome)}


def plant(bias=0.,sensitivity=0.):
    weights=((-1,0,sensitivity-bias),(-2,0,-sensitivity-bias),(-1,1,-sensitivity+bias),(-2,1,sensitivity+bias))
    return Genome(tuple(Neuron(i,.4,1.5,0.) for i in (0,1)),tuple(Connection(*e) for e in weights),0.)


class LineageFixture(unittest.TestCase):
    def test_sealed_genome_and_independent_founder(self):
        import fledge_lineage_worker as w
        from fledge_coordinator import Integrity, digest, save
        with TemporaryDirectory() as temp:
            root=Path(temp)
            selected=member(random_genome(Random(982)))
            founder=member(random_genome(Random(984)))
            save(root/"sealed.json",{"checkpoint":{"winner":selected}})
            lineage={"checkpoint":"sealed.json","checkpoint_sha256":digest((root/"sealed.json").read_bytes()),
                     "member_keys":["checkpoint","winner"],"independent_seed":984,
                     "finalist":selected["fingerprint"],"frozen_ancestor":founder["fingerprint"]}
            self.assertEqual(w.load_family(root,lineage),(selected,founder))
            for field,wrong in (("finalist",founder["fingerprint"]),("frozen_ancestor",selected["fingerprint"]),("checkpoint_sha256","0"*64)):
                with self.assertRaises(Integrity):
                    w.load_family(root,dict(lineage,**{field:wrong}))

    def test_matched_interventions_require_real_changes_and_frozen_controls(self):
        from fledge_generated import FAMILY_EVALUATE,validate_family_design
        from fledge_coordinator import Integrity
        ns={}
        exec(FAMILY_EVALUATE,ns)
        base=member(random_genome(Random(92)))
        founder=member(random_genome(Random(91)))
        owner={"action":"compare_learning_rule","successor_question":"Does removing learning improve transfer?"}
        d={"owner_action":owner["action"],"owner_question":owner["successor_question"],"arms":["finalist","frozen_ancestor","disabled"],
           "matched_interventions":{"disabled":{"control":"finalist"}},"contrasts":{"test":{"arm":"disabled","control":"finalist"}}}
        validate_family_design(d,owner)
        with self.assertRaises(Integrity):
            validate_family_design(dict(d,matched_interventions={}),owner)
        measured=[]
        def collect(subjects,seed,earlier_seed,split):
            measured.append(subjects)
            return {"post_practice":{"passed":True}}
        study=SimpleNamespace(intervene=lambda g,arm:replace(g,learning_rate=0.),evaluate_family=collect)
        body=dict(base,frozen_ancestor=founder)
        report=ns["evaluate_family"](study,body,d,392,393,"validation")
        self.assertTrue(report["subject_fingerprints"]["disabled"]!=base["fingerprint"])
        self.assertEqual(measured[0]["frozen_ancestor"],founder)
        study.intervene=lambda g,arm:g
        with self.assertRaisesRegex(ValueError,"unchanged"):
            ns["evaluate_family"](study,body,d,392,393,"validation")
        d["owner_action"]="compare_architecture"
        study.intervene=lambda g,arm:replace(g,edges=g.edges[1:])
        arch=ns["evaluate_family"](study,body,d,392,393,"confirmation")
        self.assertNotEqual(report["subject_fingerprints"]["disabled"],arch["subject_fingerprints"]["disabled"])

    def test_bias_sensitivity_and_sham(self):
        neutral=Wisp(plant()).checkpoint()
        probabilities=lambda state:{k:s.response(state,k) for k in s.LEVELS}
        baseline=s.components(probabilities(neutral))
        for b,g,expected in ((.8,0.,"rightward_bias_replicated"),(0.,1.,"rightward_sensitivity_replicated"),
                             (.5,1.,"both_components_replicated"),(0.,0.,"response_components_unresolved")):
            active=Wisp(plant(b,g))
            snapshot=active.checkpoint()
            delta={key:s.contrast([value]*8,[baseline[key]]*8,212) for key,value in s.components(probabilities(snapshot)).items()}
            self.assertEqual(expected,s.mechanism({split:{"mechanism_contrasts":delta} for split in s.LIFETIMES}))
            self.assertEqual(active.checkpoint(),snapshot)
        self.assertEqual(probabilities(neutral),dict.fromkeys(s.LEVELS,.5))

    def test_actual_banks_on_zero_change_and_genuine_descendants(self):
        base=member(random_genome(Random(192)))
        founder=member(random_genome(Random(193)))
        seeds={k:287390+i for i,k in enumerate(("training","validation","confirmation","validation_earlier","confirmation_earlier"))}
        with patch.object(s,"LIFETIMES",{"validation":3,"confirmation":4}),patch.object(s,"TRAIN",1),patch.object(s,"GENS",1),patch.object(s,"POP",4):
            sham=s.evaluate({"finalist":base,"frozen_ancestor":base},"validation",seeds,s.DIAGNOSTIC)
            self.assertTrue(all(e["difference"]==0 and e["ci95"]==[0,0] for e in sham["mechanism_contrasts"].values()))
            self.assertEqual(sham["fresh_skills"],sham["ancestor_fresh_skills"])
            m=dict(base,base_fingerprint=base["fingerprint"],parent_fingerprint=None)
            initial=[m]*4
            arms={}
            for mode in ("baseline","rightward_bias_replicated"):
                first=s.generation(initial,0,mode,seeds["training"])
                final=s.generation(first["next_members"],1,mode,seeds["training"])
                arms[mode]=final["selected"]
                self.assertEqual(arms[mode]["base_fingerprint"],base["fingerprint"])
                self.assertNotEqual(arms[mode]["fingerprint"],base["fingerprint"])
                self.assertLessEqual(final["population"]["mean_neurons"],12)
            bank=s.evaluate({"finalist":arms["rightward_bias_replicated"],"matched_selection":arms["baseline"],
                             "parent_finalist":base,"frozen_ancestor":founder},"confirmation",seeds,s.BREEDING)
            self.assertEqual(bank["lifetimes"],4)
            self.assertEqual(bank["fingerprints"]["parent_finalist"],base["fingerprint"])
            self.assertEqual(set(bank["matched_contrasts"]),{"frozen_ancestor","parent_finalist","matched_selection"})
            self.assertEqual(bank["passed"],all(bank["gates"].values()))


def selection_fixture(branch):
    base=member(random_genome(Random(6349)))
    subject=dict(base,base_fingerprint=base["fingerprint"],parent_fingerprint=None)
    with patch.object(s,"POP",4),patch.object(s,"TRAIN",1),patch.object(s,"GENS",1):
        for treatment in ("baseline",branch):
            start=s.generation([subject]*4,0,treatment,2109)
            sealed=s.generation(start["next_members"],1,treatment,2109)
            assert sealed["selected"]["fingerprint"]!=subject["fingerprint"]
            assert sealed["selected"]["base_fingerprint"]==subject["fingerprint"]
            assert sealed["population"]["architecture"]["maximum_neurons"]<=12


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--selection-fixture":
        selection_fixture(json.loads(sys.argv[2]))
    else:
        unittest.main()
