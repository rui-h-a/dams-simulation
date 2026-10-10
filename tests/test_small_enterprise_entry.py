"""Finite shell-entry controls using a recording tool, never the research model."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]
FAKE_UV = r'''
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
log = Path(os.environ["DAMS_ENTRY_TEST_CALLS"])
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\n")
if args == ["--version"]:
    print(os.environ.get("DAMS_ENTRY_TEST_UV_VERSION", "uv 0.9.26"))
elif args and args[0] == "sync":
    sys.exit(int(os.environ.get("DAMS_ENTRY_TEST_SYNC_EXIT", "0")))
elif args and args[0] == "run":
    sys.exit(int(os.environ.get("DAMS_ENTRY_TEST_RUN_EXIT", "0")))
else:
    raise SystemExit("unexpected fake uv invocation")
'''


class SmallEnterpriseEntry(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="entry controls ", dir=SOURCE.parent)
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.entry = self.root / "run.sh"
        shutil.copyfile(SOURCE / "run.sh", self.entry)
        self.bin = self.root / "recording tools"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        uv = self.bin / "uv"
        uv.write_text("#!" + sys.executable + "\n" + FAKE_UV)
        uv.chmod(0o755)
        # Explicitly remove coordinator settings; no credential values are read.
        self.env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("DAMS_") and k not in {"PYTHONPATH", "PYTHONHOME"}
        }
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                        DAMS_OFFLINE_DEPENDENCIES="1", DAMS_ENTRY_TEST_CALLS=str(self.log),
                        PYTHONDONTWRITEBYTECODE="1")

    def invoke(self, *args, extra_env=None):
        result = subprocess.run(
            ["/bin/bash", str(self.entry), *args], cwd=self.root,
            env=self.env | (extra_env or {}), capture_output=True, text=True,
            timeout=10,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    @staticmethod
    def execution_calls(calls):
        return [c for c in calls if c != ["--version"]]

    def small_args(self, n="120"):
        return ["--spec", "small-enterprise-5y", "--scale", n,
                "--output", "study results/retained world",
                "--runtime-limits", "admitted settings/runtime.json"]

    def assert_refused_before_tools(self, args, *, extra_env=None):
        result, calls = self.invoke(*args, extra_env=extra_env)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(calls, [], "refused study must not bootstrap tools or dispatch a model")

    def test_help_has_selector_and_performs_no_tool_calls(self):
        result, calls = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("small-enterprise-5y", result.stdout)
        self.assertIn("--prepare-only", result.stdout)
        self.assertEqual(calls, [])

    def test_all_three_populations_route_exact_helper(self):
        for n in ("30", "120", "300"):
            with self.subTest(n=n):
                if self.log.exists():
                    self.log.unlink()
                result, calls = self.invoke(*self.small_args(n))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.execution_calls(calls), [
                    ["sync", "--locked", "--extra", "analysis", "--python", "3.14.2", "--offline"],
                    ["run", "--no-sync", "python", "-m", "research_tools.small_enterprise",
                     "--population", n, "--output", "study results/retained world",
                     "--runtime-limits", "admitted settings/runtime.json"],
                ])

    def test_prepare_routes_existing_helper_flag(self):
        result, calls = self.invoke(*self.small_args(), "--prepare-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.execution_calls(calls)[-1], [
            "run", "--no-sync", "python", "-m", "research_tools.small_enterprise",
            "--population", "120", "--output", "study results/retained world",
            "--runtime-limits", "admitted settings/runtime.json", "--prepare-only",
        ])
        self.assertEqual(len(self.execution_calls(calls)), 2)

    def test_missing_explicit_population_is_refused(self):
        self.assert_refused_before_tools([
            "--spec", "small-enterprise-5y", "--output", "results",
            "--runtime-limits", "limits.json",
        ])

    def test_missing_output_is_refused(self):
        self.assert_refused_before_tools([
            "--spec", "small-enterprise-5y", "--scale", "30",
            "--runtime-limits", "limits.json",
        ])

    def test_empty_output_is_refused(self):
        self.assert_refused_before_tools([
            "--spec", "small-enterprise-5y", "--scale", "30", "--output", "",
            "--runtime-limits", "limits.json",
        ])

    def test_missing_runtime_limits_is_refused(self):
        self.assert_refused_before_tools([
            "--spec", "small-enterprise-5y", "--scale", "30", "--output", "results",
        ])

    def test_missing_argument_value_is_refused(self):
        self.assert_refused_before_tools(self.small_args()[:-1])

    def test_value_options_cannot_consume_prepare_or_other_option(self):
        for option in ("--spec", "--scale", "--output", "--runtime-limits"):
            for following in ("--prepare-only", "--output", "--help", "-h"):
                with self.subTest(option=option, following=following):
                    self.assert_refused_before_tools(self.small_args() + [option, following])

    def test_leading_dash_path_with_dot_prefix_is_literal_and_keeps_prepare(self):
        args = self.small_args()
        args[args.index("--output") + 1] = "./--prepare-only"
        args[args.index("--runtime-limits") + 1] = "./-limits.json"
        result, calls = self.invoke(*args, "--prepare-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.execution_calls(calls)[-1][-1], "--prepare-only")
        self.assertIn("./--prepare-only", self.execution_calls(calls)[-1])
        self.assertIn("./-limits.json", self.execution_calls(calls)[-1])

    def test_unsupported_population_is_refused(self):
        self.assert_refused_before_tools(self.small_args("1000"))

    def test_noninteger_population_is_refused(self):
        self.assert_refused_before_tools(self.small_args("120.0"))

    def test_unknown_selector_is_refused(self):
        self.assert_refused_before_tools(["--spec", "small-enterprise-10y"])

    def test_unknown_argument_is_refused(self):
        self.assert_refused_before_tools(self.small_args() + ["--confirmation-worlds", "1"])

    def test_private_cloud_config_is_not_silently_ignored(self):
        self.assert_refused_before_tools(
            self.small_args(), extra_env={"DAMS_CLOUD_PRIVATE_CONFIG": "private-config.json"},
        )

    def test_cloud_config_is_also_refused_during_prepare(self):
        self.assert_refused_before_tools(
            self.small_args() + ["--prepare-only"],
            extra_env={"DAMS_CLOUD_PRIVATE_CONFIG": "private-config.json"},
        )

    def test_online_sync_stays_locked(self):
        result, calls = self.invoke(*self.small_args(), extra_env={"DAMS_OFFLINE_DEPENDENCIES": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.execution_calls(calls)[0],
                         ["sync", "--locked", "--extra", "analysis", "--python", "3.14.2"])

    def test_wrong_uv_version_is_refused_without_download(self):
        result, calls = self.invoke(*self.small_args(), extra_env={"DAMS_ENTRY_TEST_UV_VERSION": "uv 0.9.25"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Offline image lacks the fixed uv binary", result.stderr)
        self.assertEqual(self.execution_calls(calls), [])

    def test_pinned_version_prefix_collision_is_refused_without_download(self):
        result, calls = self.invoke(*self.small_args(), extra_env={"DAMS_ENTRY_TEST_UV_VERSION": "uv 0.9.260"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.execution_calls(calls), [])

    def test_pinned_version_with_build_metadata_is_accepted(self):
        result, calls = self.invoke(*self.small_args(), extra_env={"DAMS_ENTRY_TEST_UV_VERSION": "uv 0.9.26 (build metadata)"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.execution_calls(calls)[-1][-6:], ["--population", "120", "--output", "study results/retained world", "--runtime-limits", "admitted settings/runtime.json"])

    def test_sync_failure_propagates_and_does_not_dispatch(self):
        result, calls = self.invoke(*self.small_args(), extra_env={"DAMS_ENTRY_TEST_SYNC_EXIT": "19"})
        self.assertEqual(result.returncode, 19)
        self.assertEqual(len(self.execution_calls(calls)), 1)
        self.assertEqual(self.execution_calls(calls)[0][0], "sync")

    def test_helper_exit_is_propagated(self):
        result, _ = self.invoke(*self.small_args(), extra_env={"DAMS_ENTRY_TEST_RUN_EXIT": "23"})
        self.assertEqual(result.returncode, 23)

    def test_paths_are_arguments_not_shell_programs(self):
        sentinel = self.root / "unwanted-created-file"
        literal = "$(touch unwanted-created-file); output folder"
        args = self.small_args()
        args[args.index("--output") + 1] = literal
        result, calls = self.invoke(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatch = self.execution_calls(calls)[-1]
        self.assertEqual(dispatch[dispatch.index("--output") + 1], literal)
        self.assertFalse(sentinel.exists())

    def test_existing_default_entry_is_preserved(self):
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.execution_calls(calls)[-1], [
            "run", "--no-sync", "python", "-m", "dams_sim", "pipeline",
            "--spec", "validation", "--scale", "120", "--output", "runs/validation-n120",
        ])


if __name__ == "__main__":
    unittest.main()
