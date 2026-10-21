"""Bounded R11 archive transport after the owned writer has exited.

Reuses cloud_worker/cloud_archive. Transport receipts are not scientific,
recovery, off-GCP preservation, or resource-deletion acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'research_tools'))
sys.path.insert(0, str(ROOT))

from cloud_archive import CODEC, CHUNK_BYTES, safe_name, identity, lexical_path, require_capacity
from cloud_control import GuardError, digest, stamp, utc
from cloud_worker import (Store, snapshot, download_snapshot, wait_pipeline_group_absent,
                          unique_json_object, reserve_metadata, snapshot_locked, read_private_accounting)
from dams_sim.storage import atomic_json, canonical, file_digest
from research_tools.r11_retention import science_manifest

IMPLEMENTATION = ('research_tools/r11_archive.py', 'research_tools/cloud_archive.py',
                  'research_tools/cloud_control.py', 'research_tools/cloud_worker.py', 'cloud/r11-startup.sh')


def implementation_hashes(root=ROOT):
    return {name: file_digest(Path(root)/name) for name in IMPLEMENTATION}


def require_quiet(source, pgid, deadline):
    """No live SQLite/file is eligible, even if it happens to be momentarily idle."""
    evidence = wait_pipeline_group_absent(pgid, deadline, max_wait_seconds=0)
    if shutil.which('lsof') is None:
        raise GuardError('lsof is required to refuse open research files')
    result = subprocess.run(['lsof', '-nP', '-t', '+D', str(source)], capture_output=True, timeout=30)
    if result.returncode != 1 or result.stdout.strip() or result.stderr.strip():
        raise GuardError('source closure could not be established; preserve disk and do not upload')
    return evidence


def source_bundle(source, code_root=ROOT):
    """Keep exact bound source bytes with the raw evidence; no private config."""
    plan = json.loads((source/'plan.json').read_text())
    bound = plan.get('scientific_dependencies')
    if bound != science_manifest(code_root):
        raise GuardError('unbound or changed source plan; legacy archive migration is not implemented')
    files = {**bound['files_sha256'], **bound['execution_files_sha256'], **implementation_hashes(code_root)}
    bundle = source/'archive-source'
    bundle.mkdir(exist_ok=True)
    for name, expected in sorted(files.items()):
        safe_name(name)
        original = lexical_path(Path(code_root)/name)
        if original.is_symlink() or file_digest(original) != expected:
            raise GuardError('bound source changed before archival')
        destination = lexical_path(bundle/name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_symlink() or file_digest(destination) != expected:
                raise GuardError('existing source bundle differs; preserve it without overwrite')
        else:
            with original.open('rb') as src, destination.open('xb') as dst:
                shutil.copyfileobj(src, dst, CHUNK_BYTES)
                dst.flush(); os.fsync(dst.fileno())
            if file_digest(destination) != expected:
                raise GuardError('source bundle byte verification failed')
    record = {'schema': 'r11-archived-source-v1', 'plan_sha256': file_digest(source/'plan.json'),
              'files_sha256': files, 'archive_implementation_sha256': implementation_hashes(code_root)}
    path = bundle/'source-manifest.json'
    if path.exists():
        if json.loads(path.read_text()) != record:
            raise GuardError('existing source manifest differs')
    else:
        atomic_json(path, record)
    return record


def regular_roster(source):
    """Refuse every non-regular/non-directory entry before the legacy codec."""
    if not stat.S_ISDIR(source.lstat().st_mode):
        raise GuardError('archive source must be a regular directory')
    result = {}
    for path in source.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            result[path.relative_to(source).as_posix()] = path
        elif not stat.S_ISDIR(mode):
            raise GuardError('symlink or special file in archive source; no omission permitted')
    return result


def verify_tree(source, descriptor):
    """Recheck the entire closed source after transport; never accept a subset."""
    paths = regular_roster(source)
    if set(paths) != set(descriptor['files']):
        raise GuardError('whole source roster changed after transport')
    identities = {}
    for name, path in paths.items():
        before = identity(path)
        expected = descriptor['files'][name]
        if before[2] != expected['bytes'] or file_digest(path) != expected['sha256'] or identity(path) != before:
            raise GuardError('source bytes changed after transport')
        identities[name] = before
    if set(regular_roster(source)) != set(paths) or any(identity(paths[n]) != v for n, v in identities.items()):
        raise GuardError('source changed during final inventory verification')


class GenerationStore(Store):
    """Every accepted object is re-read at one explicit generation through EOF."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bindings = {}

    def verify_generation(self, key, expected_sha, length):
        safe_name(key)
        with self.request(self.url(key)) as response:
            declared = response.headers.get('Content-Length')
            if declared is not None and not 0 <= int(declared) <= 65536:
                raise GuardError('object metadata length exceeds its bound')
            allowance = 65537 if declared is None else int(declared)+1
            self.charge_transfer(allowance)
            raw = response.read(allowance)
        if declared is not None and len(raw) != int(declared):
            raise GuardError('object metadata response length differs')
        if len(raw) > 65536:
            raise GuardError('object metadata exceeds its bound')
        metadata = json.loads(raw, object_pairs_hook=unique_json_object)
        generation = metadata.get('generation')
        if (not isinstance(generation, str) or re.fullmatch('[1-9][0-9]*', generation) is None
                or metadata.get('name') != self.prefix+'/'+key or metadata.get('bucket') != self.bucket
                or str(metadata.get('size')) != str(length)):
            raise GuardError('remote object identity/generation/length differs')
        binding = {'bucket': self.bucket, 'object': self.prefix+'/'+key, 'generation': generation,
                   'sha256': expected_sha, 'bytes': length}
        self.read_generation(binding)
        self.bindings[key] = binding
        return binding

    def read_generation(self, binding, destination=None, *, capture=False):
        if (binding.get('bucket') != self.bucket or not binding.get('object', '').startswith(self.prefix+'/')
                or re.fullmatch('[1-9][0-9]*', str(binding.get('generation', ''))) is None
                or re.fullmatch('[0-9a-f]{64}', str(binding.get('sha256', ''))) is None
                or type(binding.get('bytes')) is not int or binding['bytes'] < 0):
            raise GuardError('invalid fixed-generation object binding')
        key = binding['object'][len(self.prefix)+1:]
        safe_name(key)
        if capture and binding['bytes'] > 32*1024**2:
            raise GuardError('captured descriptor exceeds metadata bound')
        url = self.url(key, media=True)+'&'+urllib.parse.urlencode({
            'generation': binding['generation'], 'ifGenerationMatch': binding['generation']})
        h, count, captured = hashlib.sha256(), 0, []
        target = None if destination is None else Path(destination).open('xb')
        try:
            self.charge_transfer(binding['bytes'])
            with self.request(url) as response:
                if str(response.headers.get('Content-Length')) != str(binding['bytes']):
                    raise GuardError('fixed-generation response length differs')
                while block := response.read(min(CHUNK_BYTES, binding['bytes']-count+1)):
                    self.check_deadline()
                    count += len(block)
                    if count > binding['bytes']:
                        raise GuardError('fixed-generation response exceeds bound')
                    h.update(block)
                    if capture:
                        captured.append(block)
                    if target is not None:
                        target.write(block)
            if count != binding['bytes'] or h.hexdigest() != binding['sha256']:
                raise GuardError('fixed-generation EOF/hash differs')
            if target is not None:
                target.flush(); os.fsync(target.fileno())
            return b''.join(captured) if capture else None
        except BaseException:
            if target is not None:
                target.close(); Path(destination).unlink(missing_ok=True)
            raise
        finally:
            if target is not None:
                target.close()

    def _verify_file(self, key, expected_sha, length, context):
        context['phase'] = 'fixed-generation-verify'
        self.verify_generation(key, expected_sha, length)


def config_file(path):
    path = lexical_path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise GuardError('archive config must be a private regular mode-0600 file')
    if not path.is_file() or path.stat().st_size > 65536:
        raise GuardError('archive config exceeds its bound')
    value = json.loads(path.read_text(), object_pairs_hook=unique_json_object)
    if value.get('schema') != 'r11-root-archive-admission-v1':
        raise GuardError('explicit Root archive admission is required')
    if value.get('archive_implementation_sha256') != implementation_hashes():
        raise GuardError('archive implementation differs from its fixed admission')
    if not isinstance(value.get('bucket'), str) or re.fullmatch('[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]', value['bucket']) is None:
        raise GuardError('invalid admitted bucket')
    safe_name(value['prefix'])
    utc(value['deadline_utc'])
    if datetime.now(timezone.utc) >= utc(value['deadline_utc']):
        raise GuardError('absolute archive admission deadline expired')
    for key in ('max_requests', 'max_download_bytes', 'max_storage_bytes', 'max_raw_bytes',
                'max_files', 'minimum_free_bytes'):
        if type(value.get(key)) is not int or value[key] <= 0:
            raise GuardError('missing finite positive archive allowance')
    admitted_state(value)
    return value


def admitted_state(config):
    """Missing prior accounting is a refusal, never a fresh allowance."""
    if not isinstance(config.get('state_directory'), str) or not Path(config['state_directory']).is_absolute():
        raise GuardError('fixed persistent accounting directory is required')
    state = lexical_path(config['state_directory'])
    for name, counter, cap in (('requests.json', 'requests_upper', 'max_requests'),
                               ('downloads.json', 'bytes_upper', 'max_download_bytes'),
                               ('upload-state.json', 'bytes', 'max_storage_bytes')):
        path = lexical_path(state/name)
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077 or path.stat().st_size > 32*1024**2:
            raise GuardError('original private accounting ledger is missing or unsafe')
        record = json.loads(path.read_text())
        if type(record.get(counter)) is not int or not 0 <= record[counter] <= config[cap]:
            raise GuardError('original archive allowance is invalid or exhausted')
    return state


def archive_closed(source, config, pgid, state):
    """Transport all original files, including unvalidated interrupted SQLite."""
    source, state = lexical_path(source), lexical_path(state)
    if admitted_state(config) != state:
        raise GuardError('accounting path differs from the original admission')
    if datetime.now(timezone.utc) >= utc(config['deadline_utc']):
        raise GuardError('absolute archive deadline expired')
    if state == source or state.is_relative_to(source) or source.is_relative_to(state):
        raise GuardError('archive accounting state must be separate from research evidence')
    require_capacity(state, 2*CHUNK_BYTES, config['minimum_free_bytes'])
    regular_roster(source)
    closure = require_quiet(source, pgid, config['deadline_utc'])
    source_record = source_bundle(source)
    require_quiet(source, pgid, config['deadline_utc'])
    regular_roster(source)
    store = GenerationStore(config['bucket'], config['prefix'], deadline=config['deadline_utc'],
                            request_state=state/'requests.json', max_requests=config['max_requests'],
                            transfer_state=state/'downloads.json', max_transfer_bytes=config['max_download_bytes'],
                            require_existing_state=True)
    sid = snapshot(source, store, state/'upload-state.json', config['max_storage_bytes'],
                   include_reaped_working=True, preserve_interrupted_evidence=True,
                   owned_pipeline_group_id=pgid, owned_pipeline_deadline=config['deadline_utc'],
                   transport_codec=CODEC, archive_max_raw_bytes=config['max_raw_bytes'],
                   archive_min_free_bytes=config['minimum_free_bytes'], archive_max_files=config['max_files'],
                   require_existing_state=True)
    descriptor_key = 'snapshots/'+sid+'.json'
    if descriptor_key not in store.bindings:
        raise GuardError('snapshot descriptor lacks a fixed-generation receipt')
    descriptor = json.loads(store.read_generation(store.bindings[descriptor_key], capture=True))
    if not isinstance(descriptor, dict) or digest(descriptor) != sid:
        raise GuardError('immutable snapshot descriptor differs from its identity')
    # Reused upload-state entries are not trusted as current generation custody.
    objects = {}
    for item in descriptor['files'].values():
        for chunk in item['archive']['chunks']:
            objects['chunks/'+CODEC+'/'+chunk['encoded_sha256']] = (chunk['encoded_sha256'], chunk['encoded_bytes'])
    for key, (sha, length) in sorted(objects.items()):
        if key not in store.bindings:
            store.verify_generation(key, sha, length)
    final_closure = require_quiet(source, pgid, config['deadline_utc'])
    verify_tree(source, descriptor)
    store.check_deadline()
    receipt = {'schema': 'r11-generation-archive-transport-v1', 'utc': stamp(),
               'snapshot': sid, 'source': source_record, 'closure': closure, 'final_closure': final_closure,
               'descriptor': descriptor, 'objects': {k: store.bindings[k] for k in sorted(objects)},
               'descriptor_object': store.bindings[descriptor_key],
               'archive_accepted': False, 'complete_preservation_accepted': False,
               'pending': ['full state/recovery validation', 'independent complete off-GCP preservation',
                           'Root named custody and lifecycle acceptance'],
               'invalidation': 'missing/replaced generation, changed source/implementation, failed EOF/hash or changed custody'}
    if len(canonical(receipt)) > 32*1024**2:
        raise GuardError('complete generation receipt exceeds its metadata bound; no truncation permitted')
    # Publish the receipt body immutably too. Its own generation is bound by the
    # separate local index, avoiding a self-referential receipt/hash.
    receipt_key = 'receipts/'+digest(receipt)+'.json'
    with snapshot_locked((state/'upload-state.json').with_suffix('.lock')):
        accounting = read_private_accounting(state/'upload-state.json')
        reserve_metadata(accounting, receipt_key, receipt, config['max_storage_bytes'])
        atomic_json(state/'upload-state.json', accounting)
    store.put_json(receipt_key, receipt)
    receipt['remote_receipt_object'] = store.bindings[receipt_key]
    # No done.json or deletion authority is issued.
    path = state/('generation-receipt-'+digest(receipt)+'.json')
    atomic_json(path, receipt)
    return path


def recover_checkpoint(path, destination):
    from dams_sim.model import Model
    path, destination = lexical_path(path), lexical_path(destination)
    if destination.exists():
        raise GuardError('zero-step recovery target must not exist')
    require_capacity(destination.parent, path.with_suffix('.sqlite').stat().st_size+2*CHUNK_BYTES, 2*1024**3)
    model = Model.restore_checkpoint(path, storage_dir=destination)
    try:
        receipt = {'schema': 'r11-zero-step-recovery-v1', 'checkpoint_sha256': file_digest(path),
                   'state_semantic_sha256': model.semantic_digest(), 'new_simulation_steps': 0,
                   'archive_accepted': False, 'complete_preservation_accepted': False}
        atomic_json(destination/'zero-step-recovery.json', receipt)
    finally:
        model._long.ledger.close()
    return receipt


def restore_mirror(receipt_path, objects, destination, *, max_raw_bytes, minimum_free_bytes):
    """Offline byte restoration; generation custody still needs independent proof.

    A separately downloaded object mirror uses GENERATION/SHA256 filenames. No
    current/latest pointer or unversioned network read is used by this operation.
    """
    receipt_path, objects = lexical_path(receipt_path), lexical_path(objects)
    destination = lexical_path(destination)
    if destination.exists() or destination.is_symlink():
        raise GuardError('offline restoration requires a new destination')
    if receipt_path.stat().st_size > 32*1024**2:
        raise GuardError('archive receipt exceeds its metadata bound')
    receipt = json.loads(receipt_path.read_text())
    descriptor, sid = receipt['descriptor'], receipt['snapshot']
    if receipt.get('schema') != 'r11-generation-archive-transport-v1' or digest(descriptor) != sid:
        raise GuardError('archive transport receipt/descriptor identity differs')
    descriptor_binding = receipt['descriptor_object']
    suffix = '/snapshots/'+sid+'.json'
    if (re.fullmatch('[1-9][0-9]*', str(descriptor_binding.get('generation', ''))) is None
            or not isinstance(descriptor_binding.get('object'), str)
            or not descriptor_binding['object'].endswith(suffix)
            or not isinstance(descriptor_binding.get('bucket'), str)):
        raise GuardError('descriptor has no fixed-generation identity')
    prefix = descriptor_binding['object'][:-len(suffix)]
    class Mirror:
        def get_json(self, key):
            if key != 'snapshots/'+sid+'.json':
                raise GuardError('unexpected mirror descriptor')
            binding = receipt['descriptor_object']
            raw = (json.dumps(descriptor, sort_keys=True, indent=2)+'\n').encode()
            if len(raw) != binding['bytes'] or hashlib.sha256(raw).hexdigest() != binding['sha256']:
                raise GuardError('descriptor bytes differ from their fixed-generation binding')
            return descriptor

        def download(self, key, destination, *, max_bytes=None):
            binding = receipt['objects'][key]
            generation, sha = str(binding['generation']), binding['sha256']
            if (re.fullmatch('[1-9][0-9]*', generation) is None or re.fullmatch('[0-9a-f]{64}', sha) is None
                    or binding.get('bucket') != descriptor_binding['bucket']
                    or binding.get('object') != prefix+'/'+key):
                raise GuardError('unsafe mirror generation/hash')
            original = lexical_path(objects/generation/sha)
            before = identity(original)
            if before[2] != binding['bytes'] or max_bytes is None or before[2] > max_bytes or file_digest(original) != sha:
                raise GuardError('mirror encoded object differs from its trusted allowance')
            with original.open('rb') as source, Path(destination).open('xb') as target:
                shutil.copyfileobj(source, target, CHUNK_BYTES)
            if identity(original) != before or file_digest(Path(destination)) != sha:
                raise GuardError('mirror changed during restore')
            return sha
    result = download_snapshot(Mirror(), sid, destination, transport_codec=CODEC,
                               archive_max_raw_bytes=max_raw_bytes, archive_min_free_bytes=minimum_free_bytes,
                               allow_interrupted_evidence=True)
    return {'snapshot': sid, 'restore_result': result, 'scope': 'offline exact-byte restoration only',
            'archive_accepted': False, 'complete_preservation_accepted': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('upload-closed')
    a.add_argument('--source', required=True, type=Path)
    a.add_argument('--private-config', required=True, type=Path)
    a.add_argument('--owned-pgid', required=True, type=int)
    a.add_argument('--state', required=True, type=Path)
    r = sub.add_parser('recover-checkpoint')
    r.add_argument('checkpoint', type=Path)
    r.add_argument('--destination', required=True, type=Path)
    m = sub.add_parser('restore-mirror')
    m.add_argument('receipt', type=Path)
    m.add_argument('--objects', required=True, type=Path)
    m.add_argument('--destination', required=True, type=Path)
    m.add_argument('--max-raw-bytes', required=True, type=int)
    m.add_argument('--minimum-free-bytes', type=int, default=2*1024**3)
    ns = p.parse_args()
    try:
        if ns.command == 'upload-closed':
            receipt = archive_closed(ns.source, config_file(ns.private_config), ns.owned_pgid, ns.state)
            print(json.dumps({'transport_receipt': str(receipt), 'archive_accepted': False}))
        elif ns.command == 'recover-checkpoint':
            print(json.dumps(recover_checkpoint(ns.checkpoint, ns.destination)))
        else:
            print(json.dumps(restore_mirror(ns.receipt, ns.objects, ns.destination,
                                           max_raw_bytes=ns.max_raw_bytes,
                                           minimum_free_bytes=ns.minimum_free_bytes)))
    except Exception as error:
        # Network exceptions may contain tokens/session URLs; never stringify.
        print(json.dumps({'status': 'refused-or-incomplete', 'error_class': type(error).__name__,
                          'preserve_original_disk': True, 'archive_accepted': False}), file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
