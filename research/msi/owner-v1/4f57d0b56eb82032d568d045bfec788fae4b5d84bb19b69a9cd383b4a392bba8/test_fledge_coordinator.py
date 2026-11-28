"""Exercise coordinator crash boundaries with isolated research records and workers."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from fledge_coordinator import Coordinator, Hold, Integrity, START, read, save


class ServiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "campaign"
        self.state = Path(tmp.name) / "service"
        self.cursor = self.root / "active_research.json"
        self.parent = dict(state="bounded_study_complete",active_path="continuations/old", objective="Old?",review_sha256=START)
        self.child = dict(active_path="continuations/new",objective="New?",prior_diagnostic_review_sha256=START)
        self.plan = {"child":self.child["active_path"]}
        self.assess = Mock(return_value="Scientific criterion failed. Test the mechanism.")
        self.prepare = Mock(return_value=self.plan)
        self.dispatch = Mock()
        self.emit = Mock()
        self.guard = Mock()
        self.verify = Mock(return_value={})
        save(self.cursor,self.parent)
        self.enable()

    def enable(self):
        save(self.state/"cutover.json",dict(host="MSI",hourly_paused_and_idle=True,anchor=START))

    def restart(self):
        return Coordinator(self.root,self.state,self.assess,self.prepare,self.dispatch,self.emit,self.guard,self.verify)

    def test_duplicate_completion_and_restart_after_worker_commit(self):
        def crash_after_commit(j):
            save(self.cursor,dict(self.child,state="bounded_study_complete",review_sha256="f"*64))
            raise KeyboardInterrupt()
        self.dispatch.side_effect = crash_after_commit
        with self.assertRaises(KeyboardInterrupt):
            self.restart().tick()
        self.prepare.return_value = {"child":"continuations/third"}
        self.dispatch.side_effect = None
        (self.state/"cutover.json").unlink()
        self.restart().tick()
        self.restart().tick()
        self.assertEqual(self.assess.call_count,2)
        self.assertEqual(self.prepare.call_count,2)
        self.dispatch.assert_called_once()
        self.assertEqual(read(self.state/"owner.json")["parent"],"f"*64)

    def test_interrupted_review_and_durable_decision(self):
        self.assess.side_effect = [KeyboardInterrupt(), "Second attempt completed"]
        with self.assertRaises(KeyboardInterrupt):
            self.restart().tick()
        self.assertFalse((self.state/START/"decision.json").exists())
        self.prepare.side_effect = [KeyboardInterrupt(),self.plan]
        with self.assertRaises(KeyboardInterrupt):
            self.restart().tick()
        self.restart().tick()
        self.assertEqual(self.assess.call_count,2)
        self.dispatch.assert_called_once()

    def test_live_cutover_prepares_next_successor_in_completion_tick(self):
        self.restart().tick()
        completed = dict(self.child,state="bounded_study_complete",review_sha256="f"*64)
        save(self.cursor,completed)
        self.prepare.return_value = {"child":"continuations/third"}
        self.restart().tick()
        self.assertEqual(self.assess.call_count,2)
        self.assertEqual(self.prepare.call_count,2)
        self.dispatch.assert_called_with({"parent":"f"*64,"child":"continuations/third"})

    def test_process_exit_mid_review_releases_service_lock_and_resumes(self):
        # Deliberately bypass Python cleanup, as a power loss would.
        script = """
import os,sys
from pathlib import Path
from fledge_coordinator import Coordinator,try_lock
root,state = map(Path,sys.argv[1:3])
def assess(packet,folder):
    if sys.argv[3] == 'interrupt':
        os._exit(17)
    return 'Fixture decision after restart'
service = Coordinator(root,state,assess,lambda *a:dict(child='continuations/new'),
    lambda j: (state/'dispatched').write_text(j['child']),lambda *a:None,
    guard=lambda *a:None,verify=lambda *a:{})
with (state/'service.lock').open('a+b') as lock:
    assert try_lock(lock)
    service.tick()
"""
        args = [sys.executable,"-B","-c",script,str(self.root),str(self.state)]
        first = subprocess.run([*args,"interrupt"],cwd=Path(__file__).parent)
        self.assertEqual(first.returncode,17)
        self.assertFalse((self.state/"dispatched").exists())
        subprocess.run([*args,"restart"],cwd=Path(__file__).parent,check=True)
        self.assertEqual((self.state/"dispatched").read_text(),"continuations/new")

    def test_resume_only_own_claimed_child(self):
        def start(j):
            save(self.cursor,dict(self.child,state="preregistered"))
        self.dispatch.side_effect = start
        self.restart().tick()
        self.restart().tick()
        self.assertEqual(self.dispatch.call_count,2)
        self.assess.assert_called_once()
        self.prepare.assert_called_once()
        save(self.cursor,dict(self.child,state="preregistered",prior_diagnostic_review_sha256="other"))
        with self.assertRaises(Integrity):
            self.restart().tick()

    def test_no_launch_before_cutover_or_on_operator_pause_during_review(self):
        (self.state/"cutover.json").unlink()
        self.restart().tick()
        self.dispatch.assert_not_called()
        self.enable()
        self.guard.side_effect = Hold("operator paused")
        with self.assertRaises(Hold):
            self.restart().tick()
        self.dispatch.assert_not_called()
        self.guard.side_effect = None
        self.restart().tick()
        self.dispatch.assert_called_once()

    def test_pause_arrives_during_review(self):
        def hold_while_interpreting(*args):
            self.guard.side_effect = Hold("operator paused")
            return "decision pending"
        self.assess.side_effect = hold_while_interpreting
        with self.assertRaises(Hold):
            self.restart().tick()
        self.prepare.assert_not_called()
        self.dispatch.assert_not_called()

    def test_integrity_failure_stops_before_codex_or_worker(self):
        self.verify.side_effect = Integrity("bank bytes changed")
        with self.assertRaises(Integrity):
            self.restart().tick()
        self.assess.assert_not_called()
        self.dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
