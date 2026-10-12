"""Bounded metadata observer outside the frozen scientific entry.

Preparation reuses SHA-pinned ``paired-result.json`` receipts produced AFTER the
original two-arm raw gate. It does not import that gate, construct a Model, read
SQLite, hash raw payloads, or independently validate scientific accounts.

Root may call synchronize() with a pinned grant and an injected API transport.
The Git Data API sequence follows Root's existing finite non-force publisher:
publish.py SHA256 44cb969decd788f95896c7a365a76cc4784fd5d05e11b729a8875401bda86ebd.
There is deliberately no built-in GitHub/provider/auth transport or daemon.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat

CORE = 'b763b85e34164095911ae1cb85efca123b11205f305cd47e950cf9e0493e0b36'
DRIVER = '81edc3f731f39157ae562c0ec920416c8c1fe3b642d8938bdd3d99a48d37ecd3'
ENTRY = 'a99521f9fe6121633e8e49c3df8d6a6b8b20dd6db10944d131de4160ad5b1f90'
PUBLISHER = '44cb969decd788f95896c7a365a76cc4784fd5d05e11b729a8875401bda86ebd'
REPOS = ('rui-h-a/dams-simulation', 'rui-h-a/dams-thesis')
PHYSICAL_ROOTS = ((Path('/Volumes/DAMS-Workspace/projects/dams-simulation'), REPOS[0]),
                  (Path('/Users/akira/論文'), REPOS[1]),
                  (Path('/Volumes/DAMS-Workspace/projects/thesis'), REPOS[1]))
CURRENT = 'docs/research-log/current-n300-accepted-count.json'
BOUND = 1024 * 1024
JOURNAL_BOUND = 64 * BOUND
RESOURCE_FIELDS = {'max_wall_seconds', 'max_output_mb', 'max_rss_mb', 'max_events'}
ARMS = {'startup-existing': 'hierarchy', 'startup-founding': 'sublinear'}
ENDPOINTS = {'work_per_present_member_workday', 'decision_regret_per_decision',
             'net_resource_gain_per_present_member_workday', 'closed_by_common_end'}


class Refused(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Refused(reason)


def canonical(value):
    # The original study/config codec escapes non-ASCII JSON string values.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def hexsha(value, length=64):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{'+str(length)+'}', value) is not None


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def nonfinite(_):
        raise Refused('nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def relative(name):
    require(isinstance(name, str) and name and '\\' not in name, 'invalid relative path')
    p = PurePosixPath(name)
    require(not p.is_absolute() and p.as_posix() == name
            and all(part not in ('.', '..') for part in p.parts), 'path escape or alias')
    return p.parts


def directory(path):
    """Open every absolute ancestor without following symlinks."""
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(str(path)), 'root must be absolute and lexical')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def parent_fd(root, name, *, create=False):
    parts = relative(name)
    fd = directory(root)
    try:
        for part in parts[:-1]:
            if create:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def stable(root, name, *, expected=None, optional=False, bound=BOUND):
    require(expected is None or hexsha(expected), 'invalid metadata SHA')
    try:
        parent, leaf = parent_fd(root, name)
    except FileNotFoundError:
        if optional: return None
        raise
    try:
        try: fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            if optional: return None
            raise
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= bound,
                    'metadata must be a bounded, single-link regular file')
            chunks = []; remaining = bound + 1
            while remaining:
                block = os.read(fd, min(65536, remaining))
                if not block: break
                chunks.append(block); remaining -= len(block)
            raw = b''.join(chunks)
            key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
            require(len(raw) == before.st_size and key(before) == key(os.fstat(fd))
                    == key(os.stat(leaf, dir_fd=parent, follow_symlinks=False)), 'metadata changed before EOF')
            require(expected is None or sha(raw) == expected, 'metadata SHA differs')
            return raw
        finally: os.close(fd)
    finally: os.close(parent)


def reference(root, ref):
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'reference shape differs')
    return stable(root, ref['path'], expected=ref['sha256'])


def scalar(value, *, undefined=False):
    if value is None:
        require(undefined, 'undefined effect refused')
        return None
    require(type(value) in (int, float) and math.isfinite(value), 'nonfinite/non-numeric effect')
    return value


def effects_checked(result, raw, *, policy, declared_endpoints):
    effects = result['effects']
    require(len(effects) == 4 and {e['endpoint'] for e in effects} == ENDPOINTS, 'endpoint roster differs')
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'), newline='')))
    require(len(rows) == 4 and {r['endpoint'] for r in rows} == ENDPOINTS, 'CSV endpoint roster differs')
    by_name = {r['endpoint']: r for r in rows}; kept = []
    for effect in effects:
        require(effect['endpoint'] in declared_endpoints
                and [effect['target_halfwidth'], effect['unit']] == declared_endpoints[effect['endpoint']],
                'declared endpoint precision/unit differs')
        require(effect['world'] == result['world'] and effect['stage'] == result['stage']
                and effect['initial_n'] == 300, 'effect assignment differs')
        value = scalar(effect['effect'], undefined=policy == 'retain')
        denoms = [scalar(effect[k], undefined=policy == 'retain')
                  for k in ('reference_denominator', 'treatment_denominator')]
        require(all(d is None or d >= 0 for d in denoms), 'negative exposure')
        require(value is None or all(d is not None and d > 0 for d in denoms), 'defined effect lacks exposure')
        require(value is not None or any(d is None or d == 0 for d in denoms), 'undefined effect lacks undefined exposure')
        row = by_name[effect['endpoint']]
        require(row['world'] == str(result['world']) and row['stage'] == result['stage']
                and row['initial_n'] == '300', 'CSV assignment differs')
        for key in ('effect', 'reference_denominator', 'treatment_denominator', 'target_halfwidth'):
            actual = None if row[key] == '' else scalar(float(row[key]))
            require(actual == effect[key], 'CSV effect or exposure differs')
        require(row['unit'] == effect['unit'], 'CSV unit differs')
        scalar(effect['target_halfwidth'])
        kept.append({**effect, 'effect_status': 'defined' if value is not None else 'undefined-exposure'})
    return kept


def inspect(input_path, input_sha256, *, physical_preimages=None, now=None):
    """Read only listed metadata and exact selected completed attempts."""
    input_path = Path(input_path)
    raw = stable(input_path.parent, input_path.name, expected=input_sha256)
    request = parse(raw)
    require(set(request) == {'schema', 'study_root', 'study', 'confirmation_roster', 'pairs',
                            'previous_confirmation_count', 'event_day', 'observed_utc', 'operation_prefix', 'undefined_policy'},
            'observer input fields differ')
    require(request['schema'] == 'dams-accepted-result-observer-v1', 'observer input schema differs')
    root = Path(request['study_root']); fd = directory(root); os.close(fd)
    require(re.fullmatch('[a-z0-9][a-z0-9-]{0,47}', request['operation_prefix']) is not None, 'invalid Root operation prefix')
    require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', request['event_day']) is not None, 'invalid event day')
    datetime.strptime(request['event_day'], '%Y-%m-%d')
    observed = utc(request['observed_utc'])
    require(observed <= (now or (lambda:datetime.now(timezone.utc)))().astimezone(timezone.utc), 'observed UTC is in the future')
    require(request['undefined_policy'] in ('retain', 'reject'), 'undefined policy must be explicit')
    previous = request['previous_confirmation_count']
    require(type(previous) is int and 0 <= previous <= 66, 'invalid previous accepted prefix')
    study = parse(reference(root, request['study']))
    require(study['source_sha256'] == CORE and study['pipeline_driver_sha256'] == DRIVER
            and study['entry_sha256'] == ENTRY and hexsha(study['runtime_sha256']), 'frozen source/runtime identity differs')
    declaration = study['declaration']
    require(declaration['schema'] == 'dams-small-enterprise-five-year-v1'
            and declaration['initial_population'] == 300 and declaration['seed'] == 20261008
            and declaration['days_including_settlement_tail'] == 1917
            and declaration['full_observation_years'] == 5 and declaration['arms'] == list(ARMS),
            'original N300 declaration differs')
    pilots = list(range(42000, 42004)); confirmations = list(range(52000, 52066))
    require(declaration['pilot_world_ids'] == pilots and declaration['pilot_worlds'] == 4
            and declaration['confirmation_world_start'] == 52000, 'pilot/confirmation assignment differs')
    roster = parse(reference(root, request['confirmation_roster']))
    require(roster['status'] == 'frozen' and roster['world_count'] == 66
            and roster['world_ids'] == confirmations and roster['pilot_worlds_excluded'] is True
            and roster['study_sha256'] == sha(canonical(study)), 'original confirmation roster differs')
    identity = {'source_sha256': CORE, 'pipeline_driver_sha256': DRIVER,
                'spec_sha256': sha(canonical(declaration))}
    declared_endpoints = {row[0]:row[1:] for row in declaration['primary_endpoints']}
    require(len(declaration['primary_endpoints']) == 4 and set(declared_endpoints) == ENDPOINTS,
            'original declared endpoint roster differs')
    original_pilot = {r['case_id']:r for r in study['pilot_inventory']}
    require(len(study['pilot_inventory']) == len(original_pilot) == 8, 'original pilot inventory differs')
    require(isinstance(request['pairs'], list) and len(request['pairs']) <= 70, 'pair receipt bound exceeded')
    accepted = {}; incomplete = []; seen = set(); read_bytes = len(raw)
    for refs in request['pairs']:
        require(set(refs) == {'result', 'inventory', 'effects'}, 'pair references differ')
        result_raw = reference(root, refs['result']); read_bytes += len(result_raw)
        result = parse(result_raw); world = result['world']; stage = result['stage']
        require(type(world) is int and ((stage == 'pilot' and world in pilots)
                or (stage == 'confirmation' and world in confirmations)), 'engineering/unassigned world refused')
        require(world not in seen, 'duplicate world receipt'); seen.add(world)
        pair = f'{stage}/world-{world}'
        require(refs['result']['path'] == pair+'/paired-result.json'
                and refs['inventory']['path'] == pair+'/inventory.json'
                and refs['effects']['path'] == pair+'/effects.csv', 'pair metadata path differs')
        require(result['status'] in ('full-paired-raw-validated', 'running', 'failed', 'stopped', 'interrupted', 'partial'),
                'unsupported receipt status')
        if result['status'] != 'full-paired-raw-validated':
            incomplete.append({'world': world, 'stage': stage, 'status': result['status'],
                               'receipt_sha256': sha(result_raw)})
            continue
        require(result['initial_n'] == 300 and result['days'] == 1917
                and result['observation_years'] == 5 and result['identity'] == identity
                and result['entry_sha256'] == ENTRY and result['producer_workers_absent'] is True,
                'original full-paired raw gate receipt identity differs')
        inventory_raw = reference(root, refs['inventory']); read_bytes += len(inventory_raw)
        inventory = parse(inventory_raw)
        require(inventory['identity'] == identity and inventory['entry_sha256'] == ENTRY, 'pair inventory identity differs')
        rows = inventory['rows']; cases = result['cases']
        require(len(rows) == len(cases) == 2 and {c['arm'] for c in cases} == set(ARMS)
                and len({c['case_id'] for c in cases}) == 2, 'complete two-arm roster differs')
        by_id = {r['case_id']: r for r in rows}
        require(len(by_id) == 2, 'duplicate inventory case')
        if stage == 'pilot':
            require(all(original_pilot.get(r['case_id']) == r for r in rows), 'original pilot config/inventory differs')
        public_cases = []
        for case in cases:
            cid = case['case_id']; require(hexsha(cid) and cid in by_id, 'selected case unassigned')
            row = by_id[cid]; config = row['config']; arm = case['arm']
            expected_case = sha(canonical({k:v for k,v in config.items() if k not in RESOURCE_FIELDS}))
            require(cid == expected_case and config['n'] == 300 and config['days'] == 1917
                    and config['seed'] == declaration['seed'] and config['world'] == world
                    and config['regime'] == ARMS[arm] and config['backend'] == 'central'
                    and row['tags']['arm_id'] == arm and row['tags']['world'] == world
                    and row['tags']['parent_case_id'] is None, 'selected scientific config differs')
            attempt = case['attempt']
            require(re.fullmatch(re.escape(pair+'/cases/'+cid)+r'/attempt-\d{3}', attempt) is not None,
                    'selected attempt outside assigned case')
            manifest_raw = stable(root, attempt+'/manifest.json', expected=case['manifest_sha256'])
            manifest = parse(manifest_raw)
            require(manifest['status'] == 'complete' and type(manifest['exit_code']) is int and manifest['exit_code'] == 0
                    and manifest['scientific_case_id'] == cid and manifest['config'] == config
                    and manifest['config_sha256'] == sha(canonical(config))
                    and manifest['source_sha256'] == CORE and manifest['pipeline_driver_sha256'] == DRIVER,
                    'selected completed manifest identity differs')
            summary_raw = stable(root, attempt+'/summary.json', expected=manifest['output_sha256']['summary.json'])
            summary = parse(summary_raw); read_bytes += len(manifest_raw)+len(summary_raw)
            require(summary == case['summary'] and summary['n'] == 300
                    and summary['world'] == world and summary['days_completed'] == 1917
                    and summary['regime'] == ARMS[arm] and summary['backend'] == 'central', 'summary identity differs')
            require(hexsha(case['state_semantic_sha256'])
                    and manifest['final_state_descriptor']['state_semantic_sha256'] == case['state_semantic_sha256'],
                    'retained final-state metadata differs')
            public_cases.append({'case_id': cid, 'arm': arm, 'selected_attempt': Path(attempt).name,
                                 'manifest_sha256': sha(manifest_raw), 'config_sha256': manifest['config_sha256'],
                                 'summary_sha256': sha(summary_raw), 'state_semantic_sha256': case['state_semantic_sha256']})
        effect_raw = reference(root, refs['effects']); read_bytes += len(effect_raw)
        effect_rows = effects_checked(result, effect_raw, policy=request['undefined_policy'], declared_endpoints=declared_endpoints)
        accepted[world] = {'world': world, 'stage': stage, 'paired_result_sha256': sha(result_raw),
                           'arms': public_cases, 'effects': effect_rows}
    require(read_bytes <= 64*BOUND, 'aggregate small metadata bound exceeded')
    require(all(w in accepted for w in pilots), 'complete four-pilot prefix required')
    complete = [w for w in confirmations if w in accepted]
    require(complete == confirmations[:len(complete)], 'gap in accepted confirmation prefix')
    require(len(complete) >= previous, 'accepted prefix cannot shrink')
    counts = {'accepted_pairs': 4+len(complete), 'assigned_pairs': 70, 'excluded_pilot': 4,
              'confirmation_complete': len(complete), 'confirmation_assigned': 66,
              'confirmation_remaining': 66-len(complete)}
    new = [accepted[w] for w in complete[previous:]]
    public = {'schema': 'dams-accepted-result-observer-event-v1',
              'operation_namespace_day': request['event_day'], 'observed_utc': request['observed_utc'],
              'N300': counts, 'previous_confirmation_complete': previous, 'new_verified_pairs': new,
              'retained_incomplete_receipts': incomplete,
              'frozen_science': {**identity, 'entry_sha256': ENTRY, 'runtime_sha256': study['runtime_sha256'],
                                 'study_file_sha256': request['study']['sha256'],
                                 'confirmation_roster_file_sha256': request['confirmation_roster']['sha256']},
              'verification_scope': 'SHA-pinned original paired raw-gate receipt reuse and small metadata checks',
              'independent_full_raw_verification_performed': False,
              'newly_accepted_formal_worlds': len(new), 'observer_Model_calls': 0, 'observer_generated_worlds': 0,
              'provider_calls': 0, 'whole_goal_complete': False}
    event_id = sha(canonical(public))
    event_path = f"docs/research-log/{request['event_day']}/{request['operation_prefix']}-{event_id}.json"
    current = {'schema': 'dams-current-n300-accepted-count-v1', **counts, 'last_event_sha256': event_id,
               'operation_namespace_day':request['event_day'], 'observed_utc':request['observed_utc'],
               'independent_full_raw_verification_performed': False, 'whole_goal_complete': False}
    preimages = {} if physical_preimages is None else dict(physical_preimages)
    require(physical_preimages is None or set(preimages) == {str(p) for p,_ in PHYSICAL_ROOTS}, 'physical preimage roster differs')
    if physical_preimages is None:
        for physical, _ in PHYSICAL_ROOTS:
            old = stable(physical, CURRENT, optional=True)
            preimages[str(physical)] = None if old is None else sha(old)
            if old is not None:
                require(parse(old)['confirmation_complete'] == previous, 'physical current accepted prefix differs')
    return {'schema': 'dams-accepted-result-observer-preparation-v1', 'input_path': str(input_path),
            'input_sha256': sha(raw), 'operation_prefix': request['operation_prefix'], 'day': request['event_day'],
            'event_id': event_id, 'publisher_sha256': PUBLISHER, 'public_event': public,
            'files': {event_path: public, CURRENT: current}, 'physical_preimages': preimages,
            'metadata_bytes_read': read_bytes, 'actual_synchronization_performed': False}


def write_metadata(root, name, raw, *, previous=None, replace=False):
    require(len(raw) <= BOUND, 'output metadata bound exceeded')
    parent, leaf = parent_fd(root, name, create=True)
    try:
        old = stable(root, name, optional=True)
        if old == raw: return
        require((old is None and previous is None) or (replace and old is not None and sha(old) == previous),
                'immutable file or current metadata preimage differs')
        temporary = '.observer-'+sha(raw)+'.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as out:
                out.write(raw); out.flush(); os.fsync(fd)
        finally: os.close(fd)
        if old is None:
            # link is exclusive publication; no overwrite of an immutable event.
            os.link(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.unlink(temporary, dir_fd=parent)
        else:
            require(stable(root, name) == old, 'current metadata changed before publication')
            os.rename(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        require(stable(root, name) == raw, 'physical metadata readback differs')
    finally: os.close(parent)


def utc(value):
    require(isinstance(value, str), 'UTC deadline must be explicit')
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(date.tzinfo is not None and date.utcoffset().total_seconds() == 0, 'deadline must be UTC')
    return date


def native(info):
    return {'device':info.st_dev, 'inode':info.st_ino, 'uid':info.st_uid,
            'birthtime_ns':getattr(info,'st_birthtime_ns',None)}


def parent_authority(ref):
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'parent authority reference differs')
    path = Path(ref['path'])
    parent = parse(stable(path.parent, path.name, expected=ref['sha256']))
    require(set(parent) == {'schema', 'issued_by', 'state_root', 'operation_prefix', 'day',
                           'max_events', 'max_provider_calls', 'max_gh_api_calls',
                           'node_deadline_utc', 'global_deadline_utc', 'frozen_science'}, 'parent authority fields differ')
    require(parent['schema'] == 'dams-observer-root-parent-authority-v1' and parent['issued_by'] == 'ROOT'
            and type(parent['max_events']) is int and parent['max_events'] == 57
            and type(parent['max_provider_calls']) is int and parent['max_provider_calls'] == 0
            and type(parent['max_gh_api_calls']) is int and 1 <= parent['max_gh_api_calls'] <= 1824,
            'parent budget must retain provider0/events57/API<=1824')
    require(utc(parent['node_deadline_utc']) <= utc(parent['global_deadline_utc']), 'node cannot extend original global deadline')
    return parent


def enroll(state_root, parent_path, parent_sha256, *, now=None):
    """Root's ONE exclusive enrollment of a fresh owned empty metadata directory.

    The pinned parent fixes this native namespace, source assignment, counters,
    and absolute node/global deadlines. Failure leaves its evidence in place;
    neither this function nor sync removes/re-enrolls an existing scope.
    """
    root = Path(state_root); parent_path = Path(parent_path)
    ref = {'path':str(parent_path), 'sha256':parent_sha256}
    authority = parent_authority(ref)
    require(authority['state_root'] == str(root), 'parent authority belongs to another native namespace')
    clock = now or (lambda:datetime.now(timezone.utc))
    require(clock().astimezone(timezone.utc) < utc(authority['node_deadline_utc']), 'parent enrollment deadline expired')
    folder = directory(root); journal = None; issued = None
    try:
        info = os.fstat(folder)
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o022 and os.listdir(folder) == [],
                'Root enrollment requires a fresh owned empty directory; no retry namespace')
        journal = os.open('observer-journal.jsonl', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        os.mkdir('issued-begin', 0o700, dir_fd=folder)
        issued = os.open('issued-begin', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
        header = {'seq':0, 'previous':'0'*64, 'kind':'SCOPE', 'scope':authority, 'parent_authority':ref}
        raw = canonical(header)+b'\n'
        with os.fdopen(journal, 'wb', closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(journal)
        marker = {'schema':'dams-observer-root-enrollment-v1', 'state_root':str(root),
                  'state_native_identity':native(info), 'journal_native_identity':native(os.fstat(journal)),
                  'issued_native_identity':native(os.fstat(issued)), 'initial_header_bytes':len(raw),
                  'initial_header_sha256':sha(raw), 'parent_authority':ref,
                  'initial_observer_source_sha256':sha(Path(__file__).read_bytes())}
        marker_raw = canonical(marker)+b'\n'
        fd = os.open('enrollment.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(marker_raw); stream.flush(); os.fsync(fd)
        finally: os.close(fd)
        os.fsync(folder)
        return {'enrollment_sha256':sha(marker_raw), 'journal_floor':{'records':1,'head_sha256':sha(canonical(header))},
                'parent_authority_sha256':parent_sha256, 'provider_calls':0, 'GitHub_calls':0}
    finally:
        if issued is not None:os.close(issued)
        if journal is not None:os.close(journal)
        os.close(folder)


class Journal:
    """Exclusive, append-only issued-BEGIN counter; failures never refund calls."""
    def __init__(self, root, enrollment_sha256):
        self.root = Path(root); self.parent = directory(root); self.fd = None; self.issued = None
        try:
            marker = parse(stable(root, 'enrollment.json', expected=enrollment_sha256))
            require(marker['schema'] == 'dams-observer-root-enrollment-v1' and marker['state_root'] == str(self.root)
                    and marker['state_native_identity'] == native(os.fstat(self.parent)), 'enrolled native namespace differs')
            # Never O_CREAT: an enrolled missing/replaced journal is an error,
            # not permission to rebuild a zero counter.
            self.fd = os.open('observer-journal.jsonl', os.O_RDWR | os.O_NOFOLLOW, dir_fd=self.parent)
            self.issued = os.open('issued-begin', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            require(native(os.fstat(self.fd)) == marker['journal_native_identity']
                    and native(os.fstat(self.issued)) == marker['issued_native_identity'], 'original enrolled journal/issued inode replaced')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._load()
            require(self.rows and canonical(self.rows[0])+b'\n' == stable(root,'observer-journal.jsonl',bound=JOURNAL_BOUND)[:marker['initial_header_bytes']]
                    and sha(canonical(self.rows[0])+b'\n') == marker['initial_header_sha256']
                    and self.rows[0]['kind'] == 'SCOPE' and self.rows[0]['parent_authority'] == marker['parent_authority'],
                    'original initialization header missing/truncated/changed')
            self.authority = parent_authority(marker['parent_authority'])
            require(self.rows[0]['scope'] == self.authority, 'immutable parent scope differs')
            self.marker = marker
            begins = [r for r in self.rows if r['kind'] == 'API_BEGIN']
            require(len(begins) <= self.authority['max_gh_api_calls']
                    and [r['call'] for r in begins] == list(range(len(begins))), 'original issued counter sequence differs')
            names = sorted(os.listdir(self.issued))
            require(names == [f'{n:06d}.json' for n in range(len(begins))], 'issued BEGIN retained floor differs; journal truncated')
            for row, name in zip(begins, names):
                stable(root, 'issued-begin/'+name, expected=sha(canonical(row)+b'\n'))
        except BaseException:
            self.close(); raise

    def _load(self):
        s = os.fstat(self.fd)
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_size <= JOURNAL_BOUND, 'journal is not bounded owned metadata')
        self.rows = []; os.lseek(self.fd, 0, os.SEEK_SET)
        raw = b''
        while True:
            part = os.read(self.fd, 65536)
            if not part: break
            raw += part
        require(not raw or raw.endswith(b'\n'), 'journal has an incomplete issued record; Root reconciliation required')
        previous = '0'*64
        for line in raw.splitlines():
            row = parse(line)
            require(row['seq'] == len(self.rows) and row['previous'] == previous, 'journal order or chain differs')
            previous = sha(line); self.rows.append(row)
        self.head = previous
        self.expected_size = len(raw)
        os.lseek(self.fd, 0, os.SEEK_END)

    def guard_native(self):
        current = os.stat('observer-journal.jsonl',dir_fd=self.parent,follow_symlinks=False)
        require(native(current) == self.marker['journal_native_identity']
                and native(os.fstat(self.fd)) == self.marker['journal_native_identity']
                and current.st_size == os.fstat(self.fd).st_size == self.expected_size,
                'enrolled journal missing/truncated/replaced during operation')

    def append(self, kind, **fields):
        self.guard_native()
        row = {'seq': len(self.rows), 'previous': self.head, 'kind': kind, **fields}
        raw = canonical(row)
        require(len(raw) <= 2*BOUND, 'journal row bound exceeded')
        require(self.expected_size+len(raw)+1 <= JOURNAL_BOUND, 'cumulative journal bound exhausted before append')
        if kind == 'API_BEGIN':
            require(type(row['call']) is int and row['call'] == sum(r['kind'] == 'API_BEGIN' for r in self.rows), 'issued call counter differs')
            leaf = f"{row['call']:06d}.json"
            fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.issued)
            try:
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(raw+b'\n'); stream.flush(); os.fsync(fd)
            finally:os.close(fd)
            os.fsync(self.issued)
        view = memoryview(raw+b'\n')
        while view: view = view[os.write(self.fd, view):]
        os.fsync(self.fd); self.rows.append(row); self.head = sha(raw); self.expected_size += len(raw)+1
        return row

    def close(self):
        if self.issued is not None: os.close(self.issued); self.issued = None
        if self.fd is not None: os.close(self.fd); self.fd = None
        if self.parent is not None: os.close(self.parent); self.parent = None


def synchronize(plan_path, plan_sha256, grant_path, grant_sha256, state_root, transport, *, now=None):
    """Root-only explicit sync; transport.api follows the existing publisher ABI.

    Unknown mutating outcomes stop for Root reconciliation. Completed remote
    units are retained and never republished during a partial two-repo resume.
    """
    clock = now or (lambda: datetime.now(timezone.utc))
    plan_path = Path(plan_path); grant_path = Path(grant_path)
    plan = parse(stable(plan_path.parent, plan_path.name, expected=plan_sha256))
    grant = parse(stable(grant_path.parent, grant_path.name, expected=grant_sha256))
    require(set(grant) == {'schema', 'issued_by', 'plan_sha256', 'operation_prefix', 'day', 'deadline_utc',
                          'max_events', 'max_provider_calls', 'max_gh_api_calls', 'expected_parents', 'physical_roots',
                          'state_root', 'observer_source_sha256', 'journal_floor', 'enrollment_sha256'},
            'Root grant fields differ')
    require(grant['schema'] == 'dams-observer-root-sync-grant-v1' and grant['issued_by'] == 'ROOT'
            and grant['plan_sha256'] == plan_sha256 and grant['operation_prefix'] == plan['operation_prefix']
            and grant['day'] == plan['day'] and grant['physical_roots'] == [str(p) for p,_ in PHYSICAL_ROOTS]
            and grant['state_root'] == str(Path(state_root))
            and grant['observer_source_sha256'] == sha(Path(__file__).read_bytes())
            and hexsha(grant['enrollment_sha256'])
            and grant['max_events'] == 57 and grant['max_provider_calls'] == 0
            and type(grant['max_gh_api_calls']) is int and 1 <= grant['max_gh_api_calls'] <= 57*32,
            'Root grant scope differs')
    require(set(grant['expected_parents']) == set(REPOS)
            and all(hexsha(v, 40) for v in grant['expected_parents'].values()), 'remote parent pins differ')
    deadline = utc(grant['deadline_utc'])
    marker = parse(stable(state_root,'enrollment.json',expected=grant['enrollment_sha256']))
    authority = parent_authority(marker['parent_authority'])
    require(authority['state_root'] == str(Path(state_root)) and authority['operation_prefix'] == grant['operation_prefix']
            and authority['day'] == grant['day'] and authority['max_events'] == grant['max_events']
            and authority['max_provider_calls'] == grant['max_provider_calls']
            and authority['max_gh_api_calls'] == grant['max_gh_api_calls']
            and authority['frozen_science'] == plan['public_event']['frozen_science']
            and deadline <= utc(authority['node_deadline_utc']) <= utc(authority['global_deadline_utc']),
            'original parent caps/source/deadline cannot reset or extend')
    def guard():
        require(clock().astimezone(timezone.utc) < deadline, 'Root synchronization deadline expired')
        require(utc(plan['public_event']['observed_utc']) <= clock().astimezone(timezone.utc)
                and utc(plan['public_event']['observed_utc']) <= deadline,
                'observed UTC is future or exceeds original operation deadline')
        stable(plan_path.parent, plan_path.name, expected=plan_sha256)
        stable(grant_path.parent, grant_path.name, expected=grant_sha256)
        stable(state_root,'enrollment.json',expected=grant['enrollment_sha256'])
        require(parent_authority(marker['parent_authority']) == authority
                and clock().astimezone(timezone.utc) < utc(authority['node_deadline_utc'])
                and clock().astimezone(timezone.utc) < utc(authority['global_deadline_utc']),
                'original parent budget or node/global deadline differs')
        require(grant['observer_source_sha256'] == sha(Path(__file__).read_bytes()), 'observer source changed')
    guard()
    # Re-read ALL selected small receipts before any external action. A prior
    # local partial publication may already contain the proposed current count;
    # its original approved preimage remains in the pinned plan.
    require(plan['schema'] == 'dams-accepted-result-observer-preparation-v1' and plan['publisher_sha256'] == PUBLISHER
            and plan['event_id'] == sha(canonical(plan['public_event']))
            and len(plan['files']) == 2 and CURRENT in plan['files'], 'prepared payload differs')
    event = next(p for p in plan['files'] if p != CURRENT)
    require(event == f"docs/research-log/{plan['day']}/{plan['operation_prefix']}-{plan['event_id']}.json"
            and plan['files'][event] == plan['public_event'], 'public event scope differs')
    require(plan == inspect(Path(plan['input_path']), plan['input_sha256'],
                            physical_preimages=plan['physical_preimages'],now=clock), 'receipt metadata changed after preparation')
    input_request = parse(stable(Path(plan['input_path']).parent, Path(plan['input_path']).name, expected=plan['input_sha256']))
    science_root = Path(input_request['study_root'])
    state_path = Path(state_root)
    require(not state_path.is_relative_to(science_root) and not science_root.is_relative_to(state_path)
            and not any(state_path.is_relative_to(p) for p,_ in PHYSICAL_ROOTS), 'journal must be outside science and physical repositories')
    require(plan['public_event']['new_verified_pairs'], 'no newly accepted pair to synchronize')
    raw_files = {p: canonical(v)+b'\n' for p,v in plan['files'].items()}
    journal = Journal(state_root, grant['enrollment_sha256'])
    try:
        floor = grant['journal_floor']
        require(isinstance(floor, dict) and set(floor) == {'records', 'head_sha256'}
                and type(floor['records']) is int and 1 <= floor['records'] <= len(journal.rows)
                and floor['head_sha256'] == ('0'*64 if floor['records'] == 0 else sha(canonical(journal.rows[floor['records']-1]))),
                'original issued journal floor changed or truncated')
        scope = authority
        scope_rows = [r for r in journal.rows if r['kind'] == 'SCOPE']
        require(len(scope_rows) == 1 and scope_rows[0]['scope'] == scope, 'original scope/counter cap cannot reset')
        grants = [r for r in journal.rows if r['kind'] == 'CHILD_GRANT']
        require(grant['journal_floor']['records'] >= max([r['journal_floor']['records'] for r in grants]+[1]),
                'original acknowledged source floor regressed')
        if not any(r['grant_sha256'] == grant_sha256 for r in grants):
            require(len(grants) < 1881, 'finite child-grant metadata bound exhausted')
            journal.append('CHILD_GRANT', grant_sha256=grant_sha256, plan_sha256=plan_sha256,
                           journal_floor=grant['journal_floor'], observer_source_sha256=grant['observer_source_sha256'])
        begins = [r for r in journal.rows if r['kind'] == 'OP_BEGIN']
        existing = [r for r in begins if r['event_id'] == plan['event_id']]
        if not existing:
            require(len(begins) < 57, 'original event counter exhausted')
            require(not any(r['kind'] == 'OP_BEGIN' and not any(d['kind'] == 'OP_DONE' and d['event_id'] == r['event_id'] for d in journal.rows) for r in journal.rows), 'previous partial operation must close first')
            journal.append('OP_BEGIN', event_id=plan['event_id'], plan_sha256=plan_sha256,
                           grant_sha256=grant_sha256, expected_parents=grant['expected_parents'],
                           deadline_utc=grant['deadline_utc'], journal_floor=grant['journal_floor'])
        else:
            require(existing[0]['plan_sha256'] == plan_sha256 and existing[0]['expected_parents'] == grant['expected_parents']
                    and deadline <= utc(existing[0]['deadline_utc'])
                    and grant['journal_floor']['records'] >= existing[0]['journal_floor']['records'],
                    'resumed original operation/source floor/deadline regressed')
        op = plan['event_id']
        def completed(key):
            return next((r for r in reversed(journal.rows) if r['kind'] == 'STEP_DONE' and r['event_id'] == op and r['key'] == key), None)
        def api(key, method, endpoint, body=None):
            done = completed(key)
            if done: return done['response']
            guard()
            journal.guard_native()
            prior = [r for r in journal.rows if r['kind'] == 'API_BEGIN' and r['event_id'] == op and r['key'] == key]
            if prior and method != 'GET':
                require(all(any(d['kind'] == 'API_FAILED' and d['call'] == b['call'] and d['definitely_not_applied'] for d in journal.rows) for b in prior), 'unknown mutating outcome; Root reconciliation required')
            calls = sum(r['kind'] == 'API_BEGIN' for r in journal.rows)
            require(authority['max_provider_calls'] == 0 and authority['max_events'] == 57
                    and len(begins) <= 57 and calls < authority['max_gh_api_calls'] <= 1824,
                    'original GitHub API counter exhausted')
            begin = journal.append('API_BEGIN', event_id=op, key=key, call=calls, method=method,
                                   endpoint=endpoint, body_sha256=sha(canonical(body)))
            try:
                remaining = (deadline-clock().astimezone(timezone.utc)).total_seconds()
                require(remaining > 0, 'Root synchronization deadline expired')
                response = transport.api(method, endpoint, body, timeout=min(45., remaining))
                require(isinstance(response, dict) and len(canonical(response)) <= BOUND, 'bounded API metadata response required')
            except BaseException as error:
                journal.append('API_FAILED', event_id=op, key=key, call=begin['call'],
                               error_class=type(error).__name__,
                               definitely_not_applied=(method == 'GET' or getattr(error, 'definitely_not_applied', False) is True))
                raise
            journal.append('STEP_DONE', event_id=op, key=key, call=begin['call'], response=response)
            return response
        commits = []
        for repo in REPOS:
            if completed(repo+':done'):
                row = completed(repo+':done')['response']
                nonce = str(sum(r['kind'] == 'API_BEGIN' for r in journal.rows))
                require(api(repo+':completed-ref:'+nonce, 'GET', '/repos/'+repo+'/git/ref/heads/main')['object']['sha'] == row['commit'],
                        'completed repository ref drift; no republish')
                commits.append(row); continue
            base = '/repos/'+repo; expected = grant['expected_parents'][repo]
            # Ref reads are always fresh, including resumes; immutable object
            # POST results can be reused after their issued BEGIN completes.
            nonce = str(sum(r['kind'] == 'API_BEGIN' for r in journal.rows))
            ref = api(repo+':before:'+nonce, 'GET', base+'/git/ref/heads/main')
            proposed = completed(repo+':commit')
            target = None if proposed is None else proposed['response']['sha']
            require(ref['object']['sha'] in ({expected, target} if target else {expected}), 'remote drift before mutation')
            if ref['object']['sha'] == expected:
                old = api(repo+':parent', 'GET', base+'/git/commits/'+expected)
                existing_event = api(repo+':event-preimage', 'GET', base+'/contents/'+event+'?ref='+expected)
                require(existing_event == {'not_found':True} or
                        ('content' in existing_event and base64.b64decode(existing_event['content']) == raw_files[event]),
                        'immutable remote event already has different bytes')
                tree = api(repo+':tree', 'POST', base+'/git/trees', {'base_tree': old['tree']['sha'],
                           'tree': [{'path':p, 'mode':'100644', 'type':'blob', 'content':v.decode()} for p,v in raw_files.items()]})
                commit = api(repo+':commit', 'POST', base+'/git/commits', {'message': 'Record accepted frozen N300 receipt metadata',
                             'tree':tree['sha'], 'parents':[expected]})
                target = commit['sha']; require(hexsha(target, 40), 'commit SHA differs')
                nonce = str(sum(r['kind'] == 'API_BEGIN' for r in journal.rows))
                ref = api(repo+':pre-patch:'+nonce, 'GET', base+'/git/ref/heads/main')
                require(ref['object']['sha'] == expected, 'remote drift; no force update')
                api(repo+':patch', 'PATCH', base+'/git/refs/heads/main', {'sha':target, 'force':False})
            nonce = str(sum(r['kind'] == 'API_BEGIN' for r in journal.rows))
            require(api(repo+':after:'+nonce, 'GET', base+'/git/ref/heads/main')['object']['sha'] == target, 'remote post-update drift')
            for path, raw in raw_files.items():
                obj = api(repo+':content:'+path, 'GET', base+'/contents/'+path+'?ref='+target)
                require(base64.b64decode(obj['content'], validate=False) == raw
                        and obj['sha'] == hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(), 'remote metadata readback differs')
            row = {'repo':repo, 'before':expected, 'commit':target, 'full_byte_readback_pass':True}
            journal.append('STEP_DONE', event_id=op, key=repo+':done', response=row); commits.append(row)
        for physical, repo in PHYSICAL_ROOTS:
            for path, raw in raw_files.items():
                guard()
                write_metadata(physical, path, raw, previous=plan['physical_preimages'][str(physical)] if path == CURRENT else None,
                               replace=path == CURRENT)
        if not any(r['kind'] == 'OP_DONE' and r['event_id'] == op for r in journal.rows):
            journal.append('OP_DONE', event_id=op, commits=commits)
        return {'status':'synchronized-small-metadata', 'commits':commits,
                'API_calls_total':sum(r['kind'] == 'API_BEGIN' for r in journal.rows),
                'events_total':sum(r['kind'] == 'OP_BEGIN' for r in journal.rows), 'provider_calls':0,
                'newly_accepted_formal_worlds':len(plan['public_event']['new_verified_pairs']),
                'observer_Model_calls':0, 'observer_generated_worlds':0,
                'independent_full_raw_verification_performed':False, 'whole_goal_complete':False}
    finally: journal.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--input-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path, help='existing owned metadata preparation directory')
    args = parser.parse_args(argv)
    plan = inspect(args.input, args.input_sha256)
    request = parse(stable(args.input.parent, args.input.name, expected=args.input_sha256))
    science = Path(request['study_root'])
    require(not args.output.is_relative_to(science) and not science.is_relative_to(args.output), 'preparation output must be outside raw science')
    raw = canonical(plan)+b'\n'
    write_metadata(args.output, 'prepared-metadata.json', raw)
    print(json.dumps({'status':'prepared-only', 'plan_sha256':sha(raw), 'provider_calls':0,
                      'GitHub_calls':0, 'independent_full_raw_verification_performed':False}))


if __name__ == '__main__':
    main()
