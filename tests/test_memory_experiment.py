from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from wisp.lab import LabController
from wisp.lineage import LineageArchive
from wisp.memory_experiment import exported_cohort, main
from wisp.transfer import PERSONAL, export_lab


class MemoryExperimentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def make_export(self):
        lab = LabController(17, per_condition=2)
        with closing(LineageArchive(self.root / "original.db")) as archive:
            archive.start(lab.checkpoint())
            for _ in lab.schedule:
                lab.command({"action": "step"})
            finished = lab.checkpoint()
            archive.record("main", 1, finished, lab.history[-1])
            child, tip = archive.fork(
                "main", 1, "Memory test", finished,
                lambda snapshot, key: LabController.restore(snapshot).fork(key).checkpoint(),
            )
            second = LabController.restore(tip)
            second.command({"action": "step"})
            package = export_lab(archive, child, second.checkpoint(),
                                 second.members[0].wisp.checkpoint(), self.root)
        return package, child, finished

    def test_export_defaults_to_finished_ancestor_and_checks_hashes(self):
        package, child, finished = self.make_export()
        snapshot, provenance = exported_cohort(package)
        self.assertEqual(snapshot, finished)
        self.assertEqual(provenance["branch_id"], child)
        self.assertEqual(provenance["origin_branch_id"], "main")
        self.assertEqual(provenance["latest_completed_at_export"], 1)
        other, _ = exported_cohort(package, branch="main", generation=1)
        self.assertEqual(snapshot, other)
        with self.assertRaisesRegex(ValueError, "completed generation"):
            exported_cohort(package, generation=2)

        damaged = self.root / "damaged.zip"
        with ZipFile(package) as original, ZipFile(damaged, "w") as replaced:
            for name in original.namelist():
                data = original.read(name)
                replaced.writestr(name, b" " * len(data) if name == PERSONAL else data)
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            exported_cohort(damaged)

    def test_founder_cli_records_the_actual_source_and_options(self):
        output = self.root / "results" / "memory.json"
        with patch("wisp.memory_experiment.run_next_plan", return_value={"example": 1}) as experiment, \
                redirect_stdout(io.StringIO()):
            main(["--founder-seed", "20", "--population", "32", "--seed", "23", "--repeats", "3",
                  "--member", "fixed:0", "--output", str(output)])
        checkpoint = experiment.call_args.args[0]
        self.assertEqual((checkpoint["seed"], checkpoint["generation"], checkpoint["step"]), (20, 1, 128))
        self.assertEqual(len(checkpoint["members"]), 32)
        self.assertEqual(experiment.call_args.kwargs, {"seed": 23, "repeats": 3, "member_ids": ["fixed:0"]})
        envelope = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(envelope["source"], {
            "kind": "founders", "founder_seed": 20, "population": 32, "generation": 1})
        self.assertEqual(envelope["report"], {"example": 1})
        self.assertIn("wisp/memory_experiment.py", envelope["evaluator_source"]["module_sha256_lf"])
        self.assertTrue(envelope["finished_at"])

    def test_failed_study_preserves_existing_output_and_source(self):
        package, _, _ = self.make_export()
        output = self.root / "report.json"
        output.write_text("previous complete report", encoding="utf-8")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            main(["--export", str(package), "--generation", "999", "--output", str(output)])
        self.assertEqual(output.read_text(), "previous complete report")
        original = package.read_bytes()
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            main(["--export", str(package), "--output", str(package)])
        self.assertEqual(package.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
