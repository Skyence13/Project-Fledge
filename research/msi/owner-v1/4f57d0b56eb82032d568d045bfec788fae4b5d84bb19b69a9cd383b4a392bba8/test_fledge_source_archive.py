from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fledge_source_archive as source


class SourceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root/"fledge-source-history"
        self.workspace = self.root/"worker"
        self.workspace.mkdir()
        original = Path(source.__file__).resolve().parent
        source.initialize(self.repo,original/"fledge-one-blank-pr13")
        self.package = "lineage-v1"
        self.names = source.PACKAGES[self.package]
        for name in self.names:
            (self.workspace/name).write_bytes(b"# synthetic fixture\r\n")
        self.pins = {n:source.sha((self.workspace/n).read_bytes()) for n in self.names}
        self.tree = source.git(self.repo,"rev-parse",source.BASE+"^{tree}").decode().strip()

    def archive(self, **kw):
        return source.archive_source(self.workspace,self.repo,self.package,self.pins,source.BASE,self.tree,**kw)

    def test_exact_bytes_idempotent_reconstruction_and_offline_old_receipt(self):
        record = self.archive()
        old = self.archive(reconstructed=True)
        self.assertNotEqual(record["archive_commit"],old["archive_commit"])
        self.assertEqual(self.archive(),record)
        self.assertIsNone(old["execution_commit"])
        self.assertEqual(record["archive_commit"],record["execution_commit"])
        (self.workspace/self.names[0]).write_bytes(b"# revised safely\n")
        source.verify_receipt(self.repo,record)
        with self.assertRaises(ValueError):
            source.verify_source(self.workspace,self.repo,record)
        with self.assertRaises(ValueError):
            source.verify_receipt(self.repo,dict(old,execution_commit=old["archive_commit"]))
        source.verify_publish_tree(self.repo,old["archive_commit"])

    def test_unlisted_data_or_embedded_credentials_never_enter_archive(self):
        self.pins["../raw.json"] = "0"*64
        with self.assertRaises(ValueError):
            self.archive()
        del self.pins["../raw.json"]
        path = self.workspace/self.names[0]
        path.write_bytes(b"# "+b"gh"+b"p_"+b"x"*40)
        self.pins[self.names[0]] = source.sha(path.read_bytes())
        with self.assertRaisesRegex(ValueError,"privacy"):
            self.archive()
        self.assertEqual(source.BASE,source.git(self.repo,"rev-parse",source.REF).decode().strip())

    def test_queue_persists_offline_and_clears_only_acknowledged_sources(self):
        record = self.archive()
        state = self.root/"state"
        source.on_completion(self.root,state,record,start_publisher=False)
        directory = state/"source-publication"
        pending = directory/(record["archive_commit"]+".pending")
        self.assertTrue(pending.exists())
        with patch.object(source,"publish",side_effect=OSError("unreachable")):
            source.drain(self.repo,state)
        self.assertTrue(pending.exists())
        source.acknowledge(self.repo,directory,record["archive_commit"],"b"*40)
        self.assertFalse(pending.exists())
        source.on_completion(self.root,state,start_publisher=False)
        self.assertFalse(pending.exists())

    def test_generated_checkout_rebuilds_exact_executed_commit_and_rejects_unlisted_changes(self):
        reference = Path(source.__file__).resolve().parent/"fledge-state-swap"
        code = self.root/"candidate"
        source.git(self.root, "clone", "--no-local", "--no-checkout",str(reference),str(code))
        source.git(code,"checkout","--detach",source.STATE_SWAP)
        for path in ("wisp/generated_study.py","tests/test_generated_study.py"):
            (code/path).write_bytes(b"# a disposable program\n")
        source.git(code,"add","--","wisp/generated_study.py","tests/test_generated_study.py")
        source.git(code,"-c","user.name=Project Fledge MSI","-c","user.email=fledge@localhost","commit","-m","Synthetic calibration")
        revision = source.git(code,"rev-parse","HEAD").decode().strip()
        tree = source.git(code,"rev-parse","HEAD^{tree}").decode().strip()
        r = source.archive_generated(code,self.repo,revision,tree,recovered=True)
        self.assertEqual(r["execution_commit"],revision)
        self.assertNotEqual(r["archive_commit"],revision)
        self.assertEqual(r["provenance"],"recovered_tested_git_commit")
        source.verify_receipt(self.repo,r)
        source.verify_publish_tree(self.repo,r["archive_commit"])
        (code/"unwanted.json").write_text("{}")
        with self.assertRaisesRegex(ValueError,"registered checkout"):
            source.archive_generated(code,self.repo,revision,tree)
