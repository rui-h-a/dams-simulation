"""Offline public-hostkey startup controls; never run the startup script."""
import ast
import base64
import contextlib
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent.parent
SCRIPT = HERE / 'source/cloud/compute-only-wait-source.sh'
if not SCRIPT.exists():
    SCRIPT = HERE / 'cloud/compute-only-wait-source.sh'
RAW_SCRIPT = SCRIPT.read_bytes()
START = b"/usr/bin/python3 -I -B <<'DAMS_PUBLIC_HOSTKEY_PY'\n"
CODE = RAW_SCRIPT.split(START, 1)[1].split(b'\nDAMS_PUBLIC_HOSTKEY_PY\n', 1)[0]
NS = {'__name__': 'hostkey_offline_fixture'}
exec(compile(CODE, str(SCRIPT), 'exec'), NS)

def field(raw):
    return len(raw).to_bytes(4, 'big') + raw

def wire(kind, *parts):
    return field(kind.encode()) + b''.join(field(p) for p in parts)

def public(kind='ssh-ed25519', payload=None, comment=b' fixture-public'):
    if payload is None:
        payload = {
            'ssh-ed25519': wire('ssh-ed25519', b'x'*32),
            'ecdsa-sha2-nistp256': wire('ecdsa-sha2-nistp256', b'nistp256', b'\x04'+b'x'*64),
            'ssh-rsa': wire('ssh-rsa', b'\x01\x00\x01', b'\x00\x80'+b'x'*255),
        }[kind]
    return kind.encode()+b' '+base64.b64encode(payload)+comment+b'\n'

class Response:
    def __init__(self, raw=b'', status=200, flavor='Google', extra=b''):
        self.raw, self.status, self.flavor, self.extra = raw, status, flavor, extra
        self.closed = False
    def getheader(self, name):
        return self.flavor if name == 'Metadata-Flavor' else None
    def read(self, size):
        if self.raw:
            raw, self.raw = self.raw[:size], self.raw[size:]
            return raw
        raw, self.extra = self.extra, b''
        return raw
    def close(self):
        self.closed = True

class Connection:
    def __init__(self, response, records, host, timeout):
        self.response, self.records, self.closed = response, records, False
        records.append({'host': host, 'timeout': timeout, 'connection': self})
    def request(self, method, path, *, body, headers):
        self.records[-1].update(method=method, path=path, body=body, headers=headers)
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True

class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='public-key-fixture-', dir=HERE))
        self.kind = 'ssh-ed25519'
        self.path = self.temp / 'host.pub'
        self.path.write_bytes(public()); self.path.chmod(0o644)
    def tearDown(self):
        shutil.rmtree(self.temp)
    def read(self, **kwargs):
        return NS['read_public'](self.path, self.kind, expected_owner=os.getuid(), **kwargs)
    def factory(self, response):
        records = []
        def create(host, timeout):
            return Connection(response, records, host, timeout)
        return create, records
    def test_shell_syntax_and_python_compilation(self):
        result = subprocess.run(['/bin/bash', '-n', str(SCRIPT)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RAW_SCRIPT.count(START), 1)
        compile(CODE, str(SCRIPT), 'exec'); ast.parse(CODE)
    def test_three_valid_wire_types_and_comment_never_published(self):
        for kind, _ in NS['KEYS']:
            value = NS['wire_public'](public(kind, comment=b' DO_NOT_PUBLISH_COMMENT'), kind)
            self.assertEqual(value, public(kind).split()[1])
            self.assertNotIn(b'DO_NOT_PUBLISH', value)
    def test_unknown_type_and_wire_type_mismatch(self):
        for raw, kind in [(public(), 'ssh-dss'), (public('ssh-rsa'), self.kind),
                          (public(payload=wire('ssh-rsa', b'x'*32)), self.kind)]:
            with self.assertRaises(Exception): NS['wire_public'](raw, kind)
    def test_private_key_multiline_and_malformed_base64_refuse(self):
        for raw in (b'-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET\n',
                    b'ssh-ed25519 !!!\n', public()+b'other-line\n', public().replace(b'\n', b'\r\n')):
            with self.assertRaises(Exception): NS['wire_public'](raw, self.kind)
    def test_noncanonical_base64_and_wire_tail_refuse(self):
        valid = public().split()[1]
        for raw in (self.kind.encode()+b' '+valid+b'=\n',
                    public(payload=wire(self.kind, b'x'*32)+b'tail'),
                    public(payload=wire(self.kind, b'x'*32)[:-1])):
            with self.assertRaises(Exception): NS['wire_public'](raw, self.kind)
    def test_algorithm_shape_refuses(self):
        bad = [('ssh-ed25519', wire('ssh-ed25519', b'x'*31)),
               ('ecdsa-sha2-nistp256', wire('ecdsa-sha2-nistp256', b'nistp384', b'\x04'+b'x'*64)),
               ('ecdsa-sha2-nistp256', wire('ecdsa-sha2-nistp256', b'nistp256', b'\x03'+b'x'*64)),
               ('ssh-rsa', wire('ssh-rsa', b'\x80', b'\x01')),
               ('ssh-rsa', wire('ssh-rsa', b'\x00\x01', b'\x01'))]
        for kind, blob in bad:
            with self.assertRaises(Exception): NS['wire_public'](public(kind, blob), kind)
    def test_regular_public_file_full_native_sha_and_only_pub_open(self):
        actual_open, opened = os.open, []
        def observe(path, flags, *args, **kwargs):
            opened.append(str(path)); return actual_open(path, flags, *args, **kwargs)
        with patch.object(NS['os'], 'open', observe): result = self.read()
        self.assertEqual(result['sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result['value'], public().split()[1])
        self.assertEqual([p for p in opened if p.endswith('.pub')], ['host.pub'])
    def test_public_leaf_symlink_and_hardlink_refuse(self):
        self.path.rename(self.temp / 'retained.pub'); self.path.symlink_to(self.temp / 'retained.pub')
        with self.assertRaises(Exception): self.read()
        self.path.unlink(); os.link(self.temp / 'retained.pub', self.path)
        with self.assertRaises(Exception): self.read()
    def test_symlink_ancestor_and_world_writable_parent_refuse(self):
        nested = self.temp / 'nested'; nested.mkdir(); (nested / 'host.pub').write_bytes(public())
        link = self.temp / 'alias'; link.symlink_to(nested, target_is_directory=True)
        with self.assertRaises(Exception): NS['read_public'](link / 'host.pub', self.kind, expected_owner=os.getuid())
        nested.chmod(0o777)
        with self.assertRaises(Exception): NS['read_public'](nested / 'host.pub', self.kind, expected_owner=os.getuid())
    def test_fifo_rejected_before_open_without_block(self):
        self.path.unlink(); os.mkfifo(self.path)
        with self.assertRaises(ValueError): self.read()
    def test_public_owner_mode_and_size_refuse(self):
        with self.assertRaises(ValueError): NS['read_public'](self.path, self.kind, expected_owner=os.getuid()+10000)
        self.path.chmod(0o666)
        with self.assertRaises(ValueError): self.read()
        self.path.chmod(0o644); self.path.write_bytes(b'x'*16385)
        with self.assertRaises(ValueError): self.read()
    def test_same_bytes_inode_swap_before_leaf_open_refuses(self):
        actual_open = os.open
        def replace(path, flags, *args, **kwargs):
            if str(path) == 'host.pub':
                self.path.rename(self.temp / 'retained.pub'); self.path.write_bytes(public()); self.path.chmod(0o644)
            return actual_open(path, flags, *args, **kwargs)
        with patch.object(NS['os'], 'open', replace):
            with self.assertRaises(ValueError): self.read()
        self.assertTrue((self.temp / 'retained.pub').exists())
    def test_put_get_exact_endpoint_public_body_and_resource_close(self):
        value = public().split()[1]
        for method, raw in [('PUT', b''), ('GET', value)]:
            response = Response(raw); factory, records = self.factory(response)
            NS['request'](self.kind, method, value, connection_factory=factory)
            row = records[0]
            self.assertEqual((row['host'], row['timeout']), ('169.254.169.254', 5))
            self.assertEqual(row['path'], NS['PREFIX']+self.kind)
            self.assertEqual(row['body'], value if method == 'PUT' else None)
            self.assertEqual(row['headers'], {'Metadata-Flavor':'Google','Content-Type':'text/plain'})
            self.assertTrue(response.closed and row['connection'].closed)
    def test_redirect_notfound_and_wrong_flavor_refuse_no_retry(self):
        for response in (Response(status=302), Response(status=404), Response(flavor='Other')):
            factory, records = self.factory(response)
            with self.assertRaises(ValueError): NS['request'](self.kind, 'GET', b'a', connection_factory=factory)
            self.assertEqual(len(records), 1)
            self.assertTrue(response.closed and records[0]['connection'].closed)
    def test_oversize_wrong_value_and_nonEOF_refuse(self):
        for method, response in [('PUT', Response(b'x'*1025)), ('GET',Response(b'wrong')),
                                 ('GET',Response(b'a', extra=b'x'))]:
            factory, records = self.factory(response)
            with self.assertRaises(ValueError): NS['request'](self.kind, method, b'a', connection_factory=factory)
            self.assertTrue(response.closed and records[0]['connection'].closed)
    def test_unknown_request_never_opens_connection(self):
        factory, records = self.factory(Response())
        for kind, method in [('ssh-dss','PUT'), (self.kind,'POST'), ('../../key','GET')]:
            with self.assertRaises(ValueError): NS['request'](kind, method, b'a', connection_factory=factory)
        self.assertEqual(records, [])
    def test_timeout_closes_connection_and_does_not_retry(self):
        factory, records = self.factory(Response())
        def timeout(host, timeout):
            connection = factory(host, timeout)
            connection.getresponse = lambda: (_ for _ in ()).throw(TimeoutError('fixture'))
            return connection
        with self.assertRaises(TimeoutError): NS['request'](self.kind, 'GET', b'a', connection_factory=timeout)
        self.assertEqual(len(records), 1); self.assertTrue(records[0]['connection'].closed)
    def test_six_finite_requests_with_checks_and_rate_pacing(self):
        keys = {kind: {'value':public(kind).split()[1]} for kind,_ in NS['KEYS']}
        records, sleeps, checks = [], [], []
        def factory(host, timeout):
            kind = NS['KEYS'][len(records)//2][0]
            response = Response(b'' if len(records)%2 == 0 else keys[kind]['value'])
            return Connection(response, records, host, timeout)
        NS['publish'](keys, lambda: checks.append(True), connection_factory=factory, sleep=sleeps.append)
        self.assertEqual([r['method'] for r in records], ['PUT','GET']*3)
        self.assertEqual(len(checks), 12); self.assertEqual(sleeps, [0.35]*6)
        self.assertTrue(all(r['connection'].closed for r in records))
    def test_drift_aborts_before_next_request(self):
        keys = {kind:{'value':b'a'} for kind,_ in NS['KEYS']}
        factory, records = self.factory(Response())
        n = []
        def check():
            n.append(True)
            if len(n) == 2: raise ValueError('changed native')
        with self.assertRaises(ValueError): NS['publish'](keys, check, connection_factory=factory, sleep=lambda _:None)
        self.assertEqual(len(records), 1)
    def main_fixture(self, missing=False, unsafe=False):
        reads, runs, publications, alarms = [], [], [], []
        def read(path, kind):
            reads.append(path)
            if unsafe: raise ValueError('unsafe public file')
            if missing and len(reads) == 1: raise FileNotFoundError(path)
            return {'value': public(kind).split()[1], 'identity': (1,), 'sha256':'f'*64}
        def publish(keys, check):
            check(); publications.append(keys)
        with patch.dict(NS, read_public=read, publish=publish), \
             patch.object(NS['os'],'geteuid',return_value=0), \
             patch.object(NS['signal'],'signal'), patch.object(NS['signal'],'alarm',side_effect=alarms.append), \
             patch.object(NS['subprocess'],'run',side_effect=lambda *a,**k:runs.append((a,k))):
            if unsafe:
                with self.assertRaises(ValueError): NS['main']()
            else: NS['main']()
        return reads, runs, publications, alarms
    def test_main_existing_public_keys_no_keygen_and_no_private_reads(self):
        reads, runs, publications, alarms = self.main_fixture()
        self.assertEqual(runs, []); self.assertEqual(len(publications), 1)
        self.assertTrue(all(p.endswith('.pub') for p in reads)); self.assertEqual(alarms, [90,0])
    def test_missing_key_runs_only_stock_A_bounded_suppressed(self):
        reads, runs, publications, alarms = self.main_fixture(missing=True)
        args, kwargs = runs[0]
        self.assertEqual(args[0], ['/usr/bin/ssh-keygen','-A']); self.assertEqual(len(runs),1)
        self.assertEqual(kwargs, {'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,
                                 'stderr':subprocess.DEVNULL,'timeout':20,'check':True})
        self.assertEqual(len(publications),1); self.assertTrue(all(p.endswith('.pub') for p in reads))
    def test_unsafe_public_key_never_keygen_or_publish(self):
        reads, runs, publications, alarms = self.main_fixture(unsafe=True)
        self.assertEqual(runs, []); self.assertEqual(publications, []); self.assertEqual(alarms,[90,0])
    def test_main_nonroot_refuses_without_reads_or_request(self):
        with patch.object(NS['os'],'geteuid',return_value=501), patch.dict(NS,read_public=lambda *_:self.fail('read')):
            with self.assertRaises(ValueError): NS['main']()
    def test_error_boundary_no_key_material_or_traceback_echo(self):
        tree = ast.parse(CODE)
        boundary = tree.body[-1].body[-1]
        code = compile(ast.Module(body=[boundary],type_ignores=[]), '<offline-safe-boundary>', 'exec')
        stdout, stderr = io.StringIO(), io.StringIO()
        def fail(): raise ValueError('PRIVATE_FIXTURE_SECRET_NOT_TO_ECHO')
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught: exec(code, {'main': fail})
        self.assertEqual(str(caught.exception),'DAMS_PUBLIC_HOSTKEY_PUBLISH_REFUSED')
        self.assertEqual(stdout.getvalue()+stderr.getvalue(), '')

if __name__ == '__main__':
    unittest.main(verbosity=2)
