"""Versioned research-VM GCS route; Root enrollment is explicit, never inferred.

The legacy no-SA controller and GCS IAM admission remain unchanged. This route
uses the existing generation-pinned GCS adapter, Collector, lossless codec and
scientific gates on the actual study VM. It never creates provider resources or
authorizes deletion, and a VM/GCS pair is not a final independent off-GCP archive.
"""
from __future__ import annotations

from contextlib import ExitStack
import copy
import fcntl
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import quote

from research_tools.compute_only_control import blob, parse, read, require
from research_tools import compute_only_persistent_backends as pb
from dams_sim._committed_pages import Directory, identity
from dams_sim.storage import canonical, digest

SCHEMA = 'dams-same-region-research-archive-route-v1'
ROOT_SCHEMA = 'dams-root-same-region-workload-admission-v1'
PROOF_SCHEMA = 'dams-same-region-full-restoration-reuse-v1'
SCOPE = 'https://www.googleapis.com/auth/devstorage.read_write'
PERMISSIONS = {'storage.buckets.get', 'storage.objects.create', 'storage.objects.get'}
ROOT_FIELDS = {'schema', 'project', 'region', 'bucket', 'prefix', 'service_account',
    'source_manifest_sha256', 'global_deadline_utc', 'custody_epoch', 'bucket_role',
    'bucket_metadata_sha256', 'bucket_iam_sha256', 'project_iam_sha256',
    'granted_permissions', 'guest_project_roles', 'root_verified',
    'expected_versioning', 'expected_soft_delete_seconds'}
SHA = re.compile(r'[0-9a-f]{64}\Z')
IMPORT_SHA = digest(Path(__file__).read_bytes())


def root_admission(raw, expected_sha256, *, project, region, source_manifest_sha256,
                   global_deadline_utc):
    """Consume separately SHA-pinned Root readback, not guest IAM assertions.

    Root must actually resolve custom-role permissions, bindings/conditions and
    project grants before enrolling this receipt. The guest receives only the
    receipt and cannot request getIamPolicy or provision an identity.
    """
    require(isinstance(raw, bytes) and len(raw) <= pb.MAX_POLICY
            and SHA.fullmatch(expected_sha256) and digest(raw) == expected_sha256,
            'exact Root workload admission bytes required')
    a = parse(raw)
    require(isinstance(a, dict) and set(a) == ROOT_FIELDS and a['schema'] == ROOT_SCHEMA
            and a['root_verified'] is True, 'Root workload admission schema differs')
    require(a['project'] == project and a['region'] == region
            and a['source_manifest_sha256'] == source_manifest_sha256
            and a['global_deadline_utc'] == global_deadline_utc,
            'Root workload admission target/source/deadline differs')
    require(re.fullmatch(r'[a-z]+-[a-z0-9]+[0-9]', region)
            and re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,220}[a-z0-9]', a['bucket'])
            and '..' not in a['bucket']
            and re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', a['prefix']),
            'single regional location/bucket/prefix required')
    require(isinstance(a['service_account'], str)
            and re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]@' + re.escape(project)
                             + r'\.iam\.gserviceaccount\.com', a['service_account']),
            'dedicated same-project workload service account required')
    require(re.fullmatch(r'projects/' + re.escape(project) + r'/roles/[A-Za-z][A-Za-z0-9_]{2,63}', a['bucket_role']), 'explicit dedicated custom bucket role required')
    require(isinstance(a['granted_permissions'], list)
            and len(a['granted_permissions']) == len(PERMISSIONS)
            and set(a['granted_permissions']) == PERMISSIONS
            and a['guest_project_roles'] == [],
            'guest needs exact bucket get/object create/get with no project role')
    require(type(a['expected_versioning']) is bool
            and type(a['expected_soft_delete_seconds']) is int
            and 0 <= a['expected_soft_delete_seconds'] <= 90 * 86400,
            'explicit retained-version/soft-delete policy required')
    for key in ('source_manifest_sha256', 'bucket_metadata_sha256', 'bucket_iam_sha256',
                'project_iam_sha256', 'custody_epoch'):
        require(isinstance(a[key], str) and SHA.fullmatch(a[key]), 'Root custody/IAM SHA differs')
    return a


def create_command(c, s, e, zone, startup, *, startup_sha256, admission):
    """Transform the existing bounded no-SA plan only under explicit new mode."""
    from research_tools import cloud_control as cc
    argv = cc.create_compute_only_command(c, s, e, zone, startup,
                                         startup_sha256=startup_sha256)
    require(zone.rsplit('-', 1)[0] == admission['region'] == c['region']
            and admission['project'] == c['project'], 'same-region create target differs')
    require(argv.count('--no-service-account') == argv.count('--no-scopes') == 1,
            'original no-SA plan shape differs')
    return [x for x in argv if x not in ('--no-service-account', '--no-scopes')] + [
        '--service-account=' + admission['service_account'], '--scopes=' + SCOPE]


def validate_instance(c, s, e, vm, admission):
    """Keep original STOP/expiry/hardware/network checks; inspect actual SA first."""
    from research_tools import cloud_control as cc
    require(vm.get('serviceAccounts') == [{'email': admission['service_account'],
                                         'scopes': [SCOPE]}],
            'same-region VM identity or scopes differ')
    require(vm.get('zone', '').rsplit('/', 1)[-1].rsplit('-', 1)[0]
            == admission['region'] == c['region'], 'actual VM region differs')
    observed = copy.deepcopy(vm)
    observed['serviceAccounts'] = []
    cc.validate_compute_only_instance(c, s, e, observed)
    return vm


class MetadataTransport:
    """One link-local metadata host, no proxy, redirects or credential logging."""
    def get(self, endpoint, timeout, maximum):
        require(endpoint.startswith('/computeMetadata/v1/')
                and not any(x in endpoint for x in ('\r', '\n', '://', '..')),
                'fixed metadata endpoint required')
        connection = http.client.HTTPConnection('169.254.169.254', timeout=timeout)
        try:
            connection.request('GET', endpoint, headers={'Metadata-Flavor': 'Google'})
            response = connection.getresponse()
            require(response.status == 200 and response.getheader('Metadata-Flavor') == 'Google',
                    'metadata response identity/status differs')
            raw = response.read(maximum + 1)
            require(len(raw) <= maximum and response.read(1) == b'', 'metadata full EOF/bound differs')
            return raw
        except BaseException:
            raise ValueError('bounded workload metadata unavailable') from None
        finally:
            connection.close()


class MetadataToken:
    """Attached workload token stays only in memory; durable attempts never reset."""
    @pb._construction
    def __init__(self, admission, *, instance_id, zone, deadline, state_root,
                 max_auth_requests, check, transport=None, state_identity=None, minimum=None):
        self.token = None; self.expiry = 0; self.closed = False; self.state = None
        self.a = admission; self.instance_id = str(instance_id); self.zone = zone
        self.deadline = deadline; self.check = check; self.transport = transport or MetadataTransport()
        require(type(max_auth_requests) is int and 5 <= max_auth_requests <= 10000,
                'explicit bounded metadata request allocation required')
        self.maximum = max_auth_requests; self.state = Directory(state_root)
        self.initial_ctime_ns = os.fstat(self.state.fd).st_ctime_ns
        if state_identity is not None:
            if isinstance(state_identity, dict):
                require(set(state_identity)=={'dev','ino','initial_ctime_ns'}
                    and list(identity(os.fstat(self.state.fd))[:2])==[state_identity['dev'],state_identity['ino']],
                    'Root native auth state anchor differs')
            else: require(list(identity(os.fstat(self.state.fd))[:2]) == state_identity, 'Root native auth state anchor differs')
        self.state_identity = state_identity
        self.minimum = minimum or {'count': 0, 'last_sha256': None}
        require(set(self.minimum)=={'count','last_sha256'} and type(self.minimum['count']) is int and 0<=self.minimum['count']<=max_auth_requests, 'Root original auth floor differs')
        self.token = None; self.expiry = 0; self.closed = False
        self.binding = blob({'schema': 'dams-workload-auth-binding-v1',
            'admission_sha256': digest(blob(admission)), 'instance_id': self.instance_id,
            'zone': zone, 'deadline': deadline, 'max_auth_requests': max_auth_requests})
        self._bind()

    def _bind(self):
        with self._lock():
            try: old = read(self.state, 'binding.json')[0]
            except FileNotFoundError:
                require(set(os.listdir(self.state.fd)) == {'lock'}, 'missing auth history cannot reset')
                if isinstance(self.state_identity, dict):
                    require(self.initial_ctime_ns==self.state_identity['initial_ctime_ns'],
                            'original empty auth namespace changed; no counter reset')
                self.state.write_new('binding.json', self.binding)
                self.state.write_new('head.json', blob({'count': 0, 'last_sha256': None}))
            else: require(old == self.binding, 'workload auth original binding changed')
            self._history()

    def _lock(self):
        class Lock:
            def __enter__(inner):
                inner.fd = self.state.open('lock', os.O_RDWR | os.O_CREAT)
                require(stat.S_ISREG(os.fstat(inner.fd).st_mode)
                        and os.fstat(inner.fd).st_nlink == 1, 'auth lock private regular required')
                fcntl.flock(inner.fd, fcntl.LOCK_EX); return inner
            def __exit__(inner, *args): os.close(inner.fd)
        return Lock()

    def _history(self):
        names = sorted(n for n in os.listdir(self.state.fd) if n.endswith('.attempt.json'))
        require(names == [f'{i:08d}.attempt.json' for i in range(1, len(names)+1)],
                'metadata authentication history gap')
        previous = None; floor_sha = None
        for i, name in enumerate(names, 1):
            raw = read(self.state, name)[0]; row = parse(raw)
            require(row['sequence'] == i and row['previous_sha256'] == previous
                    and row['binding_sha256'] == digest(self.binding), 'auth attempt binding differs')
            previous = digest(raw)
            if i == self.minimum['count']: floor_sha = previous
        require(len(names)>=self.minimum['count'] and floor_sha==self.minimum['last_sha256'], 'Root metadata authentication high-water rollback')
        try: head = parse(read(self.state, 'head.json')[0])
        except FileNotFoundError: raise ValueError('existing auth head missing; no counter reset') from None
        require(head == {'count': len(names), 'last_sha256': previous}, 'auth head rollback/partial')
        return head

    def _get(self, path, maximum):
        self.check(); require(not self.closed and time.time() < self.deadline, 'workload auth deadline')
        with self._lock():
            require(read(self.state, 'binding.json')[0] == self.binding, 'auth custody changed')
            head = self._history(); require(head['count'] < self.maximum, 'workload auth allowance exhausted')
            row = {'sequence': head['count']+1, 'previous_sha256': head['last_sha256'],
                   'binding_sha256': digest(self.binding), 'endpoint_sha256': digest(path.encode())}
            self.state.write_new(f'{row["sequence"]:08d}.attempt.json', blob(row))
            from research_tools.compute_only_remote import write
            write(self.state.path/'head.json', blob({'count': row['sequence'],
                'last_sha256': digest(blob(row))}), blob(head))
        # Reserve before requesting even when HTTP fails; no retry or refund.
        try: return self.transport.get('/computeMetadata/v1/' + path,
                                      min(5, self.deadline-time.time()), maximum)
        except BaseException: raise ValueError('bounded workload credential unavailable') from None

    def __call__(self):
        self.check(); now = time.time(); require(now < self.deadline and not self.closed, 'auth expired')
        if self.token is not None and self.expiry > now + 30: return self.token
        base = 'instance/service-accounts/' + quote(self.a['service_account'], safe='') + '/'
        expected = {'project/project-id': self.a['project'], 'instance/id': self.instance_id,
            'instance/zone': self.zone, base+'email': self.a['service_account']}
        for path, value in expected.items():
            actual = self._get(path, 1024).decode()
            if path == 'instance/zone': actual = actual.rsplit('/', 1)[-1]
            require(actual == value, 'actual workload identity differs')
        value = parse(self._get(base+'token', 32768))
        require(isinstance(value, dict) and set(value) == {'access_token', 'expires_in', 'token_type'}
                and value['token_type'] == 'Bearer' and type(value['expires_in']) is int
                and 60 <= value['expires_in'] <= 3600, 'bounded metadata token expiry/type differs')
        token = value['access_token']
        require(isinstance(token, str) and 0 < len(token) <= 16384
                and all(32 < ord(x) < 127 for x in token), 'metadata token shape differs')
        self.token = token; self.expiry = min(self.deadline, time.time()+value['expires_in']-30)
        self.check(); return token

    def snapshot(self):
        with self._lock(): return dict(self._history())

    def close(self):
        self.token = None; self.expiry = 0
        if not getattr(self, 'closed', False):
            self.closed = True
            if getattr(self, 'state', None) is not None: self.state.close()


class LeaseGuard:
    """Original 120-second Root lease remains a continuous native I/O gate."""
    @pb._construction
    def __init__(self, context, config_path, config_raw):
        import sys
        from research_tools.compute_only_remote import stable
        sys.path[:0] = [str(Path(__file__).parent)]
        from research_tools import compute_only_worker as worker
        self.work = None; self.closed = False; self.worker = worker; self.context = context
        self.config_path = config_path; self.config_raw = config_raw; self.stable = stable
        self.runtime_raw = stable(Path(context['work_root'])/'compute-runtime.json')
        require(digest(self.runtime_raw) == context['runtime_sha256'], 'original runtime SHA differs')
        runtime = parse(self.runtime_raw)
        require(runtime['controller_lease_timeout_seconds'] == 120
                and runtime['deadline_utc'] == context['deadline_utc'], 'original 120-second lease/node deadline differs')
        from research_tools.cloud_control import utc
        self.deadline = utc(context['deadline_utc']).timestamp()
        self.wall = time.time(); self.monotonic = time.monotonic(); self.previous = None
        self.work = worker.Directory(context['work_root']); self.check()

    def check(self):
        require(digest(Path(__file__).read_bytes()) == IMPORT_SHA,
                'regional archive running helper source changed')
        require(not self.closed and self.stable(self.config_path) == self.config_raw
                and self.stable(self.work.path/'compute-runtime.json') == self.runtime_raw,
                'archive original config/runtime custody changed')
        now = max(time.time(), self.wall+time.monotonic()-self.monotonic)
        require(now < self.deadline, 'archive original node cutoff reached')
        self.previous = self.worker.lease(self.work, self.context['runtime_sha256'], 120, now, self.previous)

    def cancelled(self):
        self.check(); return False

    def close(self):
        if not getattr(self, 'closed', False):
            self.closed = True
            if getattr(self, 'work', None) is not None: self.work.close()


def require_closed_producer(context):
    """This first operational route archives only a closed original producer.

    The two arms finish (or fail) before archive work starts. No expensive I/O
    can hold a lock needed by an active model/checkpoint writer. This restriction
    is an execution prerequisite, not a claim that checkpoint streaming works.
    """
    from research_tools.compute_only_remote import stable
    terminal_raw = stable(Path(context['work_root'])/'compute-terminal.json')
    terminal = parse(terminal_raw)
    proof = terminal['owned_pipeline_group_verification']
    require(terminal['runtime_sha256'] == context['runtime_sha256']
            and terminal['inputs_unchanged'] is True and proof['owned_pipeline_group_absent'] is True,
            'archive needs original closed/unchanged owned producer')
    require(type(proof['owned_pipeline_group_id']) is int and proof['owned_pipeline_group_id'] > 1,
            'original owned producer group identity differs')
    try: os.killpg(proof['owned_pipeline_group_id'], 0)
    except ProcessLookupError: pass
    else: raise ValueError('archive refuses active owned producer')
    return digest(terminal_raw)


def capacity_admission(bounds, observation):
    """Joint native capacity gate; bounds are Root-enrolled, never estimates.

    The declaration deliberately separates Collector encoded cache from spool
    and CAS. Decodes in verify() are sinks; the one scientific raw lease has its
    own enforced cumulative cap, including failures. No compression ratio is
    assumed here. This function does not grant paid/provider authority.
    """
    fields = {'scientific_raw_upper_bytes', 'spool_upper_bytes',
        'collector_cache_upper_bytes', 'local_cas_upper_bytes',
        'raw_lease_upper_bytes', 'recovery_input_upper_bytes',
        'metadata_and_boot_upper_bytes', 'safety_floor_bytes'}
    require(isinstance(bounds, dict) and set(bounds) == fields
            and all(type(v) is int and v > 0 for v in bounds.values()),
            'exact positive joint working capacity bounds required')
    required = sum(bounds.values())
    require(isinstance(observation, dict) and set(observation) ==
            {'total_bytes', 'available_bytes', 'used_bytes', 'device'}
            and all(type(observation[k]) is int and observation[k] >= 0 for k in observation)
            and observation['total_bytes'] >= required
            and observation['available_bytes'] >= required-bounds['metadata_and_boot_upper_bytes']
            and observation['used_bytes'] <= bounds['metadata_and_boot_upper_bytes'],
            'actual native device cannot fit all workspaces plus unchanged safety floor')
    return {'schema':'dams-same-region-joint-capacity-admission-v1', 'bounds':bounds,
        'observation':observation, 'declared_peak_bytes':required,
        'compression_assumed':False,'provider_capacity_catalog_is_proof':False,
        'physical_two_full_restore_copies_retained':False,
        'scientific_raw_lease_is_separately_bounded':True,'science_complete':False}


def native_capacity_plan(plan_raw, *, context):
    """Actual statvfs on the enrolled studyVM, after doctor and before activate."""
    from research_tools.compute_only_remote import path
    require(digest(plan_raw)==context['regional_capacity_plan_sha256'], 'Root exact native capacity plan SHA differs')
    plan=parse(plan_raw)
    require(set(plan)=={'schema','project','region','source_manifest_sha256','global_deadline_utc',
        'workload_admission_sha256','bounds'} and plan['schema']=='dams-same-region-working-capacity-plan-v1'
        and plan['project']==context['project'] and plan['region']==context['zone'].rsplit('-',1)[0]
        and plan['source_manifest_sha256']==context['source_manifest_sha256']
        and plan['global_deadline_utc']==context['regional_global_deadline_utc']
        and plan['workload_admission_sha256']==context['regional_workload_admission_sha256'],
        'Root regional capacity/source/custody/deadline differs')
    work=path(context['work_root'])
    require(not os.path.lexists(work/'compute-runtime.json'), 'joint capacity must be admitted before model activation')
    fs=os.statvfs(work);total=fs.f_frsize*fs.f_blocks;available=fs.f_frsize*fs.f_bavail
    observation={'total_bytes':total,'available_bytes':available,'used_bytes':total-available,'device':work.stat().st_dev}
    result=capacity_admission(plan['bounds'],observation)
    return {**result,'plan_sha256':digest(plan_raw),'source_manifest_sha256':context['source_manifest_sha256'],
        'provider_identity_sha256':context['provider_identity_sha256'], 'actual_native_statvfs':True,
        'new_Model_execution':False}


class WorkloadGCSBackend(pb.GCSBackend):
    """Root IAM is pinned externally; guest performs fresh bucket GET only."""
    @pb._construction
    def __init__(self, *args, root_admission, **kwargs):
        self.root_admission = root_admission
        super().__init__(*args, **kwargs)
        c = self.config; a = root_admission
        require(c['bucket'] == a['bucket'] and c['prefix'] == a['prefix']
                and c['expected_location'] == a['region'].upper()
                and c['expected_versioning'] == a['expected_versioning']
                and c['expected_soft_delete_seconds'] == a['expected_soft_delete_seconds'],
                'workload backend differs from Root regional admission')

    def admit(self):
        self.admitted = False; self._local_check(); deadline = self._deadline()
        status, raw, _ = self._full('GET', '/storage/v1/b/'+quote(self.config['bucket'], safe=''),
                                  None, pb.MAX_POLICY, deadline)
        policy = parse(raw); c = self.config
        require(status == 200 and policy.get('name') == c['bucket']
                and policy.get('location') == c['expected_location']
                and policy.get('locationType') == 'region'
                and policy.get('versioning', {}).get('enabled', False) is c['expected_versioning']
                and policy.get('iamConfiguration', {}).get('uniformBucketLevelAccess', {}).get('enabled') is True
                and policy.get('iamConfiguration', {}).get('publicAccessPrevention') == 'enforced'
                and policy.get('softDeletePolicy', {}).get('retentionDurationSeconds') == str(c['expected_soft_delete_seconds']),
                'fresh single-region GCS policy differs')
        self.policy_receipt = {'bucket_policy_sha256': digest(raw),
            'root_IAM_policy_sha256': self.root_admission['bucket_iam_sha256'],
            'root_project_IAM_sha256': self.root_admission['project_iam_sha256'],
            'actual_guest_policy_reads': 1, 'guest_IAM_reads': 0,
            'custody_epoch': self.root_admission['custody_epoch'],
            'not_final_off_GCP_backup': True}
        self.admitted = True; self.check(); return dict(self.policy_receipt)

    def custody(self, key, size, sha):
        """Fresh fixed-generation metadata, bound to earlier complete media SHA."""
        self.check(); pin = self._pin(key)
        require(pin['bytes'] == size and pin['sha256'] == sha
                and isinstance(pin['generation'], str), 'reuse original object pin differs')
        status, raw, _ = self._full('GET', self._path(key, generation=pin['generation'],
            ifGenerationMatch=pin['generation']), None, pb.MAX_POLICY, self._deadline())
        require(status == 200, 'reuse fixed generation missing/inaccessible')
        meta = parse(raw)
        require(isinstance(meta, dict) and meta.get('bucket') == self.config['bucket']
                and meta.get('name') == self.config['prefix']+'/'+key
                and meta.get('generation') == pin['generation'] and meta.get('size') == str(size)
                and meta.get('contentType') == 'application/octet-stream'
                and meta.get('contentEncoding') is None, 'reuse immutable generation metadata differs')
        return {'key': key, 'bytes': size, 'sha256': sha, 'generation': pin['generation'],
                'binding_sha256': digest(blob(self.binding)),
                'config_sha256': digest(self.config_raw),
                'custody_epoch': self.root_admission['custody_epoch']}


class ProofReuse:
    """Reuse exact prior full restorations; fail on any content/custody drift.

    No new restoration is claimed. Local receipt bytes and scientific/source
    bindings remain checked every time. GCS objects get fresh fixed-generation
    metadata; local CAS objects get full native SHA/EOF/identity checks.
    """
    def __init__(self, route_sha256, custody_epoch):
        require(SHA.fullmatch(route_sha256) and SHA.fullmatch(custody_epoch), 'reuse pins missing')
        self.route_sha = route_sha256; self.epoch = custody_epoch

    def _binding(self, closure, mr, sr, gr, ir):
        return {'schema': PROOF_SCHEMA, 'route_sha256': self.route_sha,
            'custody_epoch': self.epoch, 'profile_sha256': closure.profile_sha,
            'job_sha256': closure.job_sha, 'manifest_sha256': digest(mr),
            'seal_sha256': digest(sr), 'gate_sha256': digest(gr), 'index_sha256': digest(ir),
            'source_sha256': closure.c.config['source_sha256'],
            'pipeline_driver_sha256': closure.c.config['pipeline_driver_sha256'],
            'archive_source_sha256': digest(closure.source_raw),
            'reuse_helper_sha256': IMPORT_SHA,
            'backup_bindings': [b.binding for b in closure.backends]}

    def _objects(self, m, mr, sr, gr, ir):
        rows = [(m['generation']+'/'+n, len(raw), digest(raw)) for n, raw in
                [('manifest.json', mr), ('seal.json', sr), ('scientific-gate.json', gr), ('index.json', ir)]]
        rows += [('objects/'+c['encoded_sha256']+'.z', c['encoded_bytes'], c['encoded_sha256'])
                 for p in m['files'] for c in p['codec']['chunks']]
        return sorted(set(rows))

    def _custody(self, closure, objects):
        rows = []
        for backend in closure.backends:
            backend.check(); selected = []
            for key, size, sha in objects:
                closure.c.check()
                if isinstance(backend, WorkloadGCSBackend):
                    selected.append(backend.custody(key, size, sha))
                elif type(backend) is pb.ExternalCASBackend:
                    stream = backend.read(key); h = hashlib.sha256(); total = 0
                    try:
                        while block := stream.read(65536): h.update(block); total += len(block)
                    finally: stream.close()
                    require(total == size and h.hexdigest() == sha, 'reuse local CAS full bytes differ')
                    selected.append({'key': key, **backend._pin(key),
                        'binding_sha256': digest(blob(backend.binding)),
                        'config_sha256': digest(backend.config_raw)})
                else: raise ValueError('proof reuse requires explicit workload GCS/local CAS pair')
            rows.append(selected)
        return rows

    def record(self, closure, group, m, mr, sr, gr, ir, copies):
        record = {**self._binding(closure, mr, sr, gr, ir), 'copies': copies,
            'objects': self._objects(m, mr, sr, gr, ir),
            'custody': self._custody(closure, self._objects(m, mr, sr, gr, ir))}
        raw = blob(record)
        try: old = read(group, 'full-restoration-reuse.json')[0]
        except FileNotFoundError: group.write_new('full-restoration-reuse.json', raw)
        else: require(old == raw, 'full restoration reuse receipt changed')

    def try_reuse(self, closure, group, m, mr, sr, gr, ir, gate):
        try: raw = read(group, 'full-restoration-reuse.json')[0]
        except FileNotFoundError: return None
        record = parse(raw); expected = self._binding(closure, mr, sr, gr, ir)
        require({k: record.get(k) for k in expected} == expected,
                'original restoration reuse source/manifest/custody invalidated')
        objects = self._objects(m, mr, sr, gr, ir)
        require(record.get('objects') == [list(x) for x in objects], 'reuse object inventory changed')
        require(record['custody'] == self._custody(closure, objects), 'reuse object custody invalidated')
        copies = []
        for backend in closure.backends:
            rr = read(group, backend.binding['backup_id']+'.receipt.json')[0]; receipt = parse(rr)
            require(receipt['profile_sha256'] == closure.profile_sha
                    and receipt['job_sha256'] == closure.job_sha
                    and receipt['generation'] == m['generation'] and receipt['files'] == gate['files']
                    and receipt['backup_binding'] == backend.binding
                    and receipt['index_sha256'] == digest(ir) and receipt['scientific_gate_sha256'] == digest(gr)
                    and receipt['manifest_sha256'] == digest(mr) and receipt['group_sha256'] == m['group_sha256']
                    and receipt['verification'] == 'fresh-full-decode-to-sink-and-original-scientific-gate',
                    'reuse original full restoration receipt differs')
            copies.append({'restoration_id': receipt['restoration_id'], 'receipt_sha256': digest(rr),
                           'group_sha256': m['group_sha256'], 'gate': gate['gate']})
        require(copies == record['copies'], 'reuse original receipt bytes changed')
        closure.c.check(); return copies


def guest_collect(config_path, config_sha256, *, context, metadata_transport=None,
                  gcs_transport=None):
    """One sealed generation, on actual research VM, original scientific gate.

    Root precreates/seals the VM-local refs and native anchors after doctor.
    Auth/meter/Collector heads are retained across calls and never reset.
    """
    from research_tools.compute_only_remote import stable, path
    from research_tools.compute_only_control import Collector, ARCHIVE_COLLECTOR_SCHEMA
    from research_tools import persistent_backend_budget_meter as bm
    from dams_sim.transfer_spool import TransferSpool
    p = path(config_path); raw = stable(p)
    require(digest(raw) == config_sha256, 'Root guest archive config SHA differs')
    config = parse(raw)
    fields = {'schema', 'root_admission', 'admission', 'assignment', 'backends', 'meter',
        'minimum_head', 'minimum_meter', 'generation', 'parent_generations',
        'auth_state_dir', 'auth_state_identity', 'minimum_auth', 'max_auth_requests', 'source_manifest_sha256', 'runtime_sha256',
        'provider_identity_sha256', 'deadline_utc', 'global_deadline_utc'}
    require(isinstance(config, dict) and set(config) == fields and config['schema'] == SCHEMA,
            'guest regional archive exact fields differ')
    require(all(config[k] == context[k] for k in ('source_manifest_sha256', 'runtime_sha256',
        'provider_identity_sha256', 'deadline_utc')), 'guest archive actual runtime/source/provider differs')
    def reference(ref):
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}
                and SHA.fullmatch(ref['sha256']), 'guest archive exact reference required')
        data = stable(ref['path']); require(digest(data) == ref['sha256'], 'guest archive reference differs')
        return data
    root_raw = reference(config['root_admission'])
    a = root_admission(root_raw, config['root_admission']['sha256'], project=context['project'],
        region=context['zone'].rsplit('-', 1)[0], source_manifest_sha256=context['source_manifest_sha256'],
        global_deadline_utc=config['global_deadline_utc'])
    admission_raw = reference(config['admission']); ad = parse(admission_raw)
    require(ad['schema'] == ARCHIVE_COLLECTOR_SCHEMA and ad['stage_id'] == context['stage_id']
            and ad['source_sha256'] == context['source_sha256']
            and ad['deadline_utc'] == context['deadline_utc'], 'guest Collector target/source/deadline differs')
    assignment = reference(config['assignment']); profile = parse(reference(ad['storage_profile']))
    require(profile['job']['runtime_sha256'] == context['runtime_sha256']
            and profile['job']['provider_identity_sha256'] == context['provider_identity_sha256']
            and profile['mode'] == 'external-persistent', 'guest full archive runtime/provider mode differs')
    job_sha = digest(canonical(profile['job']))
    reference(config['meter']); meter = bm.Meter(config['meter']['path'], config_sha256=config['meter']['sha256'])
    snapshot = meter.snapshot()
    floor = config['minimum_meter']
    from research_tools.compute_only_backend_factory import Factory
    fence = object.__new__(Factory); fence.minimum = floor; fence._floor(snapshot)
    require(config['max_auth_requests'] + 3 <= meter.config['limits']['authentication_requests_margin'],
            'metadata request allocation exceeds original budget margin')
    require(isinstance(config['auth_state_identity'],dict)
            and set(config['auth_state_identity'])=={'dev','ino','initial_ctime_ns'},
            'Root initial native auth custody anchor required')
    from research_tools.cloud_control import utc
    require(utc(context['deadline_utc']) <= utc(config['global_deadline_utc']), 'node expiry extends original global deadline')
    with ExitStack() as stack:
        closed_producer_sha = require_closed_producer(context)
        guard = LeaseGuard(context, p, raw); stack.callback(guard.close)
        token = MetadataToken(a, instance_id=context['instance_id'], zone=context['zone'],
            deadline=utc(context['deadline_utc']).timestamp(), state_root=config['auth_state_dir'],
            max_auth_requests=config['max_auth_requests'], check=guard.check,
            transport=metadata_transport, state_identity=config['auth_state_identity'], minimum=config['minimum_auth'])
        stack.callback(token.close); built = []
        require(isinstance(config['backends'], list) and len(config['backends']) == 2, 'two exact guest backends required')
        for item in config['backends']:
            reference(item['config']); enrolled = reference(item['enrollment']); options = parse(reference(item['config']))
            args = (item['config']['path'], item['config']['sha256'], enrolled, job_sha)
            backend = (pb.ExternalCASBackend(*args, cancel=guard.cancelled) if options['kind'] == 'external-cas' else
                WorkloadGCSBackend(*args, root_admission=a, token_provider=token, meter=meter, transport=gcs_transport, cancel=guard.cancelled))
            stack.callback(backend.close); built.append(backend)
        require({type(b) for b in built} == {pb.ExternalCASBackend, WorkloadGCSBackend}, 'exact VM-local CAS/regional GCS pair required')
        for b in built:
            if isinstance(b, WorkloadGCSBackend): b.admit()
        # Floors and generation selection advance without rewriting the science
        # or invalidating unchanged proof. Allocation, source and custody do not.
        stable_route = {k:v for k,v in config.items() if k not in
                        ('minimum_head', 'minimum_meter', 'minimum_auth', 'generation', 'parent_generations')}
        reuse = ProofReuse(digest(blob(stable_route)), a['custody_epoch'])
        with Collector(config['admission']['path'], admission_sha256=config['admission']['sha256'],
                assignment_raw=assignment, minimum_head=config['minimum_head'], archive_backends=built,
                proof_reuse=reuse, lifetime_check=guard.check) as collector:
            spool = TransferSpool(Path(context['work_root'])/'transfer-config.json',
                                  expected_source_sha256=context['source_sha256'])
            original_check = spool._check
            def spool_check(deadline_monotonic=None):
                guard.check(); original_check(deadline_monotonic)
            spool._check = spool_check
            # Capture only the native immutable sealed descriptor under writer
            # lock. Chunk I/O, science and both restores happen after release.
            with spool._locked():
                spool._check(); spool._history(); generation = config['generation']
                require(generation in spool.records, 'guest archive generation not original reservation')
                group = path(Path(spool.config['spool_dir'])/'groups'/generation)
                m, mr, seal = spool._manifest(group); sr = stable(group/'seal.json')
                group_identity = identity(group.stat())[:2]
            if os.path.lexists(Path(ad['state_dir'])/(generation+'.ACK.json')):
                _, ack_raw = collector.archive.ack_guard(generation)
                result = {'ack_bytes': ack_raw, 'head': collector.head, 'reused_existing_ACK': True}
            else:
                t1 = collector.reserve_metadata_attempt(len(mr)); t2 = collector.reserve_metadata_attempt(len(sr))
                def fetch(part, chunk):
                    guard.check()
                    require(identity(group.stat())[:2] == group_identity, 'original sealed group custody differs')
                    data = stable(group/part['part_directory']/f'{chunk["offset"]:020d}.z', chunk['encoded_bytes'])
                    require(len(data) == chunk['encoded_bytes'] and digest(data) == chunk['encoded_sha256'],
                            'original spool local chunk differs')
                    guard.check(); return io.BytesIO(data)
                result = collector.collect(mr, sr, fetch, metadata_tickets=[t1, t2],
                                           parent_generations=config['parent_generations'])
            # Only an unchanged closed producer may enter original ACK/pruning.
            # This first route intentionally does not claim streaming collection
            # during an active producer; reconciliation can decode a full group.
            require(require_closed_producer(context) == closed_producer_sha,
                    'original closed producer terminal custody differs')
            from research_tools.compute_only_remote import write
            with spool._locked():
                spool._check(); spool._history()
                require(generation in spool.records and identity(group.stat())[:2] == group_identity,
                        'original sealed generation changed before ACK')
                _, current_mr, current_seal = spool._manifest(group)
                require(current_mr == mr and current_seal == seal and stable(group/'seal.json') == sr,
                        'original sealed manifest changed before ACK')
                destination = Path(spool.config['ack_dir'])/(generation+'.json')
                if os.path.lexists(destination): require(stable(destination) == result['ack_bytes'], 'existing guest ACK differs')
                else: write(destination, result['ack_bytes'])
                # Original pruning is retained, and checks the original lease
                # at every chunk through this instance's native _check hook.
                spool._reconcile_acks()
            return {'schema': 'dams-same-region-generation-collected-v1', 'generation': generation,
                'ack_sha256': digest(result['ack_bytes']), 'ack_applied': True,
                'collector_head': collector.head, 'meter_head': meter.snapshot(), 'auth_head': token.snapshot(),
                'actual_original_scientific_gate': parse(result['ack_bytes'])['gate'],
                'producer_terminal_sha256': closed_producer_sha, 'active_producer_supported': False,
                'full_study_gate': False, 'science_complete': False,
                'GCP_external_final_preservation_complete': False}



def background_operation(action, request, *, context, runner=None):
    """Nonblocking, idempotent same-VM unit; keeps original Root heartbeat free.

    This is storage/validation on the study VM, never another Model execution.
    A prior result or existing unit is polled rather than restarted. The command
    contains only private file paths, context pins and no token or private key.
    """
    import base64
    import subprocess
    from research_tools.compute_only_remote import stable, path, write
    from research_tools.cloud_control import utc
    runner = runner or subprocess.run
    require(action in ('regional-archive-start', 'regional-archive-status'), 'unknown archive service action')
    config_path = path(request['config_path']); raw = stable(config_path)
    require(digest(raw) == request['config_sha256'] == context['regional_archive_route_sha256']
            and config_path.parent == Path(context['work_root'])/'regional-archive'/'metadata',
            'archive service Root private config pin differs')
    config = parse(raw); generation = config['generation']; require(SHA.fullmatch(generation), 'archive generation differs')
    result_dir = path(Path(context['work_root'])/'regional-archive'/'results')
    result_path = result_dir/(request['config_sha256']+'.json')
    unit = 'dams-regional-archive-' + generation[:32]
    # The generation has one enrolled writer even if a second config changes
    # floors. Root must reconcile the original BEGIN rather than spawning anew.
    begin = result_dir/(generation+'.BEGIN.json')
    existing = None
    if os.path.lexists(begin):
        existing = parse(stable(begin))
        require(existing['config_sha256'] == request['config_sha256']
                and existing['generation'] == generation and existing['unit'] == unit,
                'archive service original ownership differs')
    if os.path.lexists(result_path):
        receipt = parse(stable(result_path))
        require(existing is not None and receipt['config_sha256'] == request['config_sha256'],
                'archive service terminal receipt missing original ownership')
        return {'schema': 'dams-regional-archive-service-status-v1', 'unit': unit,
                'state': 'TERMINAL', 'receipt_sha256': digest(stable(result_path)),
                'receipt': receipt, 'new_run': False, 'science_complete': False}
    if action == 'regional-archive-start' and existing is None:
        remaining = utc(context['deadline_utc']).timestamp()-time.time()
        require(remaining > 30, 'archive service cannot fit absolute stop/reaping margin')
        encoded = base64.b64encode(blob(context)).decode()
        argv = ['/usr/bin/systemd-run', '--quiet', '--unit='+unit,
            '--property=Type=exec', '--property=KillMode=control-group',
            '--property=TimeoutStopSec=10', '--property=RuntimeMaxSec='+str(max(1,int(remaining-20))),
            '--property=NoNewPrivileges=yes', '--property=UMask=0077',
            '--property=WorkingDirectory='+context['source_root'],
            '/usr/bin/python3', '-B', '-m', 'research_tools.same_region_archive',
            '--config='+str(config_path), '--config-sha256='+request['config_sha256'],
            '--context='+encoded, '--receipt='+str(result_path)]
        first = {'schema':'dams-regional-archive-service-BEGIN-v1', 'unit':unit,
                 'config_sha256':request['config_sha256'], 'generation':generation,
                 'argv_sha256':digest(blob(argv)), 'deadline_utc':context['deadline_utc'],
                 'owned_model_execution':False}
        write(begin, blob(first))
        # Ambiguous spawn is retained and is never auto-retried. Root can inspect
        # the unit journal/BEGIN and exact meter/collector heads to recover.
        try:
            completed = runner(argv, capture_output=True, timeout=min(15,remaining-20))
            require(completed.returncode == 0, 'archive service activation unknown')
        except BaseException:
            raise ValueError('archive service activation unknown; original BEGIN retained') from None
        return {'schema':'dams-regional-archive-service-status-v1','unit':unit,'state':'STARTED',
                'new_run':True,'Model_execution':False,'science_complete':False}
    require(existing is not None, 'archive service not started')
    completed = runner(['/usr/bin/systemctl','show',unit,'--property=ActiveState,SubState,Result',
                        '--no-pager'],capture_output=True,timeout=10)
    require(completed.returncode == 0 and len(completed.stdout) <= 4096, 'owned archive unit status unavailable')
    text = completed.stdout.decode() if isinstance(completed.stdout, bytes) else completed.stdout
    return {'schema':'dams-regional-archive-service-status-v1', 'unit':unit,
            'state':'PENDING_OR_FAILED_NO_TERMINAL_RECEIPT', 'unit_status':text,
            'new_run':False,'science_complete':False}


def main(argv=None):
    import argparse
    import base64
    from research_tools.compute_only_remote import write, path, package, context as identity_context
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',required=True); parser.add_argument('--config-sha256',required=True)
    parser.add_argument('--context',required=True); parser.add_argument('--receipt',required=True)
    args = parser.parse_args(argv); receipt = Path(args.receipt)
    require(not os.path.lexists(receipt) and receipt.parent ==
            Path('/var/lib/dams-compute/regional-archive/results'), 'private exclusive archive result path required')
    c = parse(base64.b64decode(args.context, validate=True)); identity_context(c); package(c)
    started = time.monotonic(); outcome = None; code = 1
    try:
        require(digest(Path(__file__).read_bytes()) == IMPORT_SHA, 'archive executable source changed')
        outcome = guest_collect(args.config,args.config_sha256,context=c); code = 0
    except BaseException as error:
        # Preserve failure without raw exception or credential-bearing causes.
        outcome = {'status':'FAILED_REQUIRES_ROOT_RECONCILIATION','error_type':type(error).__name__,
                   'full_study_gate':False,'science_complete':False}
    result = {'schema':'dams-regional-archive-service-terminal-v1',
        'config_sha256':args.config_sha256,'source_manifest_sha256':c['source_manifest_sha256'],
        'runtime_sha256':c['runtime_sha256'],'provider_identity_sha256':c['provider_identity_sha256'],
        'exit_code':code,'wall_seconds':time.monotonic()-started,'outcome':outcome,
        'Model_execution':False,'science_complete':False}
    write(receipt,blob(result)); raise SystemExit(code)


if __name__ == '__main__': main()
