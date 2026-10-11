"""Offline operational contracts; no provider request or scientific case runs."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'research_tools')]
sys.dont_write_bytecode = True
import cloud_control as cc
import cloud_worker as cw
import compute_only_worker as only


def profile():
    config = {'region': 'us-central1', 'zones': ['us-central1-a'],
              'price_snapshot': {'machines': {'n2-highmem-4': {
                  'vcpus': 4, 'memory_gib': 32, 'zone': 'us-central1-a'}}},
              'authorization_id': 'synthetic-n2-test', 'project': 'synthetic-research',
              'source_commit': 'a' * 40, 'image': 'projects/test/global/images/frozen',
              'image_id': '12345', 'subnet': 'projects/test/regions/us-central1/subnetworks/test',
              'network_mode': 'internal-offline', 'global_deadline_utc': '2030-01-02T00:00:00Z'}
    stage = {'id': 'n2-stage', 'purchase_mode': 'STANDARD', 'machine_type': 'n2-highmem-4',
             'disk_type': 'pd-balanced', 'disk_gib': 200, 'node_count': 1,
             'architecture_declaration': 'official-n2-x86_64',
             'quota': {'metric': 'N2_CPUS', 'info_id': 'N2-CPUS-per-project-region',
                       'dimensions': {'region': 'us-central1'}},
             'expected_guest': {'architecture': 'x86_64', 'vcpus': 4,
                                'memory_gib_min': 31, 'memory_gib_max': 32}}
    return config, stage


def observations(machine='n2-highmem-4'):
    interface = 'SCSI' if machine == 'n2-highmem-4' else 'NVME'
    maximum = 32 if machine == 'n2-highmem-4' else 31
    runtime = {'purchase_mode': 'STANDARD', 'machine_type': machine,
               'expected_guest': {'architecture': 'x86_64', 'vcpus': 4,
                                  'memory_gib_min': maximum - 1, 'memory_gib_max': maximum}}
    actual = {'machine_type': machine,
              'guest_scheduling': {'preemptible': 'FALSE', 'automatic_restart': 'FALSE',
                                   'on_host_maintenance': 'TERMINATE'},
              'guest_architecture': 'x86_64', 'guest_cpu_count': 4,
              'guest_cpu_affinity': [0, 1, 2, 3],
              'guest_memory_total_bytes': int((maximum - .5) * 2**30),
              'guest_memory_total_source': 'proc-meminfo',
              'guest_boot_disk_metadata_interface': interface,
              'guest_root_block': {'root_device': '8:1', 'interface': interface}}
    return runtime, actual


class FakeGcloud:
    def __init__(self, records): self.records = list(records); self.calls = []
    def run(self, args, **kwargs):
        self.calls.append(args)
        if not self.records: raise AssertionError('unexpected provider request')
        value = self.records.pop(0)
        return value if isinstance(value, dict) and 'exit' in value else {'exit': 0, 'stdout': json.dumps(value)}


def quota_records():
    return [{'quotaId': 'N2-CPUS-per-project-region', 'metric': 'compute.googleapis.com/n2_cpus',
             'dimensionsInfos': [{'dimensions': {'region': 'us-central1'}, 'details': {'value': '200'}}]},
            {'name': 'us-central1', 'quotas': [{'metric': 'N2_CPUS', 'limit': 200, 'usage': 0}]},
            {'quotas': [{'metric': 'CPUS_ALL_REGIONS', 'limit': 384, 'usage': 0}]}]


def provider_fixture(c, s):
    expiry = {'instance_name': 'synthetic-n2', 'termination_utc': '2030-01-01T01:00:00Z'}
    vm = {'name': expiry['instance_name'], 'labels': cc.labels(c, s),
          'machineType': 'zones/us-central1-a/machineTypes/n2-highmem-4', 'zone': 'zones/us-central1-a',
          'scheduling': {'provisioningModel': 'STANDARD', 'instanceTerminationAction': 'DELETE',
                         'terminationTime': expiry['termination_utc'], 'automaticRestart': False,
                         'onHostMaintenance': 'TERMINATE'},
          'serviceAccounts': [], 'networkInterfaces': [{'nicType': 'GVNIC', 'subnetwork': c['subnet']}],
          'metadata': {'items': [{'key': k, 'value': v} for k, v in {
              'enable-oslogin': 'TRUE', 'block-project-ssh-keys': 'TRUE',
              'dams-source-commit': c['source_commit'], 'dams-source-image-id': c['image_id']}.items()]},
          'disks': [{'boot': True, 'autoDelete': True, 'interface': 'SCSI', 'source': 'zones/us-central1-a/disks/root'}]}
    return expiry, vm


class ProfileControls(unittest.TestCase):
    def test_n2_pd_balanced_scsi_missing_catalog_arch_is_not_fabricated(self):
        c, s = profile(); before = copy.deepcopy(c)
        self.assertEqual(cc.stage_capabilities(c, s), {'purchase_mode': 'STANDARD', 'disk_type': 'pd-balanced',
                                                     'disk_interface': 'SCSI', 'quota': s['quota']})
        self.assertEqual(c, before); self.assertNotIn('architecture', c['price_snapshot']['machines'][s['machine_type']])
    def test_n2_pd_ssd_scsi(self):
        c, s = profile(); s['disk_type'] = 'pd-ssd'
        self.assertEqual(cc.stage_capabilities(c, s)['disk_interface'], 'SCSI')
    def test_purchase_mode_explicit_only(self):
        for mode in ('SPOT', 'OTHER', None):
            c, s = profile()
            if mode is None: s.pop('purchase_mode')
            else: s['purchase_mode'] = mode
            with self.subTest(mode=mode), self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_wrong_quota_dimensions_or_pool(self):
        for key, val in [('metric', 'M3_CPUS'), ('info_id', 'PREEMPTIBLE-CPUS-per-project-region'), ('dimensions', {'region': 'us-central1', 'vm_family': 'N2'})]:
            c, s = profile(); s['quota'][key] = val
            with self.subTest(key=key), self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_wrong_cpu_ram_arch_catalog(self):
        for key, val in [('vcpus', True), ('vcpus', 8), ('memory_gib', 31), ('architecture', 'ARM64')]:
            c, s = profile(); c['price_snapshot']['machines'][s['machine_type']][key] = val
            with self.subTest(key=key, val=val), self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_wrong_or_multiple_zone(self):
        for zones in (['us-central1-b'], ['us-central1-a', 'us-central1-b']):
            c, s = profile(); c['zones'] = zones
            with self.subTest(zones=zones), self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
        c, s = profile(); c['price_snapshot']['machines'][s['machine_type']]['zone'] = 'europe-west1-a'; c['zones'] = ['europe-west1-a']
        with self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_explicit_architecture_declaration_required(self):
        c, s = profile(); s.pop('architecture_declaration')
        with self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_exact_expected_cpu_arch_ram(self):
        for key, val in [('vcpus', True), ('vcpus', 8), ('architecture', 'aarch64'), ('memory_gib_max', 31), ('memory_gib_min', 32)]:
            c, s = profile(); s['expected_guest'][key] = val
            with self.subTest(key=key, val=val), self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_no_hyperdisk_or_inherited_provisioning(self):
        for key, val in [('disk_type', 'hyperdisk-balanced'), ('disk_iops', 3000), ('disk_throughput_mibps', 140)]:
            c, s = profile(); s[key] = val
            with self.subTest(key=key), self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_no_multi_node(self):
        for key, val in [('node_count', 2), ('node_count', True), ('nodes', ['node'])]:
            c, s = profile(); s[key] = val
            with self.subTest(key=key), self.assertRaises(cc.GuardError): cc.stage_capabilities(c, s)
    def test_legacy_c4d_spot_remains_nvme(self):
        c, s = profile(); s = {'machine_type': 'c4d-highmem-96'}
        caps = cc.stage_capabilities(c, s)
        self.assertEqual((caps['purchase_mode'], caps['disk_interface'], caps['quota']['metric']), ('SPOT', 'NVME', 'PREEMPTIBLE_CPUS'))
    def test_legacy_m3_standard_remains_nvme(self):
        c, s = profile(); s['machine_type'] = 'm3-ultramem-32'; s['quota'] = {'metric': 'M3_CPUS', 'info_id': 'M3-CPUS-per-project-region', 'dimensions': {'region': c['region']}}
        s['expected_guest'] = {'architecture': 'x86_64', 'vcpus': 32, 'memory_gib_min': 975, 'memory_gib_max': 976}
        c['price_snapshot']['machines'][s['machine_type']] = {'vcpus': 32, 'memory_gib': 976}
        self.assertEqual(cc.stage_capabilities(c, s)['disk_interface'], 'NVME')
    def test_exact_standard_c4d4_profile_remains_nvme(self):
        c, s = profile(); s['machine_type'] = 'c4d-highmem-4'; s['disk_type'] = 'hyperdisk-balanced'
        s['quota'] = {'metric': 'CPUS_PER_VM_FAMILY', 'info_id': 'CPUS-PER-VM-FAMILY-per-project-region', 'dimensions': {'region': c['region'], 'vm_family': 'C4D'}}
        s['expected_guest']['memory_gib_max'] = 31; s['expected_guest']['memory_gib_min'] = 30
        c['price_snapshot']['machines'][s['machine_type']] = {'vcpus': 4, 'memory_gib': 31, 'architecture': 'X86_64', 'zone': 'us-central1-a'}
        self.assertEqual(cc.stage_capabilities(c, s)['disk_interface'], 'NVME')


class QuotaControls(unittest.TestCase):
    def test_n2_effective_grant_usage_global_not_other_pool(self):
        c, s = profile(); g = FakeGcloud(quota_records()); cc.preflight_quotas(g, c, s)
        self.assertEqual(len(g.calls), 3); self.assertIn('N2-CPUS-per-project-region', g.calls[0])
        self.assertIn('regions', g.calls[1]); self.assertIn('project-info', g.calls[2])
    def test_missing_wrong_or_ambiguous_grant(self):
        for variant in range(4):
            records = quota_records()
            if variant == 0: records[0]['quotaId'] = 'M3-CPUS-per-project-region'
            elif variant == 1: records[0]['metric'] = 'compute.googleapis.com/preemptible_cpus'
            elif variant == 2: records[0]['dimensionsInfos'][0]['dimensions']['region'] = 'other-region'
            else: records[0]['dimensionsInfos'].append(copy.deepcopy(records[0]['dimensionsInfos'][0]))
            c, s = profile()
            with self.subTest(variant=variant), self.assertRaises(cc.GuardError): cc.preflight_quotas(FakeGcloud(records), c, s)
    def test_effective_grant_not_preference(self):
        c, s = profile(); records = quota_records(); records[0]['dimensionsInfos'][0]['details']['value'] = 0
        records[0]['preferredValue'] = 200
        with self.assertRaises(cc.GuardError): cc.preflight_quotas(FakeGcloud(records), c, s)
    def test_regional_headroom_wrong_region_and_negative_usage(self):
        for key, val in [('usage', 197), ('usage', -1), ('limit', -1)]:
            c, s = profile(); records = quota_records(); records[1]['quotas'][0][key] = val
            with self.subTest(key=key, val=val), self.assertRaises(cc.GuardError): cc.preflight_quotas(FakeGcloud(records), c, s)
        c, s = profile(); records = quota_records(); records[1]['name'] = 'other-region'
        with self.assertRaises(cc.GuardError): cc.preflight_quotas(FakeGcloud(records), c, s)
    def test_duplicate_regional_pool(self):
        c, s = profile(); records = quota_records(); records[1]['quotas'].append(copy.deepcopy(records[1]['quotas'][0]))
        with self.assertRaises(cc.GuardError): cc.preflight_quotas(FakeGcloud(records), c, s)
    def test_global_headroom_and_negative_usage(self):
        for usage in (381, -1):
            c, s = profile(); records = quota_records(); records[2]['quotas'][0]['usage'] = usage
            with self.subTest(usage=usage), self.assertRaises(cc.GuardError): cc.preflight_quotas(FakeGcloud(records), c, s)
    def test_actual_read_failure_refuses(self):
        for i in range(3):
            c, s = profile(); records = quota_records(); records[i] = {'exit': 1, 'stdout': '{}'}
            with self.subTest(i=i), self.assertRaises(cc.GuardError): cc.preflight_quotas(FakeGcloud(records), c, s)


class ProviderControls(unittest.TestCase):
    def test_create_exact_standard_scsi_no_performance_or_credentials(self):
        c, s = profile(); e, vm = provider_fixture(c, s)
        with tempfile.TemporaryDirectory() as folder:
            startup = Path(folder) / 'startup.sh'; startup.write_bytes(b'#!/bin/sh\nexit 0\n')
            cmd = cc.create_compute_only_command(c, s, e, c['zones'][0], startup, startup_sha256=hashlib.sha256(startup.read_bytes()).hexdigest())
            for argument in ('--machine-type=n2-highmem-4', '--provisioning-model=STANDARD', '--boot-disk-interface=SCSI', '--boot-disk-type=pd-balanced', '--no-service-account', '--no-scopes'):
                self.assertIn(argument, cmd)
            self.assertFalse(any('provisioned-' in arg for arg in cmd))
    def test_create_wrong_zone_or_expiry_refuses(self):
        c, s = profile(); e, vm = provider_fixture(c, s)
        with self.assertRaises(cc.GuardError): cc._create_command(c, s, e, 'us-central1-b', Path('unused'), compute_only=True)
        e['termination_utc'] = '2030-01-03T00:00:00Z'
        with self.assertRaises(cc.GuardError): cc._create_command(c, s, e, c['zones'][0], Path('unused'), compute_only=True)
    def test_readback_exact_interface_and_mode(self):
        c, s = profile(); e, vm = provider_fixture(c, s)
        self.assertIs(cc.validate_compute_only_instance(c, s, e, vm), vm)
        for key in ('interface', 'mode', 'zone', 'credentials'):
            altered = copy.deepcopy(vm)
            if key == 'interface': altered['disks'][0]['interface'] = 'NVME'
            elif key == 'mode': altered['scheduling']['provisioningModel'] = 'SPOT'
            elif key == 'zone': altered['zone'] = 'zones/us-central1-b'
            else: altered['serviceAccounts'] = [{'email': 'unexpected'}]
            with self.subTest(key=key), self.assertRaises(cc.GuardError): cc.validate_compute_only_instance(c, s, e, altered)
    def test_disk_image_type_size_or_extra_performance_refuses(self):
        c, s = profile(); e, vm = provider_fixture(c, s)
        disk = {'sourceImageId': c['image_id'], 'sourceImage': c['image'], 'type': 'zones/us-central1-a/diskTypes/pd-balanced', 'sizeGb': '200'}
        cc.verify_boot_disk(FakeGcloud([disk]), c, s, vm)
        for key, val in [('sourceImageId', 'other'), ('sourceImage', 'other'), ('type', 'hyperdisk-balanced'), ('sizeGb', '199'), ('provisionedIops', 3000), ('provisionedThroughput', 140)]:
            altered = {**disk, key: val}
            with self.subTest(key=key), self.assertRaises(cc.GuardError): cc.verify_boot_disk(FakeGcloud([altered]), c, s, vm)


class GuestControls(unittest.TestCase):
    def test_actual_n2_scsi_guest(self):
        runtime, actual = observations(); self.assertTrue(cw.verify_guest(runtime, actual)['hardware_frozen_verified'])
    def test_actual_c4d4_nvme_guest_is_now_supported(self):
        runtime, actual = observations('c4d-highmem-4'); self.assertTrue(cw.verify_guest(runtime, actual)['hardware_frozen_verified'])
    def test_catalog_exact_ram_c4d4_and_n2(self):
        for machine in ('c4d-highmem-4', 'n2-highmem-4'):
            runtime, actual = observations(machine); runtime['expected_guest']['memory_gib_max'] += 1
            with self.subTest(machine=machine), self.assertRaises(cc.GuardError): cw.verify_guest(runtime, actual)
    def test_metadata_scsi_does_not_replace_sysfs_measurement(self):
        runtime, actual = observations(); actual['guest_root_block']['interface'] = 'unverified'
        with self.assertRaises(cc.GuardError): cw.verify_guest(runtime, actual)
    def test_actual_hardware_mismatch(self):
        for key, val in [('guest_architecture', 'aarch64'), ('guest_cpu_count', True), ('guest_cpu_count', 8), ('guest_cpu_affinity', [0, 1, 2]), ('guest_cpu_affinity', [0, 1, 2, 2]), ('guest_memory_total_bytes', 30 * 2**30), ('guest_boot_disk_metadata_interface', 'NVME')]:
            runtime, actual = observations(); actual[key] = val
            with self.subTest(key=key, val=val), self.assertRaises(cc.GuardError): cw.verify_guest(runtime, actual)
    def test_actual_purchase_or_scheduling_mismatch(self):
        for key, val in [('preemptible', 'TRUE'), ('automatic_restart', 'TRUE'), ('on_host_maintenance', 'MIGRATE')]:
            runtime, actual = observations(); actual['guest_scheduling'][key] = val
            with self.subTest(key=key), self.assertRaises(cc.GuardError): cw.verify_guest(runtime, actual)
    def test_legacy_spot_receipt_unchanged(self):
        runtime = {'machine_type': 'c4d-highmem-96'}
        actual = {'machine_type': 'c4d-highmem-96', 'guest_scheduling': {'preemptible': 'TRUE'}}
        self.assertEqual(cw.verify_guest(runtime, actual), {'version': 1, 'purchase_mode': 'SPOT', 'machine_type': 'c4d-highmem-96', 'mode_evidence': 'instance scheduling/preemptible; exact provisioningModel requires coordinator readback', 'hardware_frozen_verified': False})
    def test_doctor_identity_zone_and_provider_binding(self):
        runtime, actual = observations(); expected = {'instance_id': '123', 'zone': 'us-central1-a', 'boot_disk_device_name': 'root', 'guest_os_release_sha256': 'a' * 64, 'boot_disk_bytes_min': 100}
        runtime.update(expected_identity=expected, provider_identity_sha256='b' * 64)
        actual.update({k: v for k, v in expected.items() if k != 'boot_disk_bytes_min'}); actual['guest_root_filesystem_bytes'] = 200
        with patch.object(only, 'verify_guest', cw.verify_guest):
            self.assertEqual(only.verify_identity(runtime, actual)['provider_identity_sha256'], 'b' * 64)
            for key, val in [('zone', 'us-central1-b'), ('instance_id', '124'), ('guest_os_release_sha256', 'c' * 64), ('guest_root_filesystem_bytes', 99)]:
                altered = {**actual, key: val}
                with self.subTest(key=key), self.assertRaises(cc.GuardError): only.verify_identity(runtime, altered)


class RootBlockControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name)
        self.dev = self.path / 'dev-block'; self.dev.mkdir(); self.root_device = os.makedev(8, 1)
    def tearDown(self): self.temp.cleanup()
    def node(self, name):
        path = self.path / name; path.mkdir(parents=True); return path
    def bind(self, path): (self.dev / '8:1').symlink_to(path)
    def test_scsi_partition_correlated_host_target_address(self):
        self.bind(self.node('devices/pci/host0/target0:0:1/0:0:1:0/block/sda/sda1'))
        self.assertEqual(cw.root_block_interface(self.dev, self.root_device), {'root_device': '8:1', 'interface': 'SCSI', 'nvme_namespaces': [], 'scsi_disks': ['sda']})
    def test_unrelated_scsi_ancestry_refuses(self):
        self.bind(self.node('devices/host1/target0:0:1/0:0:1:0/block/sda/sda1'))
        self.assertEqual(cw.root_block_interface(self.dev, self.root_device)['interface'], 'unverified')
    def test_sd_name_without_actual_scsi_ancestry_refuses(self):
        self.bind(self.node('devices/block/sda/sda1'))
        self.assertEqual(cw.root_block_interface(self.dev, self.root_device)['interface'], 'unverified')
    def test_nvme_original_exact_receipt(self):
        self.bind(self.node('devices/pci/nvme/nvme0/nvme0n1/nvme0n1p1'))
        self.assertEqual(cw.root_block_interface(self.dev, self.root_device), {'root_device': '8:1', 'interface': 'NVME', 'nvme_namespaces': ['nvme0n1']})
    def test_device_mapper_all_scsi_slaves(self):
        root = self.node('devices/virtual/block/dm-0'); slaves = root / 'slaves'; slaves.mkdir()
        for index, disk in enumerate(('sda', 'sdb')):
            p = self.node(f'devices/pci/host0/target0:0:{index}/0:0:{index}:0/block/{disk}')
            (slaves / disk).symlink_to(p)
        self.bind(root)
        self.assertEqual(cw.root_block_interface(self.dev, self.root_device)['scsi_disks'], ['sda', 'sdb'])
    def test_mixed_mapper_refuses(self):
        root = self.node('devices/virtual/block/dm-0'); slaves = root / 'slaves'; slaves.mkdir()
        (slaves / 'sda').symlink_to(self.node('devices/host0/target0:0:0/0:0:0:0/block/sda'))
        (slaves / 'nvme0n1').symlink_to(self.node('devices/nvme0/nvme0n1'))
        self.bind(root); self.assertEqual(cw.root_block_interface(self.dev, self.root_device)['interface'], 'unverified')
    def test_cyclic_mapper_refuses(self):
        root = self.node('devices/virtual/block/dm-0'); slaves = root / 'slaves'; slaves.mkdir(); (slaves / 'dm-0').symlink_to(root)
        self.bind(root); self.assertEqual(cw.root_block_interface(self.dev, self.root_device)['interface'], 'unverified')
    def test_missing_root_refuses(self):
        self.assertEqual(cw.root_block_interface(self.dev, self.root_device)['interface'], 'unverified')


if __name__ == '__main__': unittest.main()
