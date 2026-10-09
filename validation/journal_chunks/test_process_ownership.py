"""Mocked proc/PIDfd counterexamples; never signals or launches a process."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import run_controls as runner


def observed(birth, parent, children=(), rss=1024):
    return (birth, parent, rss, children)


class ProcessOwnershipTests(unittest.TestCase):
    def cleanup_observation(self, observer, seen):
        bindings = {99: (10, 'original-root')}; signals = []; opened = []
        process = Mock(pid=10); process.poll.return_value = 0

        def open_fd(pid):
            opened.append(pid)
            current = observer(pid)
            if current is None:
                raise ProcessLookupError()
            bindings[pid + 100] = (pid, current[0])
            return pid + 100

        def send(fd, sig):
            signals.append((bindings[fd], sig))

        with patch.object(runner, 'read_process', side_effect=observer), \
             patch.object(runner.os, 'pidfd_open', side_effect=open_fd, create=True), \
             patch.object(runner.signal, 'pidfd_send_signal', side_effect=send, create=True), \
             patch.object(runner.os, 'close'), patch.object(runner.time, 'sleep'), \
             patch.object(runner.os, 'kill', side_effect=AssertionError('numeric PID signal forbidden')):
            runner.stop_owned(process, 'original-root', 99, seen)
        return opened, signals

    def test_reused_root_never_enrolls_or_signals_new_birth(self):
        values = {10: observed('reused-root', 1, (20,)), 20: observed('foreign-child', 10)}
        seen = {10: ('original-root', 1024)}
        with patch.object(runner, 'read_process', side_effect=values.get):
            self.assertEqual(runner.process_tree(10, 'original-root', seen), {})
        opened, signals = self.cleanup_observation(values.get, seen)
        self.assertEqual(seen, {10: ('original-root', 1024)})
        self.assertEqual(opened, [])
        self.assertTrue(signals)
        self.assertEqual({binding for binding, _ in signals}, {(10, 'original-root')})

    def test_reused_seen_child_never_replaces_birth_or_enrolls_descendants(self):
        values = {10: observed('original-root', 1, (20,)),
                  20: observed('reused-child', 10, (30,)), 30: observed('foreign-grandchild', 20)}
        seen = {10: ('original-root', 1024), 20: ('original-child', 1024)}
        with patch.object(runner, 'read_process', side_effect=values.get):
            self.assertEqual(set(runner.process_tree(10, 'original-root', seen)), {10})
        opened, signals = self.cleanup_observation(values.get, seen)
        self.assertEqual(seen[20][0], 'original-child'); self.assertNotIn(30, seen)
        self.assertEqual(set(opened), {20})
        self.assertEqual({binding for binding, _ in signals}, {(10, 'original-root')})

    def test_child_with_wrong_ppid_is_not_enrolled(self):
        values = {10: observed('original-root', 1, (20,)), 20: observed('foreign-child', 77)}
        seen = {10: ('original-root', 1024)}
        with patch.object(runner, 'read_process', side_effect=values.get):
            self.assertEqual(set(runner.process_tree(10, 'original-root', seen)), {10})
        self.assertNotIn(20, seen)

    def test_parent_birth_change_during_child_enrollment_is_refused(self):
        calls = 0
        def observer(pid):
            nonlocal calls
            if pid == 10:
                calls += 1
                return observed('changed-parent' if calls == 4 else 'original-root', 1, (20,))
            return observed('child', 10)
        seen = {10: ('original-root', 1024)}
        with patch.object(runner, 'read_process', side_effect=observer):
            self.assertEqual(set(runner.process_tree(10, 'original-root', seen)), {10})
        self.assertGreaterEqual(calls, 4); self.assertNotIn(20, seen)

    def test_normal_owned_descendants_use_only_verified_births_and_fds(self):
        values = {10: observed('original-root', 1, (20,)),
                  20: observed('original-child', 10, (30,)), 30: observed('original-grandchild', 20)}
        seen = {10: ('original-root', 1024)}
        with patch.object(runner, 'read_process', side_effect=values.get):
            self.assertEqual(set(runner.process_tree(10, 'original-root', seen)), {10, 20, 30})
        opened, signals = self.cleanup_observation(values.get, seen)
        self.assertEqual(set(opened), {20, 30})
        self.assertEqual({binding for binding, _ in signals},
                         {(10, 'original-root'), (20, 'original-child'), (30, 'original-grandchild')})

    def test_proc_stat_birth_or_ppid_change_invalidates_observation(self):
        for change in ('birth', 'ppid'):
            calls = 0
            def read(path, *args, **kwargs):
                nonlocal calls
                if path.name == 'stat':
                    calls += 1; fields = ['0'] * 20; fields[0] = 'S'
                    fields[1] = '77' if calls == 2 and change == 'ppid' else '1'
                    fields[19] = 'reused' if calls == 2 and change == 'birth' else 'original'
                    return '10 (fixture) ' + ' '.join(fields)
                if path.name == 'status': return 'VmRSS: 12 kB\n'
                return '20'
            with self.subTest(change=change), patch.object(Path, 'read_text', read):
                self.assertIsNone(runner.read_process(10))


if __name__ == '__main__':
    unittest.main(verbosity=2)
