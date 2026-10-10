"""Finite phase-order/continuation controls over owned opaque metadata only.

Native Collector validates its metadata prefix. Provider/runtime guards in the
order controls are explicit stubs, so those controls certify call order only.
No scientific state, SQL, backend, authentication or provider is executed.
"""
import copy
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from dams_sim.storage import digest
from research_tools import compute_only_entry as entry_module
from research_tools import compute_only_lifecycle as lifecycle
from research_tools import compute_only_backend_factory as factory_module
from research_tools.compute_only_control import blob, Collector, COLLECTOR_SCHEMA


ROOT = Path(__file__).absolute().parents[1]
spec = importlib.util.spec_from_file_location(
    'r6_owned_factory_fixture', ROOT / 'tests/test_archive_entry_factory.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


class LaunchOrderTests(unittest.TestCase):
    def setUp(self):
        # Reuse builders, without inheriting or invoking the original 24 tests.
        self.fixture = fixture_module.FactoryTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.c = copy.deepcopy(self.fixture.c)
        self.c.update(authorization_id='r6-opaque-continuation-authorization',
                      budget_cap_usd='100', reserve_usd='5', prior_spend_usd='2',
                      region='fixture-region', bucket='fixture-bucket',
                      service_account='fixture-no-service-account',
                      subnet='fixture-subnet', image='fixture-image',
                      image_id='fixture-image-id', network_mode='internal-offline',
                      zones=['fixture-zone'], max_create_attempts=1, cost_margin='1.25')
        self.stage = self.c['stages'][0]
        self.stage['expected_guest'] = {
            'architecture': 'x86_64', 'vcpus': 2,
            'memory_gib_min': '1', 'memory_gib_max': '4'}
        self.termination = self.c['compute_only']['prepare']['planned_termination_utc']
        self.manifest_sha = self.c['compute_only']['package_manifest']['sha256']
        self.legacy = copy.deepcopy(self.fixture.admission)
        self.legacy['schema'] = COLLECTOR_SCHEMA
        self.legacy.pop('storage_profile')
        self.legacy.update(max_fetch_requests=20, max_fetch_bytes=10000)
        for role in ('state', 'cache', 'copy1', 'copy2'):
            (self.root / ('launch-' + role)).mkdir()
        self.legacy.update(state_dir=str(self.root / 'launch-state'),
                           cache_dir=str(self.root / 'launch-cache'),
                           copy_dirs=[str(self.root / 'launch-copy1'),
                                      str(self.root / 'launch-copy2')])
        self.legacy_ref = self.fixture.ref(self.legacy)
        with Collector(self.legacy_ref['path'],
                       admission_sha256=self.legacy_ref['sha256'],
                       assignment_raw=self.fixture.assignment) as collector:
            ticket = collector.reserve_metadata_attempt(64)
            with collector.locked():
                collector._accept_metadata(ticket, b'owned opaque doctor result')
            self.launch_head = dict(collector.head)
        attempt = {'action': 'doctor', 'argv_sha256': 'c' * 64,
                   'input_sha256': digest(b''), 'input_bytes': 0,
                   'deadline_utc': self.termination}
        self.head_wrapper = {'head': self.launch_head, 'attempt': attempt}
        self.head_ref = self.fixture.ref(self.head_wrapper)
        # The one native bridge reservation consumes the original launch pool.
        bridge = blob({'schema': 'dams-compute-launch-archive-bridge-v1',
                       'continuation_sha256': '0' * 64,
                       'archive_admission_sha256': '0' * 64,
                       'launch_admission_sha256': self.legacy_ref['sha256'],
                       'launch_head': self.launch_head})
        self.bridge_bound = len(bridge) + 1
        self.admission = copy.deepcopy(self.fixture.admission)
        self.admission['max_fetch_requests'] = 20 - self.launch_head['requests'] - 1
        self.admission['max_fetch_bytes'] = 10000 - self.launch_head['bytes'] - self.bridge_bound
        self.admission_ref = self.fixture.ref(self.admission)
        intent = lifecycle.cc.digest({'source': self.c['source_commit'], 'stage': self.stage,
                    'cloud_resource_identity': {k: self.c[k] for k in
                        ('project', 'region', 'bucket', 'service_account', 'subnet',
                         'image', 'image_id', 'network_mode', 'gcloud_configuration', 'gcloud_account')}})
        plan = lifecycle.cc.digest({'stages': self.c['stages'], 'source': self.c['source_commit'],
                    'control': {k: self.c.get(k) for k in
                        ('project', 'region', 'zones', 'bucket', 'service_account', 'subnet',
                         'image', 'image_id', 'network_mode', 'gcloud_configuration',
                         'gcloud_account', 'max_create_attempts', 'cost_margin')}})
        self.hold = {'intent_sha256': intent, 'reserved_usd': '10',
                     'termination_utc': self.termination,
                     'state': 'running', 'attempts': 1}
        self.ledger = {'authorization_id': self.c['authorization_id'],
                       'version': 1, 'events': [], 'frozen_plan_sha256': plan,
                       'deadline_utc': self.c['global_deadline_utc'],
                       'cap_usd': self.c['budget_cap_usd'],
                       'reserve_usd': self.c['reserve_usd'],
                       'prior_spend_usd': self.c['prior_spend_usd'],
                       'entries': {self.stage['id']: self.hold}}
        self.ledger_path = self.root / 'owned-launch-ledger.json'
        self.ledger_path.write_bytes(blob(self.ledger))
        self.approval = {'schema': 'dams-root-compute-live-approval-v1',
                         'authorization_id': self.c['authorization_id'],
                         'stage_id': self.stage['id'],
                         **{k: self.hold[k] for k in
                            ('intent_sha256', 'reserved_usd', 'termination_utc')},
                         'source_manifest_sha256': self.manifest_sha,
                         'paid_actions_authorized': True}
        self.approval_ref = self.fixture.ref(self.approval)
        vm = {'id': 'opaque-fixture', 'zone': 'fixture-zone'}
        disk = {'id': 'opaque-disk'}
        provider_sha = digest(blob({'vm': vm, 'disk': disk}))
        transfer = {'stage_id': self.stage['id'],
                    'expected_source_sha256': self.admission['source_sha256'],
                    'deadline_utc': self.termination}
        self.transfer_ref = self.fixture.ref(transfer)
        runtime = {'source_commit': self.c['source_commit'],
                   'source_manifest_sha256': self.manifest_sha,
                   'spec': self.stage['spec'], 'scale': self.stage['scale'],
                   'provider_identity_sha256': provider_sha,
                   'deadline_utc': self.termination,
                   'transfer_config_sha256': self.transfer_ref['sha256']}
        self.runtime_ref = self.fixture.ref(runtime)
        self.provider_ref = self.fixture.ref({'vm': vm, 'disk': disk,
                           'termination_utc': self.termination,
                           'approval_sha256': self.approval_ref['sha256']})
        self.doctor_ref = self.fixture.ref({'provider_identity_sha256': provider_sha,
                         'source_manifest_sha256': self.manifest_sha,
                         'measurements': {'instance_id': vm['id']}})
        self.payload = {
            'schema': 'dams-compute-launch-collector-continuation-v1',
            'launch_admission': self.legacy_ref,
            'launch_head_receipt': self.head_ref,
            'approval': self.approval_ref,
            'ledger_path': str(self.ledger_path),
            'archive_admission_sha256': self.admission_ref['sha256'],
            'provider': self.provider_ref, 'doctor': self.doctor_ref,
            'runtime': self.runtime_ref, 'transfer': self.transfer_ref}
        self.cost = mock.patch('research_tools.cloud_control.stage_cost',
                               return_value=Decimal('1'))
        self.cost.start()
        self.addCleanup(self.cost.stop)
        # Pool controls intentionally exclude full hardware/runtime validation.
        # Still retain every exact enrollment ref in Entry's original drift guard.
        def opaque_enrollment(guard):
            from research_tools.compute_only_control import parse
            provider = parse(guard.entry.reference(guard.value['provider']))
            guard.entry.reference(guard.value['doctor'])
            runtime_raw = guard.entry.reference(guard.value['runtime'])
            transfer_raw = guard.entry.reference(guard.value['transfer'])
            guard.runtime_value = parse(runtime_raw); guard.provider_value = provider
            return (provider['vm'], digest(runtime_raw),
                    digest(blob({'vm': provider['vm'], 'disk': provider['disk']})),
                    digest(transfer_raw))
        enrollment = mock.patch.object(entry_module.PhaseContinuation, '_enrollment', opaque_enrollment)
        enrollment.start(); self.addCleanup(enrollment.stop)

    def make_entry(self, *, payload=None, admission=None, options=None, legacy=False):
        config = copy.deepcopy(self.c)
        self.admission_ref = self.fixture.ref(self.admission if admission is None else admission)
        value = copy.deepcopy(self.payload if payload is None else payload)
        value['archive_admission_sha256'] = self.admission_ref['sha256']
        config['compute_only']['phase_continuation'] = self.fixture.ref(value)
        if options is not None:
            config['compute_only']['phase'] = 'lifecycle'
            config['compute_only'].pop('collect')
            config['compute_only']['lifecycle'] = self.fixture.ref(options)
        if legacy:
            config['compute_only'].pop('phase_continuation')
            config['compute_only'].pop('archive_backend_factory')
            config['compute_only']['component_sha256'] = {
                n: v for n, v in config['compute_only']['component_sha256'].items()
                if n in entry_module.COMPONENTS}
        return self.fixture.entry(config)

    def guard(self, entry):
        return entry.phase_continuation(self.admission_ref, self.fixture.assignment)

    def no_factory(self):
        return mock.patch.object(factory_module, 'Factory',
                                 side_effect=AssertionError('factory reached before guard'))

    def test_valid_native_prefix_accepts_without_factory_or_new_allowance(self):
        entry = self.make_entry()
        with self.no_factory() as factory:
            guard = self.guard(entry)
            guard.check()
            entry.check(full=True)
        factory.assert_not_called()
        self.assertEqual(self.launch_head['requests'], 1)
        self.assertEqual(guard.committed_head['requests'], self.launch_head['requests'] + 1)
        self.assertEqual(self.admission['max_fetch_requests'] + guard.committed_head['requests'], 20)
        self.assertEqual(self.admission['max_fetch_bytes'] + guard.committed_head['bytes'], 10000)

    def test_scientific_binding_or_assignment_difference_refuses_before_factory(self):
        for field in ('stage_id', 'source_sha256', 'pipeline_driver_sha256',
                      'spec_sha256', 'inventory_sha256', 'assignment_sha256',
                      'codec_source_sha256', 'collector_source_sha256', 'deadline_utc'):
            with self.subTest(field=field):
                admission = copy.deepcopy(self.admission)
                admission[field] = 'different-stage' if field == 'stage_id' else (
                    '2029-01-01T00:00:01Z' if field == 'deadline_utc' else 'a' * 64)
                entry = self.make_entry(admission=admission)
                with self.no_factory() as factory, self.assertRaises((ValueError, TimeoutError)):
                    self.guard(entry)
                factory.assert_not_called()
        entry = self.make_entry()
        with self.no_factory() as factory, self.assertRaises(ValueError):
            entry.phase_continuation(self.admission_ref, b'{}')
        factory.assert_not_called()

    def test_head_must_be_used_native_prefix_and_exact_current_head(self):
        for head in ({'sequence': 0, 'sha256': None, 'requests': 0, 'bytes': 0},
                     {**self.launch_head, 'sha256': 'a' * 64},
                     {**self.launch_head, 'bytes': self.launch_head['bytes'] + 1}):
            with self.subTest(head=head):
                payload = copy.deepcopy(self.payload)
                payload['launch_head_receipt'] = self.fixture.ref(
                    {'head': head, 'attempt': self.head_wrapper['attempt']})
                entry = self.make_entry(payload=payload)
                with self.no_factory() as factory, self.assertRaises(ValueError):
                    self.guard(entry)
                factory.assert_not_called()

    def test_request_and_byte_caps_cannot_reset_launch_spend(self):
        for field in ('max_fetch_requests', 'max_fetch_bytes'):
            with self.subTest(field=field):
                admission = copy.deepcopy(self.admission)
                admission[field] += 1
                entry = self.make_entry(admission=admission)
                with self.no_factory() as factory, self.assertRaises(ValueError):
                    self.guard(entry)
                factory.assert_not_called()

    def test_changed_approval_or_original_hold_refuses_before_factory(self):
        for target, field, value in (
                ('approval', 'intent_sha256', 'a' * 64),
                ('approval', 'source_manifest_sha256', 'a' * 64),
                ('ledger', 'cap_usd', '101'),
                ('hold', 'reserved_usd', '11'),
                ('hold', 'termination_utc', '2029-01-01T00:00:01Z')):
            with self.subTest(target=target, field=field):
                payload = copy.deepcopy(self.payload)
                ledger = copy.deepcopy(self.ledger)
                if target == 'approval':
                    approval = {**self.approval, field: value}
                    payload['approval'] = self.fixture.ref(approval)
                elif target == 'ledger':
                    ledger[field] = value
                else:
                    ledger['entries'][self.stage['id']][field] = value
                self.ledger_path.write_bytes(blob(ledger))
                entry = self.make_entry(payload=payload)
                with self.no_factory() as factory, self.assertRaises(ValueError):
                    self.guard(entry)
                factory.assert_not_called()
                self.ledger_path.write_bytes(blob(self.ledger))

    def test_expired_original_collector_deadline_refuses_before_factory(self):
        entry = self.make_entry()
        with self.no_factory() as factory, \
                mock.patch('research_tools.compute_only_control.time.time', return_value=2000000000), \
                self.assertRaises((TimeoutError, ValueError)):
            self.guard(entry)
        factory.assert_not_called()

    def test_new_state_and_cache_must_be_disjoint_from_launch_namespace(self):
        old_state = Path(self.legacy['state_dir'])
        old_cache = Path(self.legacy['cache_dir'])
        nested_state = old_state / 'owned-nested-state'
        nested_cache = old_cache / 'owned-nested-cache'
        nested_state.mkdir(); nested_cache.mkdir()
        cases = (
            {'state_dir': str(old_state)}, {'cache_dir': str(old_cache)},
            {'state_dir': str(old_cache)}, {'cache_dir': str(old_state)},
            {'state_dir': str(old_cache), 'cache_dir': str(old_state)},
            {'state_dir': str(nested_state)}, {'cache_dir': str(nested_cache)},
            {'state_dir': str(nested_cache)}, {'cache_dir': str(nested_state)},
            {'state_dir': str(self.root)}, {'cache_dir': str(self.root)})
        for changes in cases:
            with self.subTest(changes=changes):
                admission = copy.deepcopy(self.admission)
                admission.update(changes)
                entry = self.make_entry(admission=admission)
                with self.no_factory() as factory, self.assertRaises(ValueError):
                    self.guard(entry)
                factory.assert_not_called()

    def test_late_launch_head_advance_fails_registered_guard(self):
        entry = self.make_entry()
        guard = self.guard(entry)
        old = guard.launch
        old.reserve_metadata_attempt(8)
        with self.no_factory() as factory, self.assertRaises(ValueError):
            guard.check()
        factory.assert_not_called()
        with self.assertRaises(ValueError):
            entry.check(full=True)

    def test_bridge_replay_keeps_one_native_request_and_cannot_bind_second_pool(self):
        first_entry = self.make_entry()
        first = self.guard(first_entry)
        committed = dict(first.committed_head)
        first_entry.close()
        second_entry = self.make_entry()
        with self.no_factory() as factory:
            second = self.guard(second_entry)
            self.assertEqual(second.committed_head, committed)
            self.assertEqual(second.launch.head, committed)
        factory.assert_not_called()
        second_entry.close()
        admission = copy.deepcopy(self.admission)
        for role in ('second-pool-state', 'second-pool-cache'):
            (self.root / role).mkdir()
        admission['state_dir'] = str(self.root / 'second-pool-state')
        admission['cache_dir'] = str(self.root / 'second-pool-cache')
        other = self.make_entry(admission=admission)
        with self.no_factory() as factory, self.assertRaises(ValueError):
            self.guard(other)
        factory.assert_not_called()

    def test_missing_bridge_publication_or_native_prefix_cannot_reset_pool(self):
        entry = self.make_entry()
        guard = self.guard(entry)
        marker = Path(self.legacy['state_dir']) / guard.marker
        marker_raw = marker.read_bytes()
        entry.close()
        marker.unlink()
        reopened = self.make_entry()
        with self.no_factory() as factory, self.assertRaises(ValueError):
            self.guard(reopened)
        factory.assert_not_called()
        reopened.close()
        # Restore only our synthetic publication to isolate native prefix loss.
        marker.write_bytes(marker_raw); marker.chmod(0o600)
        record = Path(self.legacy['state_dir']) / 'reservations/00000002.json'
        record.unlink()
        reopened = self.make_entry()
        with self.no_factory() as factory, self.assertRaises(ValueError):
            self.guard(reopened)
        factory.assert_not_called()

    def test_archive_operation_requires_explicit_continuation(self):
        config = copy.deepcopy(self.c)
        entry = self.fixture.entry(config)
        with self.no_factory() as factory, self.assertRaises(ValueError):
            self.guard(entry)
        factory.assert_not_called()

    def test_lifecycle_archive_resolver_is_lazy(self):
        options = self.lifecycle_options('activate')
        entry = self.make_entry(options=options)
        with self.no_factory() as factory:
            resolver = entry.archive_backends(self.admission_ref)
        self.assertTrue(callable(resolver))
        factory.assert_not_called()

    def lifecycle_options(self, phase, *, legacy=False):
        for role in ('terminal1', 'terminal2', 'heads', 'keys'):
            (self.root / role).mkdir(exist_ok=True)
        key = self.root / 'keys/id-fixture'
        key.write_bytes(b'opaque fixture key'); key.with_suffix('.pub').write_bytes(b'opaque fixture pub')
        key.chmod(0o600); key.with_suffix('.pub').chmod(0o600)
        runtime = None if legacy else self.runtime_ref
        transfer = None if legacy else self.transfer_ref
        return {'schema': lifecycle.SCHEMA, 'phase': phase,
                'ledger_path': str(self.ledger_path), 'approval': self.approval_ref,
                'collector_admission': self.legacy_ref if legacy else self.admission_ref,
                'assignment': self.fixture.ref(self.fixture.assignment),
                'minimum_head': self.fixture.ref(self.launch_head) if legacy else None,
                'head_receipt_dir': str(self.root / 'heads'), 'ssh_key_file': str(key),
                'known_hosts': None, 'network_tag': 'fixture-tag',
                'runtime': runtime, 'transfer': transfer, 'ack_generation': None,
                'terminal_copy_dirs': [str(self.root / 'terminal1'), str(self.root / 'terminal2')],
                'max_terminal_files': 20, 'max_terminal_bytes': 10000,
                'max_command_seconds': 10, 'max_stdout_bytes': 1024, 'overhead_bytes': 64}

    def test_nonlaunch_provider_runtime_refusal_precedes_lazy_factory(self):
        options = self.lifecycle_options('activate')
        entry = self.make_entry(options=options)
        events = []
        class Ledger:
            def __init__(self, *args): pass
            def reserve(inner, stage): return self.hold
        def provider(live):
            events.append('provider')
            live.vm = {'id': 'opaque-fixture'}; live.disk = {'id': 'opaque-disk'}
        def host(live):
            events.append('host'); live.host_path = '/opaque-fixture-known-hosts'
        def runtime(live):
            events.append('runtime'); raise ValueError('intentional runtime guard refusal')
        with self.no_factory() as factory, \
                mock.patch.object(lifecycle.cc, 'Ledger', Ledger), \
                mock.patch.object(lifecycle.cc, 'Gcloud', side_effect=AssertionError('provider wrapper constructed')), \
                mock.patch.object(lifecycle.Live, '_load_provider', provider), \
                mock.patch.object(lifecycle.Live, 'load_host_enrollment', host), \
                mock.patch.object(lifecycle.Live, 'load_runtime', runtime), \
                self.assertRaisesRegex(ValueError, 'runtime guard refusal'):
            lifecycle.Live(entry, options, g=object(), archive_backends=lambda: factory())
        self.assertEqual(events, ['provider', 'host', 'runtime'])
        factory.assert_not_called()

    def test_successful_nonlaunch_order_guards_then_native_bridge_then_lazy_factory(self):
        # Archive Collector and factory are stubs here. The launch bridge remains
        # native; this proves sequencing without claiming archive/policy admission.
        from research_tools.compute_only_control import parse
        options = self.lifecycle_options('activate')
        entry = self.make_entry(options=options)
        provider_value = parse(Path(self.provider_ref['path']).read_bytes())
        runtime_value = parse(Path(self.runtime_ref['path']).read_bytes())
        job = {'runtime_sha256': self.runtime_ref['sha256'],
               'provider_identity_sha256': runtime_value['provider_identity_sha256']}
        events = []
        class Ledger:
            def __init__(self, *args): pass
            def reserve(inner, stage): return self.hold
        def provider(live):
            events.append('provider')
            live.vm = provider_value['vm']; live.disk = provider_value['disk']
        def host(live):
            events.append('host'); live.host_path = '/opaque-fixture-known-hosts'
        def runtime(live):
            events.append('runtime'); live.runtime = runtime_value
        original_continuation = entry.phase_continuation
        def continuation(*args):
            events.append('continuation')
            return original_continuation(*args)
        class Factory:
            def __init__(inner, *args):
                events.append('factory-construct'); inner.meter = None
            def __call__(inner):
                events.append('factory-materialize'); return ['opaque-adapters']
            def close(inner): pass
        class ArchiveCollector:
            def __init__(inner, *args, archive_backends, **kwargs):
                events.append('collector-source-history')
                inner.adapters = archive_backends()
                inner.archive = SimpleNamespace(p={'mode': 'external-persistent', 'job': job})
                inner.head = {'sequence': 0, 'sha256': None, 'requests': 0, 'bytes': 0}
            def check(inner): pass
            def close(inner): pass
            def __enter__(inner): return inner
            def __exit__(inner, *args): inner.close()
        resolver = entry.archive_backends(options['collector_admission'])
        with mock.patch.object(lifecycle.cc, 'Ledger', Ledger), \
                mock.patch.object(lifecycle.Live, '_load_provider', provider), \
                mock.patch.object(lifecycle.Live, 'load_host_enrollment', host), \
                mock.patch.object(lifecycle.Live, 'load_runtime', runtime), \
                mock.patch.object(entry, 'phase_continuation', side_effect=continuation), \
                mock.patch.object(lifecycle, 'Collector', ArchiveCollector), \
                mock.patch.object(factory_module, 'Factory', Factory):
            with lifecycle.Live(entry, options, g=object(), archive_backends=resolver) as live:
                self.assertEqual(live.collector.adapters, ['opaque-adapters'])
                self.assertEqual(entry.phase_guards[0].committed_head['requests'], 2)
        self.assertEqual(events, ['provider', 'host', 'runtime', 'continuation',
                                  'collector-source-history', 'factory-construct', 'factory-materialize'])

    def test_legacy_launch_keeps_early_native_metadata_collector_without_runtime(self):
        options = self.lifecycle_options('launch', legacy=True)
        self.hold['state'] = 'reserved'; self.hold['attempts'] = 0
        self.ledger_path.write_bytes(blob(self.ledger))
        entry = self.make_entry(options=options, legacy=True)
        class Ledger:
            def __init__(self, *args): pass
            def reserve(inner, stage): return self.hold
        with self.no_factory() as factory, \
                mock.patch.object(lifecycle.cc, 'Ledger', Ledger), \
                mock.patch.object(lifecycle.Live, '_load_provider', side_effect=AssertionError('launch loaded provider')), \
                mock.patch.object(lifecycle.Live, 'load_runtime', side_effect=AssertionError('launch loaded runtime')):
            with lifecycle.Live(entry, options, g=object()) as live:
                self.assertIsNone(live.runtime)
                self.assertIsNone(live.collector.archive)
                self.assertEqual(live.collector.head, self.launch_head)
                self.assertIsNone(options['runtime'])
                self.assertIsNone(options['transfer'])
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
