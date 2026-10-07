import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dams_sim.cli import main
from dams_sim.storage import digest


class CliTests(unittest.TestCase):
    def test_import_is_side_effect_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = str(Path(__file__).resolve().parents[1])
            code = f"import sys; sys.path.insert(0, {package!r}); import dams_sim.model, dams_sim.cli"
            subprocess.run([sys.executable, "-c", code], cwd=tmp, check=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_smoke_unique_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["smoke", "--output", tmp]), 0)
            self.assertEqual(main(["smoke", "--output", tmp]), 0)
            paths = list(Path(tmp).iterdir())
            self.assertEqual(len(paths), 2)
            for p in paths:
                m = json.loads((p/"manifest.json").read_text())
                self.assertEqual(m["status"], "complete")
                for file, expected in m["output_sha256"].items():
                    self.assertEqual(digest((p/file).read_bytes()), expected)

    def test_resume_exact_result(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "--n", "24", "--days", "12", "--checkpoint-day", "6", "--output", tmp]), 0)
            partial = next(Path(tmp).iterdir())
            self.assertEqual(main(["resume", "--checkpoint", str(partial/"checkpoint.json"), "--output", tmp]), 0)
            self.assertEqual(main(["run", "--n", "24", "--days", "12", "--output", tmp]), 0)
            runs = [p for p in Path(tmp).iterdir() if json.loads((p/"manifest.json").read_text())["status"] == "complete"]
            self.assertEqual((runs[0]/"final_state.json").read_bytes(), (runs[1]/"final_state.json").read_bytes())
            resumed=json.loads((runs[0]/"manifest.json").read_text())["restart_origin"]
            self.assertEqual(resumed["parent_run_id"],partial.name)
            self.assertEqual(resumed["checkpoint_sha256"],digest((partial/"checkpoint.json").read_bytes()))
            self.assertEqual(resumed["parent_manifest_sha256"],digest((partial/"manifest.json").read_bytes()))

    def test_resume_rejects_changed_checkpoint_bytes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", "--n", "24", "--days", "12", "--checkpoint-day", "6", "--output", tmp]), 0)
            partial = next(Path(tmp).iterdir())
            value = json.loads((partial/"checkpoint.json").read_text())
            value["state"]["agents"][0]["confirmed"] = 1e12
            (partial/"checkpoint.json").write_text(json.dumps(value))
            self.assertEqual(main(["resume", "--checkpoint", str(partial/"checkpoint.json"), "--output", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
