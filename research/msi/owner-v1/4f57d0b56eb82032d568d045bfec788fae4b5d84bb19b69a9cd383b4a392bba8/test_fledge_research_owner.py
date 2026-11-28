"""Disposable tests for Morgan's standing review boundary and completion receipts."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from fledge_coordinator import Integrity,digest,read,save
import fledge_research_owner as owner


class StandingReview(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work=Path(tmp.name)
        self.root=work/"campaign"
        self.state=work/"state"
        self.handoff=work/"fledge-monitor/RESEARCH_HANDOFF.md"
        self.handoff.parent.mkdir()
        self.handoff.write_text("# Handoff\n\nArchived notes.\n",encoding="utf8")
        review="continuations/first/review.json"
        save(self.root/review,{"result":"fixture"})
        self.key=digest((self.root/review).read_bytes())
        t=datetime.now(timezone.utc)
        self.parent=dict(review_sha256=self.key,review_path=review,active_path="continuations/first",completed_at=t.isoformat())
        archived="continuations/first/review-at-completion.json"
        save(self.root/archived,self.parent)
        self.child=dict(previous_review=archived,previous_review_sha256=digest((self.root/archived).read_bytes()),
                        parent_path=self.parent["active_path"],prior_diagnostic_review_sha256=self.key,
                        review_sha256="c"*64,active_path="continuations/second",created_at=(t+timedelta(seconds=11)).isoformat())
        save(self.root/self.child["active_path"]/"preregistration.json",self.child)
        self.record=dict(review_sha256=self.key,recorded_at=(t+timedelta(seconds=9)).isoformat(),decision="Fixture result",
                         owner_review=dict(action="diagnose",decision="Mixed sides",notice="One side gained.",capability="direction",
                            limiter="bias",history_decision="Change the cue",architecture="Unmeasured",lineage_count=1,
                            successor_question="Does it turn right at equal input?",milestone=True,
                            outcome_actions=[dict(outcome="Bias",action="Balance training"),dict(outcome="Sensitivity",action="Test transfer")]))
        save(self.state/self.key/"decision.json",self.record)

    def test_authority_is_anchored_and_ancestry_is_verified(self):
        with patch.object(owner,"ANCHOR",self.key):
            self.assertFalse(owner.authorized(self.root,self.state,self.parent))
            save(self.state/"standing-authority.json",dict(version=1,host="MSI",owner=r"msi\morga",
                 anchor=self.key,scope="one_bounded_study_at_a_time"))
            self.assertTrue(owner.authorized(self.root,self.state,self.child))
            with self.assertRaises(Integrity):
                owner.authorized(self.root,self.state,dict(self.child,parent_path="continuations/wrong"))
            save(self.root/self.parent["review_path"],{"changed":True})
            with self.assertRaises(Integrity):
                owner.authorized(self.root,self.state,self.parent)

    def test_review_has_different_actions_and_idempotent_notices(self):
        value=self.record["owner_review"]
        owner.check_decision(value)
        with self.assertRaises(Integrity):
            owner.check_decision(dict(value,outcome_actions=[value["outcome_actions"][0]]*2))
        owner.publish_decision(self.state,self.parent,self.record)
        (self.state/self.key/"notice.json").unlink()
        owner.publish_decision(self.state,self.parent,self.record)
        self.assertEqual(self.handoff.read_text(encoding="utf8").count("<!-- owner-review"),1)
        self.assertIn("Archived notes",self.handoff.read_text(encoding="utf8"))
        self.assertEqual(read(self.state/self.key/"notice.json"),read(self.state/"latest-decision.json"))

    def test_handoff_uses_registered_times_across_restart(self):
        receipt=owner.record_handoff(self.root,self.state,self.child)
        self.assertEqual(receipt["elapsed_seconds"],11)
        self.assertEqual(owner.record_handoff(self.root,self.state,self.child),receipt)
        save(self.state/self.key/"successor-start.json",dict(receipt,elapsed_seconds=0))
        with self.assertRaises(Integrity):
            owner.record_handoff(self.root,self.state,self.child)

    def test_standing_owner_advances_from_legacy_review_once_across_crash(self):
        from fledge_coordinator import Coordinator,START
        active=dict(self.parent,study_kind="evolution",stop_after_review=True,objective="Fixture objective")
        save(self.root/"active_research.json",active)
        save(self.state/"owner.json",dict(parent=self.key,child=None))
        save(self.state/"cutover.json",dict(anchor=START,host="MSI",hourly_paused_and_idle=True))
        legacy=dict(self.record)
        del legacy["owner_review"]
        save(self.state/self.key/"decision.json",legacy)
        def start(journal):
            self.assertEqual(journal["child"],self.child["active_path"])
        # In a real crash, before claim the child directory has not been created.
        child=self.root/self.child["active_path"]
        (child/"preregistration.json").unlink()
        child.rmdir()
        dispatch=Mock(side_effect=[KeyboardInterrupt(),None])
        prepare=Mock(return_value={"child":self.child["active_path"]})
        service=lambda:Coordinator(self.root,self.state,Mock(),prepare,dispatch,Mock(),guard=Mock(),
                                   verify=Mock(return_value={}))
        with patch.object(owner,"authorized",return_value=True),patch.object(owner,"interpret",return_value=self.record) as review, \
             patch.object(owner,"preserve_milestone"),patch("fledge_source_archive.on_study_completion",return_value=
                 dict(execution_commit=None,archive_commit="a"*40,provenance="fixture")):
            with self.assertRaises(KeyboardInterrupt):
                service().tick()
            service().tick()
            review.assert_called_once()
        prepare.assert_called_once()
        start(read(self.state/"owner.json"))
        self.assertEqual(read(self.state/self.key/"decision.json"),legacy)
        self.assertEqual(dispatch.call_args_list[0],dispatch.call_args_list[1])

    def test_architecture_packet_uses_only_sealed_aggregate_measurements(self):
        from fledge_coordinator import training_architecture
        child=self.root/"fixture-training"
        save(child/"preregistration.json",{"budget":0})
        row=dict(neurons=9,connections=15,sensor_to_motor_nodes=4,genome="private")
        arch=dict(count=8,neuron_cap=12,at_neuron_cap=0,mean_sensor_to_motor_nodes=4,breeding_to_next=None,
                  cohort=["private"],top_by_training_rank=[row])
        path=child/"generation-00.json"
        save(path,dict(checkpoint=dict(previous=digest((child/"preregistration.json").read_bytes()),
             generation=0,arms={"guided":{"population":{"architecture":arch}}})))
        bank=dict(selection_checkpoint_sha256=digest(path.read_bytes()))
        result=training_architecture(child,0,{"validation":bank,"confirmation":bank})
        self.assertEqual(result[0]["arms"]["guided"]["top_by_training_rank"][0]["sensor_to_motor_nodes"],4)
        self.assertNotIn("private",str(result))
        with self.assertRaises(Integrity):
            training_architecture(child,0,{"validation":bank,"confirmation":{"selection_checkpoint_sha256":"0"*64}})
