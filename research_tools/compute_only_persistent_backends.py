"""Root-enrolled immutable binary transports; never provision or start a Model.

The caller must keep the exact adapter state and the original collector ledger.
GCS admission is explicit, uses real bucket/IAM GETs, and does not change policy.
check() is local: ArchiveClosure performs fresh complete binary readbacks.
HTTP meter.before must durably reserve EACH attempted request before transport;
meter.after records observed response payload bytes. These are conservative body
holds, not an invoice or a TLS wire meter. Tokens/headers are never audit fields.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
from urllib.parse import quote, urlencode
import uuid
from functools import wraps

from dams_sim._committed_pages import Directory, identity

IMPORT_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_META = 16 * 1024**2
MAX_POLICY = 256 * 1024
CHUNK = 65536
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMON = {'schema', 'kind', 'binding', 'job_sha256', 'source_sha256', 'state_root',
          'state_identity', 'max_object_bytes', 'timeout_seconds', 'max_retries'}
CAS_FIELDS = {'root', 'root_identity', 'volume', 'volume_identity', 'min_free_bytes'}
GCS_FIELDS = {'bucket', 'prefix', 'expected_location', 'expected_versioning',
              'expected_soft_delete_seconds', 'expected_ubla', 'expected_public_access_prevention'}


class Refusal(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Refusal(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def parse(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            require(k not in out, 'duplicate adapter metadata key')
            out[k] = v
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refusal('nonfinite adapter metadata')))


def key_name(key):
    require(isinstance(key, str) and len(key) <= 200, 'unsafe archive key')
    require(re.fullmatch(r'objects/[0-9a-f]{64}\.z|[0-9a-f]{64}/(manifest|seal|scientific-gate|index)\.json|terminal/(index|roster)\.json', key),
            'unsafe archive key')
    return key


def _construction(fn):
    @wraps(fn)
    def bounded_init(self, *args, **kwargs):
        try: fn(self, *args, **kwargs)
        except BaseException:
            self.close()
            raise
    return bounded_init


def _load(directory, name, maximum=MAX_META):
    require(type(maximum) is int and 0 <= maximum <= 64 * 1024**2, 'adapter metadata read bound differs')
    fd = directory.open(name, os.O_RDONLY | os.O_NONBLOCK)
    try:
        s = os.fstat(fd); observed = identity(s)
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_size <= maximum,
                'metadata is not bounded private regular bytes')
        directory.matches(name, fd); raw = bytearray()
        while block := os.read(fd, min(CHUNK, maximum-len(raw)+1)):
            raw.extend(block); require(len(raw) <= maximum, 'adapter metadata exceeded read bound')
        directory.matches(name, fd)
        require(identity(os.fstat(fd)) == observed and len(raw) == s.st_size,
                'adapter metadata changed or lacks complete EOF')
        return bytes(raw), observed
    finally: os.close(fd)


@contextmanager
def _leaf(root, key, create=False):
    parts = key.split('/'); opened = []; parent = root
    try:
        for part in parts[:-1]:
            root.check()
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent.fd); os.fsync(parent.fd)
                except FileExistsError:
                    pass
            parent = Directory(parent.path / part); opened.append(parent)
        yield parent, parts[-1]
    finally:
        for d in reversed(opened):
            d.close()


def _atomic(root, key, raw, tick):
    """Anchored no-overwrite publication; failed partial temp bytes are removed."""
    with _leaf(root, key, True) as (parent, name):
        parent.check(); temporary = '.pending-' + uuid.uuid4().hex
        fd = parent.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            view = memoryview(raw)
            while view:
                tick(); n = os.write(fd, view[:CHUNK]); require(n > 0, 'adapter short write'); view = view[n:]
            os.fsync(fd); parent.matches(temporary, fd); tick()
            actual, _ = _load(parent, temporary, len(raw))
            require(actual == raw, 'adapter staged full bytes differ')
            try:
                os.link(temporary, name, src_dir_fd=parent.fd, dst_dir_fd=parent.fd, follow_symlinks=False)
            except FileExistsError:
                existing, _ = _load(parent, name, len(raw))
                require(existing == raw, 'immutable archive collision')
            tick(); parent.matches(temporary, fd)
        finally:
            try:
                parent.matches(temporary, fd)
                os.unlink(temporary, dir_fd=parent.fd); os.fsync(parent.fd)
            finally:
                os.close(fd)
        final, observed = _load(parent, name, len(raw))
        require(final == raw, 'adapter committed full bytes differ')
        return observed


class _Base:
    kind = 'external-persistent'

    def __init__(self, config_path, config_sha256, enrollment_raw, job_sha256, *, cancel=None):
        self.closed = False; self.cancel = cancel or (lambda: False)
        p = Path(config_path)
        require(p.is_absolute() and not any(x in ('.', '..') for x in p.parts), 'adapter config must be absolute')
        self.config_directory = Directory(p.parent); self.config_name = p.name
        self.config_raw, self.config_observed = _load(self.config_directory, p.name)
        require(SHA.fullmatch(config_sha256) and digest(self.config_raw) == config_sha256, 'adapter config SHA differs')
        self.config = config = parse(self.config_raw)
        require(isinstance(config, dict) and config.get('schema') == 'dams-compute-persistent-adapter-v1', 'adapter config schema differs')
        require(set(config) == COMMON | (CAS_FIELDS if config.get('kind') == 'external-cas' else GCS_FIELDS), 'adapter config fields differ')
        require(config['source_sha256'] == IMPORT_SHA and SHA.fullmatch(job_sha256)
                and config['job_sha256'] == job_sha256, 'adapter source/job differs')
        self._enrollment = bytes(enrollment_raw); e = parse(self._enrollment); b = config['binding']
        require(isinstance(b, dict) and set(b) == {'backup_id', 'domain', 'locator', 'enrollment_sha256'}, 'adapter binding shape differs')
        require(all(isinstance(v, str) and 0 < len(v) <= 4096 for v in b.values())
                and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,127}', b['backup_id'])
                and digest(self._enrollment) == b['enrollment_sha256'], 'adapter enrollment bytes differ')
        require(set(e) == {'schema', 'job_sha256', 'backup_id', 'domain', 'locator', 'kind'}
                and e['schema'] == 'dams-compute-persistent-backup-enrollment-v1'
                and e['kind'] == self.kind and e['job_sha256'] == job_sha256
                and all(e[k] == b[k] for k in ('backup_id', 'domain', 'locator')), 'adapter enrollment/job/locator differs')
        self._binding_raw = blob(b)
        require(type(config['max_object_bytes']) is int and 0 < config['max_object_bytes'] <= 64 * 1024**2, 'adapter object bound differs')
        require(type(config['timeout_seconds']) in (int, float) and math.isfinite(config['timeout_seconds'])
                and 0 < config['timeout_seconds'] <= 300, 'adapter operation deadline differs')
        require(type(config['max_retries']) is int and 0 <= config['max_retries'] <= 2, 'adapter retry bound differs')
        self.state = Directory(config['state_root'])
        require(list(identity(os.fstat(self.state.fd))[:2]) == config['state_identity'], 'adapter durable state anchor differs')
        require(self.state.path != p.parent and self.state.path not in p.parents, 'adapter state overlaps enrolled config')
        self.state_raw = blob({'schema':'dams-compute-adapter-state-binding-v1','config_sha256':config_sha256,
                              'job_sha256':job_sha256,'binding':b})
        _atomic(self.state, 'binding.json', self.state_raw, self._local_check)

    @property
    def binding(self):
        return parse(self._binding_raw)

    @property
    def enrollment_raw(self):
        return self._enrollment

    def _local_check(self):
        require(not self.closed and not self.cancel(), 'adapter closed or cancelled')
        require(digest(Path(__file__).read_bytes()) == IMPORT_SHA == self.config['source_sha256'], 'adapter source changed')
        require(_load(self.config_directory, self.config_name) == (self.config_raw, self.config_observed), 'adapter config drift')
        require(blob(self.config) == blob(parse(self.config_raw)), 'adapter loaded config drift')
        require(blob(self.binding) == self._binding_raw and digest(self.enrollment_raw) == self.binding['enrollment_sha256'], 'adapter enrollment drift')
        self.state.check()

    def _check_state(self):
        self._local_check()
        require(_load(self.state, 'binding.json')[0] == self.state_raw, 'adapter durable binding drift')

    def _deadline(self):
        return time.monotonic() + self.config['timeout_seconds']

    def _tick(self, deadline):
        self.check(); require(time.monotonic() < deadline, 'adapter deadline exceeded')

    def _pin(self, key):
        raw, _ = _load(self.state, digest(key.encode()) + '.json')
        row = parse(raw)
        require(set(row) == {'schema','key','bytes','sha256','generation'} and row['schema'] == 'dams-compute-object-pin-v1'
                and row['key'] == key and type(row['bytes']) is int and 0 <= row['bytes'] <= self.config['max_object_bytes']
                and SHA.fullmatch(row['sha256']), 'adapter object pin differs')
        return row

    def _save_pin(self, key, size, sha, generation, deadline):
        row = {'schema':'dams-compute-object-pin-v1','key':key,'bytes':size,'sha256':sha,'generation':generation}
        _atomic(self.state, digest(key.encode()) + '.json', blob(row), lambda: self._tick(deadline))
        return row

    def _raw(self, key, raw):
        key_name(key); require(isinstance(raw, bytes) and len(raw) <= self.config['max_object_bytes'], 'adapter payload bound/type differs')
        if key.startswith('objects/'):
            require(key[8:-2] == digest(raw), 'CAS key SHA differs from full bytes')

    def close(self):
        if not getattr(self, 'closed', False):
            self.closed = True
            for name in ('state','config_directory'):
                if hasattr(self, name): getattr(self, name).close()


class ExternalCASBackend(_Base):
    """A root-enrolled mounted volume, never an assertion of off-host backup."""
    @_construction
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        c = self.config
        require(c['kind'] == 'external-cas' and c['max_retries'] == 0, 'CAS kind/retries differ')
        self.volume = Directory(c['volume']); self.root = Directory(c['root'])
        require(os.path.ismount(self.volume.path) and list(identity(os.fstat(self.volume.fd))[:2]) == c['volume_identity'], 'actual mounted volume anchor differs')
        require(list(identity(os.fstat(self.root.fd))[:2]) == c['root_identity']
                and self.volume.path in self.root.path.parents and os.fstat(self.root.fd).st_dev == os.fstat(self.volume.fd).st_dev, 'CAS root left actual volume')
        require(self.root.path != self.state.path and self.root.path not in self.state.path.parents
                and self.state.path not in self.root.path.parents, 'CAS/state overlap')
        require(c['binding']['locator'] == str(self.root.path)
                and c['binding']['domain'] == 'filesystem-device:' + str(os.fstat(self.root.fd).st_dev), 'CAS locator/actual device domain differs')
        require(type(c['min_free_bytes']) is int and c['min_free_bytes'] >= 0, 'CAS free floor differs')
        self.check()

    def check(self):
        self._check_state(); self.volume.check(); self.root.check()
        require(os.path.ismount(self.volume.path), 'CAS volume unmounted')

    def publish(self, key, raw):
        self._raw(key, raw); deadline = self._deadline(); self._tick(deadline)
        require(shutil.disk_usage(self.root.path).free >= self.config['min_free_bytes'] + len(raw) + CHUNK, 'CAS publication would consume reserve')
        def tick():
            self._tick(deadline)
            require(shutil.disk_usage(self.root.path).free >= self.config['min_free_bytes'], 'CAS live free floor violated')
        observed = _atomic(self.root, key, raw, tick)
        self._save_pin(key, len(raw), digest(raw), list(observed), deadline)

    def read(self, key):
        key_name(key); self.check(); pin = self._pin(key); deadline = self._deadline()
        require(isinstance(pin['generation'], list) and len(pin['generation']) == 5, 'CAS object anchor differs')
        owner = self
        with _leaf(self.root, key) as (parent, name):
            # The stream owns fresh no-follow anchors, not this context's FDs.
            stream_parent = Directory(parent.path)
        fd = None
        try:
            fd = stream_parent.open(name, os.O_RDONLY | os.O_NONBLOCK)
            require(list(identity(os.fstat(fd))) == pin['generation'] and os.fstat(fd).st_nlink == 1, 'CAS immutable object identity differs')
        except BaseException:
            if fd is not None: os.close(fd)
            stream_parent.close(); raise
        class Stream:
            def __init__(self): self.total = 0; self.sha = hashlib.sha256(); self.eof = False; self.closed = False; self.failed = False
            def read(self, count):
                try:
                    require(type(count) is int and 0 < count <= owner.config['max_object_bytes'] + 1 and not self.closed, 'CAS stream bounded positive read required')
                    owner._tick(deadline); stream_parent.matches(name, fd)
                    require(list(identity(os.fstat(fd))) == pin['generation'], 'CAS stream changed')
                    data = os.read(fd, min(count, CHUNK)); self.total += len(data); self.sha.update(data)
                    require(self.total <= pin['bytes'], 'CAS object grew')
                    if not data:
                        require(self.total == pin['bytes'] and self.sha.hexdigest() == pin['sha256'], 'CAS object truncated/fullSHA differs'); self.eof = True
                    return data
                except BaseException:
                    self.failed = True; raise
            def close(self):
                if self.closed: return
                self.closed = True
                try:
                    if not self.failed:
                        owner._tick(deadline); stream_parent.matches(name, fd)
                        require(self.eof and list(identity(os.fstat(fd))) == pin['generation'], 'CAS stream closed before normal EOF or drifted')
                finally: os.close(fd); stream_parent.close()
        return Stream()

    def close(self):
        if not getattr(self, 'closed', False):
            for name in ('root','volume'):
                if hasattr(self, name): getattr(self, name).close()
        super().close()


class GoogleHTTPTransport:
    """TLS to one fixed Google host, no redirect, retry or credential logging."""
    def request(self, method, path, body, headers, timeout):
        require(path.startswith(('/storage/v1/', '/upload/storage/v1/')) and not any(x in path for x in ('\r','\n','://')), 'unapproved Google request path')
        connection = http.client.HTTPSConnection('storage.googleapis.com', timeout=timeout)
        end = time.monotonic() + timeout
        try:
            connection.request(method, path, body=body, headers=headers)
            require(time.monotonic() < end, 'Google request absolute deadline exceeded')
            if connection.sock is not None: connection.sock.settimeout(end-time.monotonic())
            response = connection.getresponse()
        except BaseException:
            connection.close(); raise Refusal('Google TLS transport failed') from None
        class Response:
            status = response.status
            headers = {k.lower():v for k,v in response.getheaders()}
            def read(self, count):
                remaining = end-time.monotonic()
                require(remaining > 0, 'Google response absolute deadline exceeded')
                if connection.sock is not None: connection.sock.settimeout(remaining)
                return response.read(count)
            def close(self):
                try: response.close()
                finally: connection.close()
        return Response()


class GCSBackend(_Base):
    """Generation-bound create-only GCS objects; actual admission is root-owned."""
    @_construction
    def __init__(self, *args, token_provider, meter, transport=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.token_provider = token_provider; self.meter = meter
        self.transport = transport or GoogleHTTPTransport(); self.admitted = False
        c = self.config
        require(c['kind'] == 'gcs' and re.fullmatch('[a-z0-9][a-z0-9.-]{1,220}[a-z0-9]', c['bucket'])
                and '..' not in c['bucket'], 'fixed GCS bucket differs')
        require(re.fullmatch('[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', c['prefix']) and len(c['prefix']) <= 512, 'fixed GCS prefix differs')
        require(c['binding']['locator'] == f"gs://{c['bucket']}/{c['prefix']}"
                and c['binding']['domain'] == 'provider:google-cloud-storage', 'GCS locator/provider domain differs')
        require(type(c['expected_versioning']) is bool and c['expected_ubla'] is True
                and c['expected_public_access_prevention'] == 'enforced'
                and type(c['expected_soft_delete_seconds']) is int and 0 <= c['expected_soft_delete_seconds'] <= 90*86400
                and re.fullmatch('[A-Z0-9-]+', c['expected_location']), 'root GCS persistence/privacy policy differs')
        require(callable(token_provider) and callable(getattr(meter,'before',None)) and callable(getattr(meter,'after',None)), 'durable original-ledger request meter required')

    def check(self):
        self._check_state(); require(self.admitted, 'fresh actual GCS policy admission required')

    def _path(self, key, **query):
        name = self.config['prefix'] + '/' + key_name(key)
        path = '/storage/v1/b/' + quote(self.config['bucket'], safe='') + '/o/' + quote(name, safe='')
        return path + ('?' + urlencode(query) if query else '')

    def _request(self, method, path, body, response_limit, deadline):
        self._local_check(); require(time.monotonic() < deadline, 'adapter deadline exceeded')
        token = None
        try:
            token = self.token_provider()
            require(isinstance(token, str) and 0 < len(token) <= 16384 and not any(ord(c) <= 32 or ord(c) >= 127 for c in token), 'invalid memory-only credential')
        except BaseException:
            raise Refusal('memory-only credential unavailable') from None
        event = {'schema':'dams-adapter-http-attempt-v1','backend_id':self.binding['backup_id'],
                 'method':method,'path':path,'request_body_upper_bytes':len(body) if body else 0,
                 'request_body_sha256':digest(body) if body is not None else None,
                 'response_body_limit_bytes':response_limit}
        ticket = None
        try:
            ticket = self.meter.before(event)  # Must preserve original ledger; no default/reset implementation.
            require(time.monotonic() < deadline and not self.cancel(), 'adapter request deadline/cancel reached')
            response = self.transport.request(method, path, body,
                {'Authorization':'Bearer '+token,'Content-Type':'application/octet-stream','Accept-Encoding':'identity'},
                min(self.config['timeout_seconds'], deadline-time.monotonic()))
        except BaseException:
            if ticket is not None:
                self.meter.after(ticket, {'status':None,'response_body_bytes':0,'normal_eof':False,'failed':True})
            raise Refusal('bounded Google request failed') from None
        finally:
            token = None
        owner = self
        class Stream:
            status = response.status
            headers = {str(k).lower():str(v) for k,v in response.headers.items()}
            def __init__(self): self.total = 0; self.eof = False; self.closed = False; self.failed = False
            def read(self, count):
                try:
                    require(type(count) is int and 0 < count <= response_limit + 1 and not self.closed, 'bounded Google positive read required')
                    owner._local_check(); require(time.monotonic() < deadline, 'adapter deadline exceeded')
                    try: data = response.read(min(count,CHUNK))
                    except BaseException: raise Refusal('bounded Google response failed') from None
                    require(isinstance(data,bytes) and len(data) <= min(count,CHUNK), 'Google response violated binary read bound')
                    self.total += len(data); require(self.total <= response_limit, 'Google response exceeded exact bound')
                    if not data: self.eof = True
                    owner._local_check(); require(time.monotonic() < deadline, 'adapter deadline exceeded')
                    return data
                except BaseException:
                    self.failed = True; raise
            def close(self):
                if self.closed:return
                self.closed = True
                prior_failure = self.failed; close_failure = False
                try: response.close()
                except BaseException: close_failure = True
                finally: owner.meter.after(ticket, {'status':self.status,'response_body_bytes':self.total,'normal_eof':self.eof,
                                                   'failed':self.failed or close_failure or not self.eof or not 200 <= self.status < 300})
                if close_failure and not prior_failure: raise Refusal('bounded Google response close failed') from None
        return Stream()

    def _full(self, method, path, body, limit, deadline):
        for attempt in range(self.config['max_retries'] + 1):
            response = self._request(method,path,body,limit,deadline)
            try:
                raw = bytearray()
                while block := response.read(min(CHUNK, limit-len(raw)+1)):raw.extend(block)
                status, headers = response.status, response.headers
            finally: response.close()
            if status not in (408,429,500,502,503,504) or attempt == self.config['max_retries']:
                return status, bytes(raw), headers
            require(time.monotonic() < deadline and not self.cancel(), 'adapter retry deadline/cancel reached')
        raise Refusal('bounded Google retries exhausted')

    def admit(self):
        """Fresh actual GET of bucket configuration and IAM; never enables policy."""
        self.admitted = False
        self._local_check(); deadline = self._deadline(); bucket = quote(self.config['bucket'],safe='')
        status, raw, _ = self._full('GET','/storage/v1/b/'+bucket,None,MAX_POLICY,deadline)
        require(status == 200, 'GCS bucket admission GET refused'); policy = parse(raw); c = self.config
        require(policy.get('name') == c['bucket'] and policy.get('location') == c['expected_location']
                and policy.get('versioning',{}).get('enabled',False) is c['expected_versioning']
                and policy.get('iamConfiguration',{}).get('uniformBucketLevelAccess',{}).get('enabled') is True
                and policy.get('iamConfiguration',{}).get('publicAccessPrevention') == 'enforced'
                and policy.get('softDeletePolicy',{}).get('retentionDurationSeconds') == str(c['expected_soft_delete_seconds']), 'actual GCS policy differs from root pin')
        status, iam, _ = self._full('GET','/storage/v1/b/'+bucket+'/iam',None,MAX_POLICY,deadline)
        require(status == 200, 'GCS IAM admission GET refused'); access = parse(iam)
        require(isinstance(access.get('bindings',[]),list) and all(isinstance(b,dict) and isinstance(b.get('members'),list)
                and all(isinstance(m,str) and m not in ('allUsers','allAuthenticatedUsers') for m in b['members']) for b in access.get('bindings',[])), 'GCS public or malformed IAM refused')
        self.policy_receipt = {'bucket_policy_sha256':digest(raw),'IAM_policy_sha256':digest(iam),'actual_policy_reads':2,
                               'remote_policy_is_not_rechecked_per_codec_block':True,'provider_correlation':'Google provider shared risk'}
        self.admitted = True; self.check(); return dict(self.policy_receipt)

    def _metadata(self, key, raw, deadline):
        status, data, _ = self._full('GET',self._path(key),None,MAX_POLICY,deadline)
        require(status == 200, 'GCS immutable collision metadata GET failed'); m = parse(data)
        return self._generation(key, raw, m)

    def _generation(self, key, raw, m):
        require(isinstance(m,dict) and m.get('bucket') == self.config['bucket']
                and m.get('name') == self.config['prefix']+'/'+key and m.get('size') == str(len(raw))
                and m.get('contentType') == 'application/octet-stream' and m.get('contentEncoding') is None
                and isinstance(m.get('generation'),str) and re.fullmatch('[1-9][0-9]{0,39}',m['generation']), 'GCS immutable object metadata differs')
        return m['generation']

    def _binary(self, key, pin, deadline):
        response = self._request('GET',self._path(key,alt='media',generation=pin['generation'],ifGenerationMatch=pin['generation']),None,pin['bytes'],deadline)
        try:
            require(response.status == 200 and response.headers.get('x-goog-generation') == pin['generation']
                    and response.headers.get('content-length') == str(pin['bytes'])
                    and response.headers.get('content-encoding','identity') == 'identity', 'GCS exact generation/media headers differ')
        except BaseException:
            response.close(); raise
        owner = self
        class Stream:
            def __init__(self):self.size=0;self.sha=hashlib.sha256();self.eof=False;self.closed=False;self.failed=False
            def read(self,count):
                try:
                    require(type(count) is int and count > 0 and not self.closed, 'GCS positive bounded read required')
                    owner._tick(deadline); data=response.read(min(count,CHUNK,pin['bytes']-self.size+1));self.size+=len(data);self.sha.update(data)
                    if not data:
                        require(self.size == pin['bytes'] and self.sha.hexdigest() == pin['sha256'], 'GCS complete media SHA/bytes differ');self.eof=True
                    return data
                except BaseException:
                    self.failed=True;response.failed=True;raise
            def close(self):
                if self.closed:return
                self.closed=True
                response.failed = response.failed or self.failed
                try:
                    if not self.failed:
                        owner._tick(deadline);require(self.eof,'GCS media closed before normal verified EOF')
                finally:response.close()
        return Stream()

    def publish(self, key, raw):
        self._raw(key,raw); self.check(); deadline=self._deadline();name=self.config['prefix']+'/'+key
        path='/upload/storage/v1/b/'+quote(self.config['bucket'],safe='')+'/o?'+urlencode({'uploadType':'media','name':name,'ifGenerationMatch':'0'})
        status, data, _=self._full('POST',path,raw,MAX_POLICY,deadline)
        if status in (200,201):generation=self._generation(key,raw,parse(data))
        elif status == 412:generation=self._metadata(key,raw,deadline)
        else:raise Refusal('GCS create-only publication failed')
        pin={'schema':'dams-compute-object-pin-v1','key':key,'bytes':len(raw),'sha256':digest(raw),'generation':generation}
        try:old=self._pin(key)
        except FileNotFoundError:old=None
        require(old is None or old == pin,'GCS durable generation drift')
        stream=self._binary(key,pin,deadline);offset=0
        try:
            while block:=stream.read(CHUNK):require(block == raw[offset:offset+len(block)],'GCS collision full bytes differ');offset+=len(block)
        except BaseException:
            stream.failed=True;raise
        finally:stream.close()
        self._save_pin(key,len(raw),digest(raw),generation,deadline);self._tick(deadline)

    def read(self,key):
        key_name(key);self.check();pin=self._pin(key)
        require(isinstance(pin['generation'],str) and re.fullmatch('[1-9][0-9]{0,39}',pin['generation']),'GCS pinned generation differs')
        return self._binary(key,pin,self._deadline())


def admit_pair(backends):
    """Root deployment must use this pair fence before passing into Collector.

    Two filesystem dirs/volumes never attest independent hardware here. This
    minimum implementation admits precisely one enrolled CAS and one GCS.
    """
    require(isinstance(backends,(list,tuple)) and len(backends)==2
            and {type(b) for b in backends}=={ExternalCASBackend,GCSBackend}, 'root pair requires one CAS and one GCS transport')
    for backend in backends:backend.check()
    require(len({b.binding['backup_id'] for b in backends})==2
            and len({b.binding['locator'] for b in backends})==2
            and len({b.binding['domain'] for b in backends})==2, 'persistent pair shares backup identity/locator/fault domain')
    return tuple(backends)
