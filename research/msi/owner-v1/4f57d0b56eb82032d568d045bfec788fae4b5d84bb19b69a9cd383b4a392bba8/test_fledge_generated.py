"""Exercise disposable source staging, pinning and recovery with a fixed test author."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fledge_generated as g
from fledge_coordinator import Integrity, Retry, read, save


class GenericPreparationTests(unittest.TestCase):
    def test_sandbox_interruption_reuses_candidate_and_changed_sources_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, folder = base/"campaign", base/("a"*64)
            folder.mkdir()
            (root/"continuations"/"previous").mkdir(parents=True)
            packet = {"review_sha256":folder.name}
            save(root/"active_research.json",{**packet,"active_path":"continuations/previous","result":"null"})
            decision = {"decision":"test recipe"}
            def author(directory, *args):
                design = dict(child="fixture-next",protocol="fixture-v1",objective="Fixture",senses="Synthetic inputs",
                              bounds="Two paired trials",arms=["intact","control"],conditions=["primary"],
                              lifetimes={"validation":2,"confirmation":2},probes_per_lifetime=4,seeds={"sensory":1,"earlier_skills":2},
                              contrasts={"advantage":{"arm":"intact","control":"control","condition":"primary","minimum":.05}})
                save(directory/"design.json",design)
                (directory/"proposal.md").write_text("Disposable fixture",encoding="utf-8")
                (directory/"test_study.py").write_text("from unittest import TestCase\nclass Example(TestCase):\n def test_probe(self): self.assertTrue(True)\n",encoding="utf-8")
                # Fixed in-test data exercise the transport/state machine, independent of scientific integration tests.
                source = """
PROTOCOL = 'fixture-v1'
def evaluate(champion, seed, earlier_seed, split):
    cell = {'correct':4,'total':8,'accuracy':.5}
    return dict(split=split,lifetimes=2,size=dict(neurons=2,connections=2),
        held_out={k:{'primary':cell} for k in ['intact','control']},
        visible_contrasts={'advantage':dict(difference=0.,ci95=[0.,0.])},gates={'advantage':False},
        instrumentation_passed=True,fresh_skills=dict(passed=True,split=split,gates=dict.fromkeys(
        ('mixed','each_fixed','pooled_fixed_minus_mixed','each_fixed_one_shot','immediate','reversal'),True)),
        post_practice=dict(passed=True,body_lifetime_pairs=2,difference={'recall':0,'reversal':0},rates={'intact':{},'nonplastic':{}}))
"""
                (directory/"study.py").write_text(source,encoding="utf-8")
            with (patch.object(g,"ROOT",root), patch.object(g,"codex",side_effect=author) as invocation,
                  patch.object(g,"run_prepared") as preflight):
                with patch.object(g,"readonly_command",side_effect=RuntimeError("windows sandbox failed: fixture")):
                    with self.assertRaisesRegex(Retry,"sandbox unavailable"):
                        g.prepare_generated(packet,decision,folder)
                self.assertTrue((folder/"candidate.json").is_file())
                self.assertFalse((folder/"prepared.json").exists())
                # The stand-in executes only the fixed in-test program above.
                with patch.object(g,"readonly_command",side_effect=lambda directory,cmd: cmd):
                    prepared = g.prepare_generated(packet,decision,folder)
                self.assertEqual(prepared,read(folder/"prepared.json"))
                self.assertEqual(invocation.call_count,1)
                preflight.assert_called_once()
                study = folder/prepared["directory"]/"author"/"study.py"
                study.write_text("changed",encoding="utf-8")
                with self.assertRaisesRegex(Integrity,"changed"):
                    g.checked_files(prepared,folder)
