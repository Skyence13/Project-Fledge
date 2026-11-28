from pathlib import Path
from subprocess import CompletedProcess, CalledProcessError
import json
import tempfile
import unittest

import install_fledge_coordinator as installer


class InstallFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.pythonw = self.work / "pythonw.exe"
        self.pythonw.touch()
        (self.work / "fledge_coordinator.py").touch()
        self.identity = r"MSI\morga"
        self.sid = "S-1-5-21-1000"
        self.xml = None
        self.calls = []
        self.run_fails = False
        self.binding = self.work / "fledge-coordinator-state" / "service-account.json"

    def scheduler(self, args):
        self.calls.append(args[0])
        if args[0] == "/Create":
            self.assertNotIn("/F", args)
            self.xml = Path(args[-1]).read_bytes()
        code = 1 if args[0] == "/Query" and self.xml is None else 0
        if args[0] == "/Run" and self.run_fails:
            code = 1
        return CompletedProcess(args, code, stdout=self.xml or b"", stderr=b"")

    def run_install(self):
        installer.install(self.work, self.pythonw, self.identity, self.sid, "MSI", self.scheduler)

    def test_task_settings_registration_readback_and_idempotent_reinstall(self):
        self.run_install()
        self.assertEqual(self.calls, ["/Query", "/Create", "/Query", "/Run"])
        self.assertEqual(json.loads(self.binding.read_bytes())["account"], self.identity.lower())
        expected = self.xml.decode("utf-16")
        for token in ("InteractiveToken", "LeastPrivilege", "PT0S", "PT1M", "IgnoreNew", "--service",
                      str(self.pythonw), str(self.work), "RestartOnFailure", "LogonTrigger"):
            self.assertIn(token, expected)
        # Simulate a redirected export and the scheduler normalizing the owner to its SID.
        self.xml = expected.replace(self.identity, self.sid).encode()
        self.calls.clear()
        self.run_install()
        self.assertEqual(self.calls, ["/Query", "/Run"])

    def test_unrelated_existing_task_preserved(self):
        self.xml = installer.task_xml(self.work, self.pythonw, self.identity).decode("utf-16").replace(
            "--service", "--once").encode("utf-16")
        with self.assertRaises(ValueError):
            self.run_install()
        self.assertEqual(self.calls, ["/Query"])
        self.assertFalse(self.binding.exists())

    def test_rejected_start_preserves_handover(self):
        self.run_fails = True
        with self.assertRaises(CalledProcessError):
            self.run_install()
        self.assertFalse(self.binding.exists())

    def test_sandbox_identity_never_registers(self):
        self.identity = r"MSI\CodexSandboxOffline"
        with self.assertRaises(ValueError):
            self.run_install()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.binding.exists())

    def test_idle_upgrade_preserves_genuine_stop_and_other_owners(self):
        from fledge_coordinator import save
        state=self.binding.parent
        anchor="e"*64
        save(state/"standing-authority.json",dict(anchor=anchor))
        save(state/"service-session.json",dict(account=installer.ACCOUNT,pid=7))
        save(state/"owner.json",dict(parent=anchor,child=None))
        (state/"service.lock").touch()
        cursor=self.work/"fledge/runs/personal.json.campaign/active_research.json"
        save(cursor,dict(review_sha256=anchor,stop_after_review=True))
        stop=state/"stopped.json"
        save(stop,dict(error="fixture operator stop"))
        with self.assertRaises(ValueError):
            installer.release_legacy_review_owner(self.work)
        self.assertEqual(json.loads(stop.read_bytes())["error"],"fixture operator stop")
        stop.unlink()
        self.assertTrue(installer.release_legacy_review_owner(self.work))
        self.assertFalse(stop.exists())
        save(state/"owner.json",dict(parent=anchor,child="continuations/different"))
        with self.assertRaises(ValueError):
            installer.release_legacy_review_owner(self.work)
        self.assertFalse(stop.exists())


if __name__ == "__main__":
    unittest.main()
