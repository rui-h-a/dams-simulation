"""Finite routing/configuration controls; no scientific Model is constructed."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import run_controls as runner


class SupervisorRoutingTests(unittest.TestCase):
    def test_missing_pidfd_refuses_before_any_child_launch(self):
        with patch.object(runner.sys, 'platform', 'linux'), \
             patch.object(runner.os, 'pidfd_open', None, create=True), \
             patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('child launch forbidden')):
            with self.assertRaisesRegex(ValueError, 'before Popen'):
                runner.main(str(Path(sys.executable).resolve()))

    def test_relative_control_path_is_refused_before_any_child_launch(self):
        with patch.object(runner, 'supervisor_preflight', return_value={}), \
             patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('child launch forbidden')):
            with self.assertRaisesRegex(ValueError, 'absolute'):
                runner.main('python3.14')

    def test_missing_proc_directory_or_identity_refuses_before_child_launch(self):
        for directory in (False, True):
            with self.subTest(proc_directory=directory), patch.object(runner.sys, 'platform', 'linux'), \
                 patch.object(runner.os, 'pidfd_open', return_value=91, create=True) as open_fd, \
                 patch.object(runner.signal, 'pidfd_send_signal', create=True), \
                 patch.object(Path, 'is_dir', return_value=directory), \
                 patch.object(runner, 'read_process', return_value=None), \
                 patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('child launch forbidden')):
                with self.assertRaisesRegex(ValueError, '/proc identity before Popen'):
                    runner.main(str(Path(sys.executable).resolve()))
                open_fd.assert_not_called()

    def test_every_control_command_and_capability_use_explicit_interpreter(self):
        executable = str(Path(sys.executable).resolve())
        self.assertEqual(runner.control_executable(executable), executable)
        for name, arguments, _ in runner.STAGES:
            with self.subTest(stage=name):
                self.assertEqual(runner.control_command(executable, arguments), [executable, *arguments])
        completed = type('Completed', (), {'returncode': 0, 'stdout':
            '{"python_version":"3.14.2","schema2_actual_control":"PASS"}', 'stderr': ''})()
        with patch.object(runner.subprocess, 'run', return_value=completed) as launch:
            self.assertEqual(runner.managed_capability(executable)['python_version'], '3.14.2')
        self.assertEqual(launch.call_args.args[0][0], executable)
        self.assertIn('--required-managed', launch.call_args.args[0])
        with patch.object(runner.sys, 'platform', 'linux'), \
             patch.object(runner.os, 'pidfd_open', return_value=91, create=True) as open_fd, \
             patch.object(runner.signal, 'pidfd_send_signal', create=True) as send, \
             patch.object(Path, 'is_dir', return_value=True), \
             patch.object(runner, 'read_process', return_value=('supervisor', 1, 1024, ())), \
             patch.object(runner.os, 'close') as close:
            self.assertTrue(runner.supervisor_preflight()['actual_pidfd_self_open'])
            open_fd.assert_called_once_with(runner.os.getpid())
            close.assert_called_once_with(91); send.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
