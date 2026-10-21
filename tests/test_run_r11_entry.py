"""Exercise only the R11 shell branch with fake child tools; no model/tool install."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class R11EntryTest(unittest.TestCase):
    def branch(self, *, inference_exit=0):
        base = Path(os.environ.get('TMPDIR', '/private/tmp')).resolve()
        if shutil.disk_usage(base).free < 2*1024**3:
            self.skipTest('less than 2 GiB free; no entry fixture allocated')
        with tempfile.TemporaryDirectory(prefix='r11-entry-', dir=base) as tmp:
            root = Path(tmp)
            output = root/'study'; output.mkdir()
            log = root/'calls.jsonl'
            stub = root/'fake-uv'
            stub.write_text('#!'+sys.executable+'\n'+
                'import json, os, sys\n'+
                'with open(os.environ["ENTRY_LOG"], "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'+
                'if "research_tools/r11_inference.py" in sys.argv:\n'+
                ' print(json.dumps({"fixture": "inference"}))\n'+
                ' sys.exit(int(os.environ["ENTRY_INFERENCE_EXIT"]))\n')
            stub.chmod(0o700)
            text = (Path(__file__).resolve().parents[1]/'run.sh').read_text()
            branch = text.split('if [[ "$task_spec" == enterprise-r11 ]]; then', 1)[1].split('\ntask_args=', 1)[0]
            script = root/'branch.sh'
            script.write_text('set -euo pipefail\n'+
                'task_spec=enterprise-r11; task_scale=30; task_context=startup; task_growth=none\n'+
                'task_pre=0; task_post=1; task_worlds=0,1; task_arms=control-hierarchy,dams-witness\n'+
                'task_variant=baseline; task_jobs=\n'+
                'task_uv=$ENTRY_UV; task_output=$ENTRY_OUTPUT\n'+
                'if [[ "$task_spec" == enterprise-r11 ]]; then'+branch)
            env = dict(os.environ, ENTRY_LOG=str(log), ENTRY_UV=str(stub), ENTRY_OUTPUT=str(output),
                       ENTRY_INFERENCE_EXIT=str(inference_exit))
            result = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True, timeout=10)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            scripts = [call[3] for call in calls]
            self.assertFalse((output/'paired-outcomes.json').exists())
            self.assertNotIn('Paired outcomes:', result.stdout)
            self.assertLess(sum(p.stat().st_size for p in root.rglob('*') if p.is_file()), 100*1024**2)
            return result, scripts

    def test_public_analysis_uses_only_raw_admission_inference_then_figure(self):
        result, scripts = self.branch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(scripts, ['research_tools/r11_study.py', 'research_tools/r11_inference.py',
                                   'research_tools/r11_figure.py'])

    def test_inference_failure_prevents_figure_and_success_hint(self):
        result, scripts = self.branch(inference_exit=4)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(scripts, ['research_tools/r11_study.py', 'research_tools/r11_inference.py'])
        self.assertNotIn('Inference:', result.stdout)


if __name__ == '__main__':
    unittest.main()
