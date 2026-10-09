"""Finite activation reply-loss seam; fake guest/SDK, actual local ledger only."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_compute_only_lifecycle as old
from test_compute_only_preservation_repair import fixture_manifest
from research_tools.compute_only_control import blob, parse

ROOT = Path(__file__).absolute().parents[1]


class ActivationUncertaintyTests(unittest.TestCase):
    def setUp(self):
        external = Path(os.environ['DAMS_PRESERVATION_TEST_DIR'])
        external.mkdir(exist_ok=True)
        manifest = fixture_manifest(external)
        original = old.ref
        self.pin = patch.object(old, 'ref', side_effect=lambda p: manifest if Path(p) == ROOT/'source-manifest.json' else original(p))
        self.pin.start()
        self.f = old.LifecycleTests('test_01_no_original_hold_or_wrong_approval_no_provider')
        self.f._testMethodName = self._testMethodName
        self.f.setUp()
        self.x = self.f.make()
        self.f.establish(self.x)
        self.f.runtime(self.x)
        self.before = parse((self.f.work/'budget.json').read_bytes())
        self.guest_started = False

    def tearDown(self):
        self.f.tearDown()
        self.pin.stop()

    def lost_reply(self, action, raw=b'', bound=None):
        self.assertEqual(action, 'activate')
        self.assertEqual(parse(raw)['heartbeat']['sequence'], 0)
        self.guest_started = True
        raise ValueError('fake guest services started; activation reply lost')

    def assert_retained(self):
        after = parse((self.f.work/'budget.json').read_bytes())
        entry = after['entries'][self.f.s['id']]
        before = self.before['entries'][self.f.s['id']]
        self.assertEqual(entry['state'], 'uncertain')
        for key in ('reserved_usd', 'termination_utc', 'intent_sha256', 'attempts'):
            self.assertEqual(entry[key], before[key])
        for key in ('authorization_id', 'deadline_utc', 'cap_usd', 'reserve_usd', 'prior_spend_usd', 'frozen_plan_sha256'):
            self.assertEqual(after[key], self.before[key])
        self.assertTrue(self.f.sdk.exists)
        self.assertFalse(any('delete' in argv for argv in self.f.sdk.calls))
        self.assertTrue((self.f.work/'state'/(self.f.s['id']+'-activation-BEGIN.json')).is_file())
        self.assertFalse((self.f.work/'state'/(self.f.s['id']+'-activated.json')).exists())
        self.assertFalse((self.f.work/'state'/(self.f.s['id']+'-lease-00000000.json')).exists())

    def test_01_guest_started_reply_lost_does_not_delete_unique_bytes(self):
        with patch.object(self.x, 'exchange', side_effect=self.lost_reply):
            with self.assertRaisesRegex(ValueError, 'activation reply lost'):
                self.x.activate()
        self.assertTrue(self.guest_started)
        self.assert_retained()

    def test_02_unknown_activation_cannot_retry_or_reset_original_attempt(self):
        with patch.object(self.x, 'exchange', side_effect=self.lost_reply):
            with self.assertRaises(ValueError):
                self.x.activate()
        counter = dict(self.x.collector.head)
        begin = (self.f.work/'state'/(self.f.s['id']+'-activation-BEGIN.json')).read_bytes()
        with patch.object(self.x, 'exchange') as again:
            with self.assertRaisesRegex(ValueError, 'activation already attempted'):
                self.x.activate()
            again.assert_not_called()
        self.assertEqual(self.x.collector.head, counter)
        self.assertEqual((self.f.work/'state'/(self.f.s['id']+'-activation-BEGIN.json')).read_bytes(), begin)
        self.assert_retained()

    def test_03_known_success_retains_exact_activation_receipt_and_lease(self):
        result = {'services_activated': True, 'science_complete': False}
        with patch.object(self.x, 'exchange', return_value=blob(result)) as exchange:
            self.assertEqual(self.x.activate(), result)
        self.assertEqual(exchange.call_count, 1)
        self.assertEqual(parse((self.f.work/'state'/(self.f.s['id']+'-activated.json')).read_bytes()), result)
        lease = parse((self.f.work/'state'/(self.f.s['id']+'-lease-00000000.json')).read_bytes())
        self.assertEqual(lease['sequence'], 0)
        self.assertTrue(self.f.sdk.exists)
        self.assertFalse(any('delete' in argv for argv in self.f.sdk.calls))
        self.assertEqual(parse((self.f.work/'budget.json').read_bytes()), self.before)

