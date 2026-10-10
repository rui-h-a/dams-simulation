"""Explicit root-owned archive adapter wiring; no provisioning or new allowance.

All config/source/job refs are retained by the existing Entry. Construction is
deferred until Collector has admitted its assignment and original state. Only
then can the enrolled GCS policy reads acquire a memory-only existing token.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import re
import subprocess
import time

from dams_sim.storage import canonical, digest, source_hash
from dams_sim.longitudinal_pipeline import driver_hash
from research_tools.compute_only_control import blob, parse, require, ARCHIVE_COLLECTOR_SCHEMA
from research_tools.compute_only_archive import PROFILE_FIELDS, PROFILE_SCHEMA, JOB_FIELDS, IMPORT_SHA as ARCHIVE_SHA
from research_tools import compute_only_persistent_backends as pb
from research_tools import persistent_backend_budget_meter as bm
from research_tools.compute_only_transport import ProcessStream, utc

SCHEMA = 'dams-compute-archive-backend-factory-v1'
DEPENDENCIES = {'research_tools/compute_only_backend_factory.py',
                'research_tools/compute_only_persistent_backends.py',
                'research_tools/persistent_backend_budget_meter.py'}
IMPORT_SHA = digest(Path(__file__).read_bytes())
FIELDS = {'schema', 'storage_profile', 'meter', 'minimum_meter', 'backends', 'auth'}
AUTH_FIELDS = {'configuration', 'account', 'project', 'min_expiry_seconds', 'timeout_seconds'}


class TokenProvider:
    """Existing named gcloud credentials only; bounded/reaped and never saved."""
    def __init__(self, options, *, deadline, check, popen=subprocess.Popen):
        self.o = dict(options); self.deadline = deadline; self.check = check; self.popen = popen
        self.token = None; self.expiry = 0

    def __call__(self):
        self.check(); now = time.time()
        require(now < self.deadline, 'archive authentication original deadline')
        if self.token is not None and self.expiry - now >= self.o['min_expiry_seconds']:
            return self.token
        argv = ['gcloud', 'config', 'config-helper', '--format=json',
                '--min-expiry=' + str(self.o['min_expiry_seconds']) + 's',
                '--configuration=' + self.o['configuration'], '--account=' + self.o['account'],
                '--project=' + self.o['project']]
        output = bytearray(); observations = []
        try:
            stream = ProcessStream(argv, bound=65536, stderr_bound=65536,
                    cutoff=time.monotonic()+min(self.o['timeout_seconds'], self.deadline-now),
                    check=self.check, completed=observations.append, popen=self.popen)
            try:
                while part := stream.read(65536): output.extend(part)
            finally: stream.close()
            require(observations and observations[-1]['status'] == 'accepted-stream', 'authentication process not reaped')
            value = parse(bytes(output)); credentials = value['credential']
            properties = value['configuration']['properties']['core']
            require(properties['account'] == self.o['account'] and properties['project'] == self.o['project'],
                    'existing authentication account/project differs')
            token = credentials['access_token']; expiry = utc(credentials['token_expiry'])
            require(isinstance(token, str) and 0 < len(token) <= 16384
                    and all(32 < ord(c) < 127 for c in token)
                    and expiry-time.time() >= self.o['min_expiry_seconds'], 'existing token expiry/shape differs')
            self.check(); self.token = token; self.expiry = expiry
            return token
        except BaseException:
            self.token = None; self.expiry = 0
            raise ValueError('archive existing credential unavailable') from None
        finally:
            output.clear()

    def close(self): self.token = None; self.expiry = 0


class Factory:
    """One lazy materialization over exact refs; immutable failures are retained."""
    def __init__(self, entry, ref, admission_ref, *, popen=subprocess.Popen, transport=None):
        self.entry = entry; self.stack = ExitStack(); self.backends = None; self.meter = None
        self.popen = popen; self.transport = transport; self.closed = False
        try:
            self.c = c = parse(entry.reference(ref))
            require(isinstance(c, dict) and set(c) == FIELDS and c['schema'] == SCHEMA, 'archive factory exact fields differ')
            pins = entry.options['component_sha256']
            expected = {'research_tools/compute_only_backend_factory.py': IMPORT_SHA,
                        'research_tools/compute_only_persistent_backends.py': pb.IMPORT_SHA,
                        'research_tools/persistent_backend_budget_meter.py': digest(Path(bm.__file__).read_bytes())}
            modules = {'research_tools/compute_only_backend_factory.py': __file__,
                       'research_tools/compute_only_persistent_backends.py': pb.__file__,
                       'research_tools/persistent_backend_budget_meter.py': bm.__file__}
            require(all(pins.get(k) == sha and (entry.root.path/k).absolute() == Path(modules[k]).absolute()
                        for k, sha in expected.items()),
                    'archive factory imports differ from admitted package')
            admission_raw = entry.reference(admission_ref); self.admission = a = parse(admission_raw)
            require(a.get('schema') == ARCHIVE_COLLECTOR_SCHEMA and a.get('storage_profile') == c['storage_profile'],
                    'archive factory needs the exact archive Collector profile')
            profile = self.profile = parse(entry.reference(c['storage_profile']))
            require(isinstance(profile, dict) and set(profile) == PROFILE_FIELDS
                    and profile['schema'] == PROFILE_SCHEMA and profile['mode'] == 'external-persistent'
                    and profile['helper_sha256'] == ARCHIVE_SHA
                    and profile['collector_binding_sha256'] == digest(canonical({k:v for k,v in a.items() if k!='storage_profile'})),
                    'archive factory profile/admission differs')
            job = self.job = profile['job']
            require(isinstance(job, dict) and set(job) == JOB_FIELDS
                    and job.get('stage_id') == a.get('stage_id') == entry.stage['id']
                    and all(job.get(k) == a.get(k) == entry.stage.get(k) for k in ('spec_sha256','inventory_sha256','assignment_sha256'))
                    and job['source_sha256'] == a['source_sha256'] == source_hash()
                    and job['pipeline_driver_sha256'] == a['pipeline_driver_sha256'] == driver_hash(),
                    'archive factory job/source/spec/assignment differs')
            require(all(isinstance(job[k],str) and re.fullmatch('[0-9a-f]{64}',job[k]) for k in JOB_FIELDS-{'stage_id'}), 'archive job SHA differs')
            self.job_sha = digest(canonical(job))
            require(utc(a['deadline_utc']) <= entry.termination.timestamp() <= utc(entry.c['global_deadline_utc']), 'archive factory deadline extends node')
            self.deadline = utc(a['deadline_utc'])
            if entry.options['phase']=='collect':
                transport = parse(entry.reference(entry.options['collect']['transport']))
                require(transport['remote_runtime_sha256']==job['runtime_sha256']
                        and transport['provider_identity_sha256']==job['provider_identity_sha256'], 'archive enrolled transport runtime/provider differs')
            else:
                operation = parse(entry.reference(entry.options['lifecycle']))
                require(operation.get('runtime') is not None, 'archive lifecycle needs an actual sealed runtime; no fabricated launch identity')
                runtime = parse(entry.reference(operation['runtime']))
                require(digest(blob(runtime))==job['runtime_sha256']
                        and runtime['provider_identity_sha256']==job['provider_identity_sha256'], 'archive sealed runtime/provider differs')
            auth = c['auth']
            require(isinstance(auth,dict) and set(auth)==AUTH_FIELDS
                    and all(auth[k] == entry.c[v] and isinstance(auth[k],str) and 0 < len(auth[k]) <= 256
                            for k,v in [('configuration','gcloud_configuration'),('account','gcloud_account'),('project','project')])
                    and type(auth['min_expiry_seconds']) is int and 300 <= auth['min_expiry_seconds'] <= 3600
                    and type(auth['timeout_seconds']) is int and 1 <= auth['timeout_seconds'] <= 120,
                    'archive existing credential selection differs')
            require(isinstance(c['backends'],list) and len(c['backends'])==2
                    and len(profile['backup_bindings'])==2, 'archive factory requires two enrolled adapters')
            self.adapter_inputs = []
            for item, binding in zip(c['backends'],profile['backup_bindings']):
                require(isinstance(item,dict) and set(item)=={'config','enrollment'}, 'archive adapter reference fields differ')
                config = parse(entry.reference(item['config'])); enrollment_raw = entry.reference(item['enrollment']); enrollment = parse(enrollment_raw)
                kind = config.get('kind')
                require(kind in ('external-cas','gcs') and set(config)==pb.COMMON | (pb.CAS_FIELDS if kind=='external-cas' else pb.GCS_FIELDS)
                        and config['schema']=='dams-compute-persistent-adapter-v1' and config['source_sha256']==pb.IMPORT_SHA
                        and config['job_sha256']==self.job_sha and config['binding']==binding
                        and digest(enrollment_raw)==binding['enrollment_sha256']
                        and enrollment=={'schema':'dams-compute-persistent-backup-enrollment-v1','kind':'external-persistent',
                                         'job_sha256':self.job_sha,**{k:binding[k] for k in ('backup_id','domain','locator')}},
                        'archive adapter/enrollment/source/job differs')
                self.adapter_inputs.append((item,config,enrollment_raw))
            bindings = profile['backup_bindings']
            require({v[1]['kind'] for v in self.adapter_inputs}=={'external-cas','gcs'}
                    and all(len({b[k] for b in bindings})==2 for k in ('backup_id','domain','locator')), 'archive pair shares fault domain or transport')
            self.meter_config = m = parse(entry.reference(c['meter']))
            require(m.get('source_job') is not None and parse(entry.reference(m['source_job']))==job
                    and m['source_manifest']==entry.options['package_manifest']
                    and m['global_deadline_utc']==entry.c['global_deadline_utc'], 'archive meter source/job/original deadline differs')
            require(type(profile['max_archive_encoded_bytes']) is int
                    and type(m['limits']['max_retained_encoded_bytes']) is int
                    and 0 < profile['max_archive_encoded_bytes'] <= m['limits']['max_retained_encoded_bytes'],
                    'archive profile exceeds original retention allocation')
            for field in ('current_budget','original_budget'): entry.reference(m[field])
            require(isinstance(m['source_files'],list), 'archive meter source pins missing')
            for source_ref in m['source_files']: entry.reference(source_ref)
            required_source = {str((entry.root.path/n).absolute()):sha for n,sha in expected.items()}
            require(all(any(r=={'path':p,'sha256':sha} for r in m['source_files']) for p,sha in required_source.items()), 'archive meter misses runtime source dependencies')
            self.minimum = None if c['minimum_meter'] is None else parse(entry.reference(c['minimum_meter']))
            entry.check(full=True)
        except BaseException:
            self.close(); raise

    def __call__(self):
        require(not self.closed and self.backends is None, 'archive factory already materialized/closed')
        try:
            require(self.entry.c.get('paid_actions_authorized') is True, 'archive factory requires explicit root operation authorization')
            self.entry.check(full=True)
            m = self.c['meter']; self.meter = bm.Meter(m['path'],config_sha256=m['sha256'])
            current = self.meter.snapshot(); self._floor(current)
            token = TokenProvider(self.c['auth'],deadline=self.deadline,check=self.entry.check,popen=self.popen)
            self.stack.callback(token.close); built = []
            for item, config, enrolled in self.adapter_inputs:
                args = (item['config']['path'],item['config']['sha256'],enrolled,self.job_sha)
                options = {'cancel':lambda:self.closed or self._cancelled()}
                backend = (pb.ExternalCASBackend(*args,**options) if config['kind']=='external-cas'
                           else pb.GCSBackend(*args,token_provider=token,meter=self.meter,transport=self.transport,**options))
                built.append(backend); self.stack.callback(backend.close)
            # Reject pair type/domain before any policy/authentication request.
            for backend in built:
                if type(backend) is pb.GCSBackend: backend.admit()
            self.backends = pb.admit_pair(built); self.entry.check(full=True)
            return self.backends
        except BaseException:
            self.close(); raise

    def _cancelled(self):
        self.entry.check(); return time.time() >= self.deadline

    def _floor(self, current):
        minimum = self.minimum
        if minimum is None:
            require(current['requests']==0, 'existing archive meter requires retained minimum')
            return
        require(isinstance(minimum,dict) and set(minimum)==set(current)
                and all(type(v) is type(current[k]) for k,v in minimum.items()), 'archive retained meter shape differs')
        growing = {'requests','uploaded_body_bytes_upper','downloaded_body_bytes_upper','completed_attempts','pending_attempts','held_cost_usd_upper'}
        require(all(current[k]==v for k,v in minimum.items() if k not in growing), 'archive retained meter binding differs')
        for key in growing-{'held_cost_usd_upper','pending_attempts'}:
            require(type(minimum[key]) is int and minimum[key]>=0 and current[key]>=minimum[key], 'archive meter rollback')
        require(type(minimum['pending_attempts']) is int and minimum['pending_attempts']>=0
                and minimum['requests']==minimum['completed_attempts']+minimum['pending_attempts']
                and Decimal(current['held_cost_usd_upper'])>=Decimal(minimum['held_cost_usd_upper']), 'archive retained cost/count differs')

    def snapshot(self):
        require(self.meter is not None, 'archive factory meter not opened')
        self.entry.check(); result=self.meter.snapshot(); self.entry.check(); return result

    def close(self):
        if not self.closed: self.closed=True; self.stack.close()
