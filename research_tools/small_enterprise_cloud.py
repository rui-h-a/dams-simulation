"""One source-bound operational arm of the unchanged small-enterprise study.

Only the existing Scheduler executes cases. The fixed confirmation roster remains
66 worlds; a pinned status snapshot selects an unstarted frozen-roster arm.
A versioned envelope can execute both exact arms sequentially in the same VM.
Only two complete original raw gates certify a pair; neither mode completes a study.
Neither mode redefines the study or authorizes paid resources.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime
import re
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from dams_sim.config import Config
from dams_sim.runtime import RuntimeLimits
from dams_sim.storage import canonical, digest, source_hash, atomic_json, file_digest
from dams_sim.longitudinal_pipeline import driver_hash, stopped_case_census
from dams_sim.scheduler import Scheduler
from dams_sim._committed_pages import Directory
from research_tools import small_enterprise as study

SCHEMA = 'DAMS-small-enterprise-cloud-confirmation-arm-2'
PAIR_SCHEMA = 'DAMS-small-enterprise-cloud-confirmation-pair-1'
PAIR_FIELDS = {'schema','source_sha256','pipeline_driver_sha256','study_sha256',
               'pilot_inventory_sha256','population','days','seed','selected_world',
               'status_snapshot_sha256','arm_profiles'}
FIELDS = {'schema','source_sha256','pipeline_driver_sha256','study_entry_sha256',
          'study_sha256','pilot_inventory_sha256','population','days','seed',
          'selected_world','selected_arm','selected_case_id','selected_config_sha256',
          'selected_phase','confirmation_roster_sha256',
          'confirmation_study_sha256','confirmation_world_count','confirmation_world_ids',
          'status_snapshot','status_snapshot_sha256'}
CORE = study.CORE
DRIVER = study.DRIVER
STUDY_IMPORT_SHA = '2732dc6ff3d73e9848c022522e02a744ca0e77b775d472227585e8229784494a'
ENV = 'DAMS_SMALL_ENTERPRISE_CLOUD_PROFILE'
SHA_ENV = 'DAMS_SMALL_ENTERPRISE_CLOUD_PROFILE_SHA256'

# Public scientific roster, copied from the existing frozen study. This is a
# source-only selector, never a new sample-size choice or paid-action grant.
CONFIRMATION_ROSTER_RAW = b'{"pilot_worlds_excluded":true,"requests":[{"endpoint":"work_per_present_member_workday","requested_worlds":16},{"endpoint":"decision_regret_per_decision","requested_worlds":16},{"endpoint":"net_resource_gain_per_present_member_workday","requested_worlds":16},{"endpoint":"closed_by_common_end","requested_worlds":66}],"status":"frozen","study_sha256":"bfcb565b0fd9c21ea3aa3a8a1fe8914b11246b422c4b0bc9bbac9332a4346f9a","world_count":66,"world_ids":[52000,52001,52002,52003,52004,52005,52006,52007,52008,52009,52010,52011,52012,52013,52014,52015,52016,52017,52018,52019,52020,52021,52022,52023,52024,52025,52026,52027,52028,52029,52030,52031,52032,52033,52034,52035,52036,52037,52038,52039,52040,52041,52042,52043,52044,52045,52046,52047,52048,52049,52050,52051,52052,52053,52054,52055,52056,52057,52058,52059,52060,52061,52062,52063,52064,52065]}\n'
CONFIRMATION_ROSTER_SHA = '4a20d2f1d9f2bea4ea1f8bed3982773252c9deae12609ac5a428901d812c79f2'
CONFIRMATION_STUDY_SHA = 'bfcb565b0fd9c21ea3aa3a8a1fe8914b11246b422c4b0bc9bbac9332a4346f9a'
SELECTED_ARMS = ('startup-existing', 'startup-founding')
# These values affect Config/case identity and remain the original N300 values.
FROZEN_CASE_LIMITS = {'max_events': 2000000, 'world_timeout_seconds': 86400.0,
                      'per_world_rss_bytes': 536870912}

def require(ok, message):
    if not ok: raise ValueError(message)

def plan(limits, *, world):
    require(type(world) is int and world in range(52000, 52066),
            'selected world is not in the frozen independent confirmation roster')
    limits = limits if isinstance(limits,RuntimeLimits) else RuntimeLimits.from_dict(limits)
    require(limits.journal_chunk_bytes == 1048576 and limits.per_world_rss_bytes is not None,
            'original small-study lossless journal/RSS contract missing')
    require(all(getattr(limits,k) == value for k,value in FROZEN_CASE_LIMITS.items()),
            'existing N300 scientific case limits differ')
    require(limits.max_output_bytes>=study.CAPS[300] and limits.batch_max_output_bytes>=study.CAPS[300],
            'single complete-case resource allowance below original16GiB envelope')
    template, declaration = study.spec(300)
    require(template.days == 1917 and declaration['full_observation_years'] == 5
            and declaration['pilot_world_ids'] == [42000,42001,42002,42003]
            and declaration['arms'] == list(SELECTED_ARMS)
            and declaration['confirmation_world_start'] == 52000,
            'literal small-enterprise study changed')
    require(digest(CONFIRMATION_ROSTER_RAW) == CONFIRMATION_ROSTER_SHA,
            'existing confirmation roster bytes changed')
    roster = json.loads(CONFIRMATION_ROSTER_RAW)
    require(roster['status'] == 'frozen' and roster['pilot_worlds_excluded'] is True
            and type(roster['world_count']) is int and roster['world_count'] == 66
            and roster['world_ids'] == list(range(52000,52066))
            and roster['study_sha256'] == CONFIRMATION_STUDY_SHA
            and world in roster['world_ids'],
            'existing independent confirmation roster changed')
    rows = [{k:r[k] for k in ('case_id','config','tags')}
            for r in study.rows(300,world,limits)]
    require(len(rows)==2 and len({r['case_id'] for r in rows})==2
            and [r['tags']['arm_id'] for r in rows] == list(SELECTED_ARMS)
            and all(r['config']['world'] == world
                    and r['config']['days'] == 1917 and r['config']['n'] == 300
                    and r['config']['seed'] == declaration['seed'] for r in rows),
            'exact uncompleted confirmation pair inventory differs')
    return limits, declaration, rows


SNAPSHOT_SCHEMA = 'DAMS-small-enterprise-confirmation-status-1'
SNAPSHOT_FIELDS = {'schema', 'source_sha256', 'pipeline_driver_sha256',
                   'study_sha256', 'confirmation_roster_sha256', 'population',
                   'observed_utc', 'cases'}
STATUS_FIELDS = {'case_id', 'config_sha256', 'world', 'arm', 'state'}
STATES = {'UNSTARTED', 'COMPLETE', 'PARTIAL', 'UNKNOWN', 'RUNNING', 'FAILED'}


def validate_status_snapshot(snapshot, expected_sha, limits):
    """Conservative selection evidence, not a raw-result acceptance certificate.

    Root seals the current census and prevents cross-host double dispatch. This
    helper cannot turn an unknown, partial or completed case into a fresh one.
    The digest travels with the body; a guest needs no host-private path.
    """
    require(isinstance(snapshot, dict) and set(snapshot) == SNAPSHOT_FIELDS
            and snapshot['schema'] == SNAPSHOT_SCHEMA, 'unsupported status snapshot')
    require(isinstance(expected_sha, str) and len(expected_sha) == 64
            and all(c in '0123456789abcdef' for c in expected_sha), 'invalid status snapshot SHA')
    raw = canonical(snapshot) + b'\n'
    require(len(raw) <= 65536 and digest(raw) == expected_sha, 'status snapshot body pin differs')
    require(snapshot['source_sha256'] == CORE and snapshot['pipeline_driver_sha256'] == DRIVER
            and snapshot['study_sha256'] == CONFIRMATION_STUDY_SHA
            and snapshot['confirmation_roster_sha256'] == CONFIRMATION_ROSTER_SHA
            and type(snapshot['population']) is int and snapshot['population'] == 300,
            'status snapshot is not this frozen N300 study')
    observed = snapshot['observed_utc']
    require(isinstance(observed, str) and re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z', observed),
            'snapshot UTC observation missing')
    try:
        instant = datetime.fromisoformat(observed[:-1] + '+00:00')
    except (ValueError, TypeError) as error:
        raise ValueError('invalid snapshot UTC observation') from error
    require(instant.utcoffset().total_seconds() == 0, 'snapshot observation is not UTC')
    require(isinstance(snapshot['cases'], list) and len(snapshot['cases']) == 132,
            'status snapshot must name every frozen confirmation arm')
    expected = {}
    for world in range(52000, 52066):
        _, _, rows = plan(limits, world=world)
        for row in rows:
            expected[row['case_id']] = (world, row['tags']['arm_id'], digest(canonical(row['config'])))
    seen = {}
    for item in snapshot['cases']:
        require(isinstance(item, dict) and set(item) == STATUS_FIELDS, 'invalid status row shape')
        case_id = item['case_id']
        require(isinstance(case_id, str) and case_id in expected and case_id not in seen,
                'missing, duplicate or foreign snapshot case')
        require(type(item['world']) is int and (item['world'], item['arm'], item['config_sha256'])
                == expected[case_id], 'status row changes frozen world/arm/config')
        require(isinstance(item['state'], str) and item['state'] in STATES, 'unknown snapshot state')
        seen[case_id] = item
    require(set(seen) == set(expected), 'status snapshot roster coverage differs')
    return seen


def build_profile(limits, *, world, arm, status_snapshot, status_snapshot_sha256):
    require(type(world) is int and world in range(52000, 52066)
            and isinstance(arm,str) and arm in SELECTED_ARMS,
            'only frozen confirmation worlds and exact arms are selectable')
    _, declaration, rows = plan(limits, world=world)
    statuses = validate_status_snapshot(status_snapshot, status_snapshot_sha256, limits)
    selected = [r for r in rows if r['tags']['world']==world and r['tags']['arm_id']==arm]
    require(len(selected)==1,'selected existing confirmation arm unavailable')
    row=selected[0]
    require(statuses[row['case_id']]['state'] == 'UNSTARTED',
            'selected case is not explicitly unstarted; preserve original case/resume authority')
    return {'schema':SCHEMA,'source_sha256':CORE,'pipeline_driver_sha256':DRIVER,
            'study_entry_sha256':STUDY_IMPORT_SHA,'study_sha256':digest(canonical(declaration)),
            # Retained wire field: digest of THIS exact assigned two-arm inventory.
            'pilot_inventory_sha256':digest(canonical(rows)),'population':300,'days':1917,
            'seed':declaration['seed'],'selected_world':world,'selected_arm':arm,
            'selected_case_id':row['case_id'],'selected_config_sha256':digest(canonical(row['config'])),
            'selected_phase':'confirmation','confirmation_roster_sha256':CONFIRMATION_ROSTER_SHA,
            'confirmation_study_sha256':CONFIRMATION_STUDY_SHA,
            'confirmation_world_count':66,'confirmation_world_ids':list(range(52000,52066)),
            'status_snapshot':status_snapshot,'status_snapshot_sha256':status_snapshot_sha256}



# Names are operational evidence outside all owned attempt directories. Attempts
# already include their scientific data, CPs, event chunks and raw temporaries.
PAIR_FILE_METADATA = (
    'cloud-execution.json', 'pipeline.log', 'cloud-terminal.json',
    '.small-operation.lock', 'paired-operation.json', 'pair-result.json')
ARM_FILE_METADATA = (
    '.small-operation.lock', 'small-operation.json', 'single-arm-result.json',
    'confirmation-operation/case_inventory.json',
    'confirmation-operation/manifest.json',
    'confirmation-operation/case_census.json', 'case_statuses.json')
# One original guest receipt/operation temporary at pair root, and one original
# operation/status temporary per arm: preserve failed publications, never delete
# them to manufacture room. Two slots in each namespace cover both publishers.
PAIR_FILE_TEMPORARIES = 2
ARM_FILE_TEMPORARIES = 2

def paired_file_slot_requirements(limits):
    """Administrative reservation, not measured scientific file production.

    Scheduler per_world_output_files is per *attempt*. Its original finite retry
    preserves the earlier attempt; reserve all allowed attempts of both arms.
    This gate never changes any supplied limit, checkpoint, event or raw output.
    Root must separately bind terminal transport to the returned whole-tree cap.
    """
    rt = limits if isinstance(limits, RuntimeLimits) else RuntimeLimits.from_dict(limits)
    require(rt.per_world_output_files == 8192,
            'paired N300 needs the unchanged 8192-file owned attempt reservation')
    arms = len(SELECTED_ARMS)
    attempts = rt.max_retries + 1
    raw = arms * attempts * rt.per_world_output_files
    # Scheduler._record_exit publishes outside each attempt directory.
    exit_receipts = arms * attempts
    metadata = len(PAIR_FILE_METADATA) + arms * len(ARM_FILE_METADATA)
    temporary = PAIR_FILE_TEMPORARIES + arms * ARM_FILE_TEMPORARIES
    return {'schema':'DAMS-small-enterprise-paired-file-admission-1',
            'arms':arms, 'attempts_per_arm':attempts,
            'files_per_owned_attempt':rt.per_world_output_files,
            'attempt_file_slots':raw, 'attempt_exit_receipt_slots':exit_receipts,
            'named_metadata_slots':metadata,
            'temporary_publication_slots':temporary,
            'minimum_whole_pair_files':raw+exit_receipts+metadata+temporary,
            'minimum_terminal_files':raw+exit_receipts+metadata+temporary,
            'declared_whole_pair_files':rt.batch_max_output_files,
            'batch_max_output_bytes':rt.batch_max_output_bytes,
            'per_case_max_output_bytes':rt.max_output_bytes,
            'min_free_disk_bytes':rt.min_free_disk_bytes,
            'scientific_file_production_measured':False,
            'automatic_limit_change':False}

def _admit_paired_file_slots(limits):
    value = paired_file_slot_requirements(limits)
    require(value['declared_whole_pair_files'] >= value['minimum_whole_pair_files'],
            'paired whole-tree file cap is insufficient: declared '
            + str(value['declared_whole_pair_files']) + ', required '
            + str(value['minimum_whole_pair_files'])
            + ' (two arms, retained finite retries, exit receipts, metadata and publication temporaries);'
            + ' no attempt reservation or evidence may be dropped')
    return value


def build_pair_profile(limits, *, world, status_snapshot, status_snapshot_sha256):
    """Two unchanged arms, not a new calendar, sample or scientific model."""
    arms = [build_profile(limits, world=world, arm=arm, status_snapshot=status_snapshot,
                          status_snapshot_sha256=status_snapshot_sha256) for arm in SELECTED_ARMS]
    rt = limits if isinstance(limits, RuntimeLimits) else RuntimeLimits.from_dict(limits)
    _admit_paired_file_slots(rt)
    require(rt.max_output_bytes == study.CAPS[300] and rt.batch_max_output_bytes >= 2*study.CAPS[300]
            and rt.min_free_disk_bytes >= 80*1024**3,
            'pair needs two original 16GiB cases and retained 80GiB safety floor')
    keys = ('source_sha256','pipeline_driver_sha256','study_sha256','pilot_inventory_sha256',
            'population','days','seed','selected_world','status_snapshot_sha256')
    require(all(arms[0][key] == arms[1][key] for key in keys), 'pair arm common identity differs')
    profile = {'schema':PAIR_SCHEMA, **{key:arms[0][key] for key in keys}, 'arm_profiles':arms}
    require(len(canonical(profile)) <= 65536, 'paired profile exceeds original bounded wire allowance')
    return profile


def validate_pair_profile(profile, limits):
    require(isinstance(profile,dict) and set(profile)==PAIR_FIELDS and profile['schema']==PAIR_SCHEMA,
            'unsupported small-enterprise paired profile')
    arms = profile['arm_profiles']
    require(isinstance(arms,list) and len(arms)==2, 'paired profile requires two original arms')
    for arm in arms:
        require(isinstance(arm,dict) and arm.get('schema')==SCHEMA,
                'pair child must be the retained single-arm schema')
        validate_profile(arm,limits)
    require([arm['selected_arm'] for arm in arms] == list(SELECTED_ARMS),
            'pair arm order or literal strategies differ')
    expected = build_pair_profile(limits,world=profile['selected_world'],
                                  status_snapshot=arms[0]['status_snapshot'],
                                  status_snapshot_sha256=profile['status_snapshot_sha256'])
    require(canonical(profile)==canonical(expected),
            'pair world/source/snapshot/calendar/config identity differs')
    return profile


def validate_profile(profile, limits):
    if isinstance(profile,dict) and profile.get('schema')==PAIR_SCHEMA:
        return validate_pair_profile(profile,limits)
    require(isinstance(profile,dict) and set(profile)==FIELDS and profile['schema']==SCHEMA,
            'unsupported small-enterprise cloud profile')
    require(source_hash()==CORE and driver_hash()==DRIVER
            and file_digest(Path(study.__file__))==STUDY_IMPORT_SHA,'executing f9 science/study entry differs')
    require(type(profile['selected_world']) is int and type(profile['population']) is int
            and type(profile['days']) is int and type(profile['seed']) is int,'profile numeric types differ')
    expected=build_profile(limits,world=profile['selected_world'],arm=profile['selected_arm'],
                           status_snapshot=profile['status_snapshot'],
                           status_snapshot_sha256=profile['status_snapshot_sha256'])
    require(canonical(profile)==canonical(expected),'source/study/full calendar/seed/literal arm/config pin differs')
    return profile

def assignment(profile, limits):
    validate_profile(profile,limits)
    _, declaration, rows=plan(limits, world=profile['selected_world'])
    return {'schema':'dams-compute-only-assignment-v1','source_sha256':CORE,
            'pipeline_driver_sha256':DRIVER,'spec_sha256':profile['study_sha256'],
            'spec':declaration,'inventory_sha256':profile['pilot_inventory_sha256'],'cases':rows}

def _profile(path, expected_sha):
    path=Path(path).absolute()
    require(all(not p.is_symlink() for p in (path,*path.parents)), 'profile symlink ancestor')
    before=path.lstat();require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=65536,'profile is not bounded private regular bytes')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f: raw=f.read(65537)
    native=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink)
    require(native(path.lstat())==native(before) and len(raw)<=65536
            and hashlib.sha256(raw).hexdigest()==expected_sha,'external runtime profile pin differs')
    from research_tools.compute_only_control import parse
    return parse(raw),native(before)

class _OperationOutputs:
    """FD-relative operational JSON; scientific Scheduler remains unchanged."""
    def __init__(self, output, *, pair_mode=False):
        require(type(pair_mode) is bool, 'invalid operational output mode')
        self.stack = ExitStack(); self.observed = {}; self.directories = []
        try:
            path = Path(output).absolute()
            require(all(x not in ('.','..') for x in path.parts), 'unsafe operation path')
            missing = []
            ancestor = path
            while not ancestor.exists() and not ancestor.is_symlink():
                missing.append(ancestor.name); ancestor = ancestor.parent
            root = self._open(ancestor)
            for name in reversed(missing): root = self._child(root, name)
            self.root = root
            self.stage = self._child(root, 'confirmation-operation')
            for directory,names in ((root,('paired-operation.json','pair-result.json') if pair_mode
                                    else ('small-operation.json','single-arm-result.json')),
                                    (self.stage,('case_inventory.json','manifest.json','case_census.json'))):
                for name in names:
                    raw=self.read(directory,name)
                    if raw is not None:self.observed[directory,name]=(raw,self._leaf(directory,name))
            self.check()
        except BaseException:
            self.stack.close(); raise

    def _open(self, path):
        try:
            value = Directory(path)
        except (OSError, RuntimeError) as error:
            raise ValueError('unsafe or symlinked operation directory') from error
        self.stack.callback(value.close); self.directories.append(value)
        return value

    def _child(self, parent, name):
        parent.check()
        try: os.mkdir(name, 0o700, dir_fd=parent.fd)
        except FileExistsError: pass
        before = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
        require(stat.S_ISDIR(before.st_mode), 'unsafe or symlinked operation child')
        value = self._open(parent.path/name)
        require((before.st_dev,before.st_ino)==(os.fstat(value.fd).st_dev,os.fstat(value.fd).st_ino),
                'operation child directory identity changed')
        parent.check(); value.check()
        return value

    @staticmethod
    def native(info):
        return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_nlink)

    def _leaf(self, directory, name):
        directory.check()
        try: info = os.stat(name,dir_fd=directory.fd,follow_symlinks=False)
        except FileNotFoundError: return None
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=8*1024**2,
                'unsafe or oversized operation JSON leaf')
        return self.native(info)

    def read(self, directory, name):
        before = self._leaf(directory,name)
        if before is None: return None
        require(before[2] <= 8*1024**2, 'operation JSON exceeds bound')
        fd=directory.open(name,os.O_RDONLY|os.O_NONBLOCK)
        try:
            require(self.native(os.fstat(fd))==before,'operation JSON changed before open')
            raw=bytearray()
            while block:=os.read(fd,min(65536,8*1024**2+1-len(raw))):
                raw.extend(block);require(len(raw)<=8*1024**2,'operation JSON exceeds bound')
            directory.matches(name,fd)
            require(self.native(os.fstat(fd))==before,'operation JSON read changed')
        finally:os.close(fd)
        require(self._leaf(directory,name)==before,'operation JSON closed read changed')
        return bytes(raw)

    def check(self):
        for directory in self.directories: directory.check()
        for (directory,name),(raw,anchor) in self.observed.items():
            require(self._leaf(directory,name)==anchor and self.read(directory,name)==raw,
                    'operation published JSON changed')

    def write(self, directory, name, value):
        self.check(); raw=canonical(value)+b'\n'
        require(len(raw)<=8*1024**2, 'operation JSON exceeds bound')
        before = self._leaf(directory,name)
        temporary = '.small-operation-'+uuid.uuid4().hex
        fd=directory.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        try:
            view=memoryview(raw)
            while view:
                count=os.write(fd,view);require(count>0,'operation JSON short write');view=view[count:]
            os.fsync(fd);directory.matches(temporary,fd)
            self.check()
            require(self._leaf(directory,name)==before,'operation JSON target changed before publication')
            os.replace(temporary,name,src_dir_fd=directory.fd,dst_dir_fd=directory.fd)
            directory.matches(name,fd);os.fsync(directory.fd);directory.check()
            written=self.native(os.fstat(fd))
        finally: os.close(fd)
        actual=self.read(directory,name)
        require(actual==raw and self._leaf(directory,name)==written,'operation JSON publication readback changed')
        self.observed[directory,name]=(raw,written);self.check()
        return digest(raw)

    def close(self):
        # Retain the original directory/leaf observations across FD close.
        try:
            self.check()
            namespaces={p:(p.lstat().st_dev,p.lstat().st_ino) for d in self.directories for p in (d.path,*d.path.parents)}
            leaves={(d.path/name):anchor for (d,name),(_,anchor) in self.observed.items()}
        finally:self.stack.close()
        for p,anchor in namespaces.items():
            i=p.lstat();require(stat.S_ISDIR(i.st_mode) and (i.st_dev,i.st_ino)==anchor,
                               'operation directory changed at close')
        for p,anchor in leaves.items():
            i=p.lstat();require(stat.S_ISREG(i.st_mode) and self.native(i)==anchor,
                               'operation JSON changed at close')


@contextmanager
def _case_writer(profile):
    """Single-host/UID case lock; Root still owns cross-VM dispatch uniqueness."""
    import fcntl
    # Fixed POSIX namespace, independent of a worker's TMPDIR/cache routing.
    parent = Path('/tmp').resolve()
    path = parent / ('dams-small-enterprise-writers-' + str(os.getuid()))
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe case writer directory')
    with ExitStack() as stack:
        directory = Directory(path)
        stack.callback(directory.close)
        require((info.st_dev, info.st_ino) == (os.fstat(directory.fd).st_dev, os.fstat(directory.fd).st_ino),
                'case writer directory identity changed')
        name = CORE + '-' + profile['selected_case_id'] + '.lock'
        fd = directory.open(name, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK)
        owner = stack.enter_context(os.fdopen(fd, 'a+b'))
        opened = os.fstat(owner.fileno())
        require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1
                and opened.st_uid == os.getuid(), 'unsafe case writer lock')
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory.matches(name, owner.fileno())
        directory.check()
        yield
        directory.matches(name, owner.fileno())
        directory.check()

RESUME_SCHEMA = 'DAMS-small-enterprise-resume-envelope-1'
RESUME_GRANT_SCHEMA = 'DAMS-small-enterprise-root-resume-admission-1'
_RESUME_CAPABILITY_TOKEN = object()


class _ResumeAdmission:
    # Internal helpers cannot opt into recovery with an arbitrary callback.
    # The only supported constructor is after the public sealed grant checks.
    def __init__(self, check, lifetime, output_root, token):
        require(token is _RESUME_CAPABILITY_TOKEN, 'sealed resume admission required')
        self._check=check;self.lifetime=lifetime;self.output_root=Path(output_root)
    def __call__(self):return self._check()


def _resume_file(path):
    """No-follow full-byte digest with anchored parent and native identity."""
    path=Path(path).absolute()
    directory=Directory(path.parent)
    fd=None
    try:
        info=os.stat(path.name,dir_fd=directory.fd,follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,
                'resume tree special or multiply linked file')
        native=list(_OperationOutputs.native(info))
        fd=directory.open(path.name,os.O_RDONLY|os.O_NONBLOCK)
        require(list(_OperationOutputs.native(os.fstat(fd)))==native,'resume open identity changed')
        sha=hashlib.sha256();count=0
        while block:=os.read(fd,1024*1024):sha.update(block);count+=len(block)
        directory.matches(path.name,fd)
        require(list(_OperationOutputs.native(os.fstat(fd)))==native and count==info.st_size,
                'resume input changed during full read')
        directory.check()
        return {'native':native,'sha256':sha.hexdigest(),'bytes':count}
    finally:
        if fd is not None:os.close(fd)
        directory.close()


def _resume_tree(path):
    """Closed tree identity, full bytes and SHA; no ignored temporary evidence."""
    path = Path(path).absolute()
    require(path.is_dir() and all(not p.is_symlink() for p in (path,*path.parents)),
            'resume output is absent or symlinked')
    dirs = {}; files = {}
    for item in [path,*sorted(path.rglob('*'))]:
        info = item.lstat(); rel = str(item.relative_to(path))
        require(not item.is_symlink(), 'resume tree symlink')
        if stat.S_ISDIR(info.st_mode):
            dirs[rel] = [info.st_dev,info.st_ino]
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                    'resume tree special or multiply linked file')
            files[rel] = _resume_file(item)
    return {'directories':dirs,'files':files}


def _resume_sources():
    root = Path(__file__).resolve().parents[1]
    paths = [p for directory in ('dams_sim','research_tools')
             for p in (root/directory).rglob('*.py')]
    paths += [root/name for name in ('pyproject.toml','requirements.lock','run.sh')
              if (root/name).is_file()]
    require(all(p.is_file() and not p.is_symlink() for p in paths), 'resume source symlink')
    return {str(p.relative_to(root)):_resume_file(p)['sha256'] for p in sorted(paths)}


def _resume_arm_states(profile, output):
    arms=profile['arm_profiles'] if profile['schema']==PAIR_SCHEMA else [profile]
    result=[]
    for arm in arms:
        child=Path(output)/arm['selected_arm'] if profile['schema']==PAIR_SCHEMA else Path(output)
        case=child/'cases'/arm['selected_case_id']
        attempts=sorted(p for p in case.glob('attempt-*') if p.is_dir())
        prior_started=False;prior_complete=False
        # Current and create-only retained operational census/receipt evidence
        # cannot be discarded merely because a case directory went missing.
        for file in Path(output).rglob('*.json'):
            relative=str(file.relative_to(output))
            if '/cases/' in '/'+relative or file.stat().st_size>8*1024**2:continue
            value=json.loads(file.read_bytes())
            if type(value) is dict:
                records=value.get('records',[]) if value.get('schema')=='DAMS-stopped-case-census-1' else []
                for record in records:
                    if record.get('case_id')==arm['selected_case_id']:
                        prior_started |= record.get('classification')!='unstarted'
                        prior_complete |= record.get('classification')=='complete'
                if value.get('case_id')==arm['selected_case_id'] and value.get('status')=='COMPLETE_INDIVIDUAL_FULL_RAW_VERIFIED':
                    prior_started=True;prior_complete=True
                for receipt in value.get('completed_arm_receipts',[]):
                    if receipt.get('case_id')==arm['selected_case_id']:prior_started=True;prior_complete=True
            elif type(value) is list:
                for record in value:
                    if (type(record) is dict and 'attempt' in record and 'status' in record
                            and record.get('case_id')==arm['selected_case_id']):
                        prior_started=True;prior_complete |= record.get('status')=='complete'
        complete=[p for p in attempts if (p/'manifest.json').is_file()
                  and json.loads((p/'manifest.json').read_bytes()).get('status')=='complete']
        require(not prior_started or attempts, 'retained started/complete evidence lacks original case tree')
        require(not prior_complete or complete, 'retained complete evidence lacks original complete marker')
        require(not case.exists() or attempts, 'existing case tree lacks original attempts')
        result.append({'case_id':arm['selected_case_id'],'arm':arm['selected_arm'],
                       'state':'COMPLETE' if complete else 'PARTIAL' if attempts else 'UNSTARTED',
                       'attempt_names':[p.name for p in attempts]})
    return result


def resume_envelope(profile, limits, output):
    """Measure inputs for a Root-issued grant; this measurement issues no grant."""
    validate_profile(profile,limits)
    runtime = limits if isinstance(limits,RuntimeLimits) else RuntimeLimits.from_dict(limits)
    tree = _resume_tree(output)
    require(tree['files'], 'resume requires retained original operation evidence')
    require(len(tree['files'])<=runtime.batch_max_output_files
            and sum(v['bytes'] for v in tree['files'].values())<=runtime.batch_max_output_bytes,
            'resume tree exceeds original finite file/byte bounds')
    return {'schema':RESUME_SCHEMA,'profile_sha256':digest(canonical(profile)),
            'runtime_limits_sha256':digest(canonical(runtime.to_dict())),
            'source_sha256':CORE,'pipeline_driver_sha256':DRIVER,
            'sources':_resume_sources(),'output':str(Path(output).absolute()),
            'tree':tree,'arm_states':_resume_arm_states(profile,output),'original_deadline':runtime.deadline,
            'original_stop_cutoff':runtime.stop_cutoff}


def _resume_descriptor(descriptor):
    require(type(descriptor) is dict and set(descriptor)=={'path','sha256'}
            and type(descriptor['path']) is str and Path(descriptor['path']).is_absolute()
            and type(descriptor['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',descriptor['sha256']),
            'Root referenced proof descriptor differs')
    return _profile(descriptor['path'],descriptor['sha256'])


def _resume_parent_authorization(descriptor, common):
    require(descriptor.get('sha256')==common['parent_authorization_sha256'],
            'Root original parent authorization digest differs')
    value,native=_resume_descriptor(descriptor)
    binding={k:common[k] for k in ('issued_by','original_profile_sha256',
             'original_runtime_limits_sha256','source_sha256','pipeline_driver_sha256',
             'case_ids','original_deadline')}
    expected={**binding,'schema':'DAMS-small-enterprise-parent-authorization-1',
              'source_refs':value.get('source_refs')}
    require(canonical(value)==canonical(expected), 'Root original parent authorization semantic binding differs')
    refs=value['source_refs']
    require(type(refs) is list and 1<=len(refs)<=32, 'Root original parent source references missing')
    anchors=[(Path(descriptor['path']),native)]
    for item in refs:
        require(type(item) is dict and set(item)=={'path','sha256'}
                and type(item['path']) is str and Path(item['path']).is_absolute()
                and type(item['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',item['sha256']),
                'Root parent original source descriptor differs')
        observed=_resume_file(item['path'])
        require(observed['sha256']==item['sha256'], 'Root original parent source bytes differ')
        anchors.append((Path(item['path']),tuple(observed['native'])))
    return anchors


def _resume_referenced_proof(descriptor, expected, flag):
    require(type(descriptor) is dict and set(descriptor)=={'path','sha256'}
            and type(descriptor['path']) is str and Path(descriptor['path']).is_absolute()
            and type(descriptor['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',descriptor['sha256']),
            'Root referenced proof descriptor differs')
    value,native=_profile(descriptor['path'],descriptor['sha256'])
    expected={**expected,flag:True,'evidence':value.get('evidence')}
    require(canonical(value)==canonical(expected), 'Root referenced proof semantic binding differs')
    evidence=value['evidence']
    require(type(evidence) is list and 1<=len(evidence)<=32, 'Root proof evidence roster missing or unbounded')
    anchors=[(Path(descriptor['path']),native)]
    seen=set()
    for item in evidence:
        require(type(item) is dict and set(item)=={'path','sha256'}
                and type(item['path']) is str and Path(item['path']).is_absolute()
                and type(item['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',item['sha256'])
                and item['path'] not in seen,'Root proof evidence descriptor differs')
        seen.add(item['path']);observed=_resume_file(item['path'])
        require(observed['sha256']==item['sha256'],'Root proof referenced evidence bytes differ')
        anchors.append((Path(item['path']),tuple(observed['native'])))
        leaf,leaf_native=_resume_descriptor(item)
        leaf_schema=('DAMS-small-enterprise-closed-dispatch-evidence-1'
                     if flag=='owned_producers_absent' else 'DAMS-small-enterprise-ledger-admission-evidence-1')
        leaf_expected={k:v for k,v in expected.items() if k not in ('schema','evidence')}
        leaf_expected.update(schema=leaf_schema,parent_authorization=leaf.get('parent_authorization'))
        require(canonical(leaf)==canonical(leaf_expected),
                'Root underlying typed evidence semantic binding differs')
        anchors.extend(_resume_parent_authorization(leaf['parent_authorization'],expected))
    return anchors


def resume(profile, limits, output, envelope, *, grant_path, grant_sha256,
           root_lifetime_check):
    """Opt-in exact-case recovery. Only a pinned typed Root grant admits it.

    The caller's lifetime check additionally enforces its original shared ledger
    and cross-host dispatch ownership. It cannot replace the sealed grant, source,
    filesystem, profile, checkpoint, deadline or original raw gates.
    """
    import time
    require(callable(root_lifetime_check), 'Root lifetime check required')
    measured = resume_envelope(profile,limits,output)
    require(canonical(measured)==canonical(envelope), 'resume envelope input identity differs')
    runtime = limits if isinstance(limits,RuntimeLimits) else RuntimeLimits.from_dict(limits)
    require(len(canonical(envelope))<=8*1024**2, 'resume envelope exceeds bounded metadata')
    envelope_sha = digest(canonical(envelope))
    grant,grant_native = _profile(grant_path,grant_sha256)
    expected = {'schema':RESUME_GRANT_SCHEMA,'envelope_sha256':envelope_sha,
                'issued_by':'Root','original_profile_sha256':measured['profile_sha256'],
                'original_runtime_limits_sha256':measured['runtime_limits_sha256'],
                'original_arm_states_sha256':digest(canonical(measured['arm_states'])),
                'original_deadline':runtime.deadline,'owned_producers_absent':True,
                'shared_budget_admitted':True,'parent_authorization_sha256':grant.get('parent_authorization_sha256'),
                'closed_dispatch_proof':grant.get('closed_dispatch_proof'),
                'ledger_admission':grant.get('ledger_admission'),
                'expires_epoch':grant.get('expires_epoch')}
    require(canonical(grant)==canonical(expected)
            and type(grant['parent_authorization_sha256']) is str
            and re.fullmatch(r'[0-9a-f]{64}',grant['parent_authorization_sha256'])
            and type(grant['expires_epoch']) in (int,float)
            and time.time()<grant['expires_epoch']==runtime.deadline,
            'typed Root resume grant differs or expired')
    common={'issued_by':'Root','original_profile_sha256':measured['profile_sha256'],
            'original_runtime_limits_sha256':measured['runtime_limits_sha256'],
            'original_arm_states_sha256':digest(canonical(measured['arm_states'])),
            'source_sha256':CORE,'pipeline_driver_sha256':DRIVER,
            'case_ids':[v['case_id'] for v in measured['arm_states']],
            'original_deadline':runtime.deadline,
            'parent_authorization_sha256':grant['parent_authorization_sha256'],
            'envelope_sha256':envelope_sha,'native_tree_sha256':digest(canonical(measured['tree']))}
    proof_anchors=[]
    for key,schema,flag in (
            ('closed_dispatch_proof','DAMS-small-enterprise-closed-dispatch-proof-1','owned_producers_absent'),
            ('ledger_admission','DAMS-small-enterprise-ledger-admission-proof-1','shared_budget_admitted')):
        proof_anchors.extend(_resume_referenced_proof(grant[key],{**common,'schema':schema},flag))
    source_pins = measured['sources']
    # Only these exact original operational leaves are rewritten by the original
    # execution ABI. All original attempts, raw/checkpoint bytes and exit-time
    # accounting remain pinned throughout recovery.
    mutable={'paired-operation.json','pair-result.json','single-arm-result.json',
             'small-operation.json','case_statuses.json',
             'confirmation-operation/case_inventory.json',
             'confirmation-operation/manifest.json','confirmation-operation/case_census.json'}
    def retained_guard():
        for relative,anchor in measured['tree']['directories'].items():
            path=Path(output)/relative
            info=path.lstat()
            require(stat.S_ISDIR(info.st_mode) and [info.st_dev,info.st_ino]==anchor,
                    'original resume directory identity changed')
        for relative,anchor in measured['tree']['files'].items():
            local=relative
            if profile['schema']==PAIR_SCHEMA:
                for arm in SELECTED_ARMS:
                    if local.startswith(arm+'/'):local=local[len(arm)+1:];break
            if local in mutable:continue
            require(_resume_file(Path(output)/relative)==anchor,
                    'original raw/checkpoint/attempt evidence changed during resume')
    def lifetime():
        for path,anchor in proof_anchors:
            directory=Directory(path.parent)
            try:
                info=os.stat(path.name,dir_fd=directory.fd,follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and _OperationOutputs.native(info)==anchor,
                        'Root referenced proof/evidence identity changed')
                directory.check()
            finally:directory.close()
        require(time.time()<grant['expires_epoch'], 'original resume deadline expired')
        require(_profile(grant_path,grant_sha256)[1]==grant_native, 'Root grant changed')
        require(root_lifetime_check(envelope_sha) is True,
                'Root lifetime/producer/budget admission failed')
    def gate():
        require(time.time()<grant['expires_epoch'] and time.time()<runtime.deadline,
                'original resume deadline expired')
        require(_profile(grant_path,grant_sha256)[1]==grant_native, 'Root grant changed')
        require(_resume_sources()==source_pins and source_hash()==CORE and driver_hash()==DRIVER,
                'resume source changed')
        require(digest(canonical(profile))==measured['profile_sha256']
                and digest(canonical(runtime.to_dict()))==measured['runtime_limits_sha256'],
                'resume profile or original bounds changed')
        lifetime()
        require(_profile(grant_path,grant_sha256)[1]==grant_native, 'Root grant changed')
        require(_resume_sources()==source_pins, 'resume source changed during admission')
        retained_guard()
        _resume_whole_bounds(output,runtime)
        require(time.time()<grant['expires_epoch'], 'Root resume admission expired during checking')
    admission = _ResumeAdmission(gate,lifetime,output,_RESUME_CAPABILITY_TOKEN)
    arms = profile['arm_profiles'] if profile['schema']==PAIR_SCHEMA else [profile]
    with ExitStack() as stack:
        for arm in arms: stack.enter_context(_case_writer(arm))
        # Both cases are locked before final exact input read or administrative writes.
        gate()
        require(canonical(_resume_tree(output))==canonical(measured['tree']),
                'resume input changed after lock acquisition')
        administrative=_resume_administrative_paths(output)
        predicted_files=len(administrative)+3+1
        predicted_bytes=sum(p.stat().st_size for p in administrative)+len(canonical(envelope))+len(canonical(grant))+4096
        _resume_whole_bounds(output,runtime,additional_files=predicted_files,additional_bytes=predicted_bytes)
        _retain_resume_administration(output, envelope_sha, envelope, grant)
        if profile['schema']==PAIR_SCHEMA:
            return _execute_pair_owned(profile,limits,output,resume_gate=admission)
        return _execute_owned(profile,limits,output,resume_gate=admission)


def _resume_administrative_paths(output):
    mutable={'paired-operation.json','pair-result.json','single-arm-result.json',
             'small-operation.json','case_statuses.json',
             'confirmation-operation/case_inventory.json',
             'confirmation-operation/manifest.json','confirmation-operation/case_census.json'}
    result=[]
    for file in Path(output).rglob('*.json'):
        local=str(file.relative_to(output))
        for arm in SELECTED_ARMS:
            if local.startswith(arm+'/'):local=local[len(arm)+1:];break
        if local in mutable:result.append(file)
    return sorted(result)


def _resume_whole_bounds(output, runtime, *, additional_files=0,additional_bytes=0):
    from dams_sim.scheduler import output_files,disk_bytes
    require(output_files(Path(output))+additional_files<=runtime.batch_max_output_files,
            'resume whole-tree file reservation exhausted')
    require(disk_bytes(Path(output))+additional_bytes<=runtime.batch_max_output_bytes,
            'resume whole-tree byte reservation exhausted')


def _resume_run(scheduler, configs, admission, output):
    """Joined bounded monitor stops the original Scheduler on Root revocation."""
    import threading
    done=threading.Event();failure=[]
    def monitor():
        while not done.wait(.25):
            try:admission.lifetime()
            except BaseException as error:
                failure.append(error);scheduler.stop();return
    admission()
    runtime=scheduler.limits
    # Reserve one full next original attempt plus exit and result publication.
    # Further retries are checked again through Scheduler's _elapsed ABI.
    original_elapsed=scheduler._elapsed
    def elapsed(attempts):
        admission()
        _resume_whole_bounds(output,runtime,
            additional_files=runtime.per_world_output_files+8,
            additional_bytes=max(int(c.max_output_mb*1_000_000) for c in configs)+65536)
        return original_elapsed(attempts)
    scheduler._elapsed=elapsed
    thread=threading.Thread(target=monitor,name='dams-original-resume-lifetime',daemon=False)
    thread.start()
    try:return scheduler.run(configs)
    finally:
        done.set();thread.join()
        if failure:raise ValueError('Root resume lifetime revoked during owned Scheduler') from failure[0]
        admission()


def _retain_resume_administration(output, envelope_sha, envelope, grant):
    """Create-only originals of every administrative leaf rewritten on recovery."""
    output = Path(output).absolute()
    directory = output/('resume-evidence-'+envelope_sha)
    require(not os.path.lexists(directory), 'resume envelope already consumed')
    # Open all ancestry with the same no-follow directory discipline as execution.
    holder = _OperationOutputs(output, pair_mode=True)
    try:
        target = holder._child(holder.root,directory.name)
        for file in _resume_administrative_paths(output):
            relative = file.relative_to(output)
            name = digest(canonical(str(relative)))+'.json'
            info = file.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=8*1024**2,
                    'resume administrative evidence is not bounded regular bytes')
            before = _OperationOutputs.native(info)
            safe=_resume_file(file)
            fd_read=Directory(file.parent)
            try:
                readfd=fd_read.open(file.name,os.O_RDONLY|os.O_NONBLOCK)
                try:
                    chunks=[]
                    while block:=os.read(readfd,65536):chunks.append(block)
                    raw=b''.join(chunks);fd_read.matches(file.name,readfd)
                finally:os.close(readfd)
            finally:fd_read.close()
            require(digest(raw)==safe['sha256'],'resume administrative digest changed')
            require(_OperationOutputs.native(file.lstat())==before,
                    'resume administrative input changed')
            fd=target.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            try:
                view=memoryview(raw)
                while view:
                    count=os.write(fd,view);require(count>0,'resume preservation short write');view=view[count:]
                os.fsync(fd);target.matches(name,fd);os.fsync(target.fd)
            finally:os.close(fd)
            require(holder.read(target,name)==raw, 'resume preservation readback differs')
        holder.write(target,'resume-envelope.json',envelope)
        holder.write(target,'root-admission.json',grant)
        holder.write(target,'preservation.json',{'schema':RESUME_SCHEMA,'envelope_sha256':envelope_sha,
                    'original_administration_preserved':True,'formal_samples_added':0})
    finally: holder.close()


def _resume_elapsed(attempts):
    """Conservative closed wall accounting; checkpoint age is not a terminal."""
    import math
    total=0.
    for attempt in attempts:
        samples=[];closed=False
        for path,field in ((attempt.parent/(attempt.name+'-exit.json'),'attempt_wall_seconds'),
                           (attempt/'manifest.json','total_wall_seconds'),
                           (attempt/'checkpoint-index.json','attempt_elapsed_wall_seconds')):
            if not path.is_file():continue
            value=json.loads(path.read_bytes())
            if field not in value:continue
            number=value[field]
            require(type(number) in (int,float) and math.isfinite(number) and number>=0,
                    'original attempt elapsed record invalid')
            samples.append(number)
            if path.name==attempt.name+'-exit.json':closed=True
            if path.name=='manifest.json' and value.get('status') in ('failed','complete','checkpointed'):
                closed=True
        require(closed and samples, 'original closed attempt elapsed evidence missing')
        total+=max(samples)
    return total


def _validated_complete_arm(profile, limits, output, rows, identity):
    selected=next(r for r in rows if r['case_id']==profile['selected_case_id'])
    attempts=sorted(p for p in (Path(output)/'cases'/selected['case_id']).glob('attempt-*') if p.is_dir())
    runtime=limits if isinstance(limits,RuntimeLimits) else RuntimeLimits.from_dict(limits)
    require(_resume_elapsed(attempts)<runtime.world_timeout_seconds,
            'original cumulative world-attempt time budget exhausted')
    complete=[]
    for attempt in attempts:
        marker=attempt/'manifest.json'
        if marker.is_file() and json.loads(marker.read_bytes()).get('status')=='complete':
            complete.append(attempt)
    require(len(complete)<=1,'multiple complete attempts cannot become one reused arm')
    if not complete:
        if attempts:
            require(any((p/'checkpoint-index.json').is_file() for p in attempts),
                    'partial case lacks original checkpoint; refusing regeneration')
            for attempt in attempts:
                index=attempt/'checkpoint-index.json'
                if index.is_file():
                    snapshots=json.loads(index.read_bytes()).get('snapshots',[])
                    require(snapshots and all(type(v.get('day')) is int
                            and v['day']<selected['config']['days'] for v in snapshots),
                            'horizon checkpoint lacks original complete evidence; refusing regeneration')
        return None
    reader=study.CheckedLongitudinalCases(Path(output),{k:identity[k] for k in
        ('source_sha256','pipeline_driver_sha256','spec_sha256')})
    runtime=limits if isinstance(limits,RuntimeLimits) else RuntimeLimits.from_dict(limits)
    value=reader.validate_case(selected,complete[0],expected_provenance=runtime.provenance)
    require(reader.result()['unique_complete_cases']==1,'reused arm original full raw gate missing')
    return complete[0],value


def execute(profile, limits, output):
    validate_profile(profile, limits)
    if profile['schema']==PAIR_SCHEMA:
        with ExitStack() as stack:
            for arm in profile['arm_profiles']:
                stack.enter_context(_case_writer(arm))
            return _execute_pair_owned(profile,limits,output)
    with _case_writer(profile):
        return _execute_owned(profile, limits, output)

def _execute_pair_owned(profile, limits, output, *, resume_gate=None):
    """Retain each child's evidence; failed second arm can never certify a pair."""
    require(resume_gate is None or type(resume_gate) is _ResumeAdmission,
            'sealed resume admission required')
    output = Path(output).absolute()
    outputs = _OperationOutputs(output,pair_mode=True)
    import fcntl
    try:
        fd = outputs.root.open('.small-operation.lock',os.O_RDWR|os.O_CREAT)
        with os.fdopen(fd,'a+b') as owner:
            require(os.fstat(owner.fileno()).st_nlink==1, 'unsafe pair operation lock')
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if resume_gate is None:
                require(not outputs.observed, 'paired operation already has state; preserve original resume/verification path')
            else:
                resume_gate()
                raw=outputs.read(outputs.root,'paired-operation.json')
                require(raw is not None, 'resume paired intent missing')
                prior=json.loads(raw)
                require(canonical(prior.get('profile'))==canonical(profile)
                        and canonical(prior.get('full_confirmation_pair_assignment'))==canonical(assignment(profile,limits)),
                        'resume paired intent changed')
                require(outputs.read(outputs.root,'pair-result.json') is None,
                        'completed pair already accepted; recovery cannot regenerate it')
                require(type(prior.get('completed_arm_receipts')) is list
                        and len(prior['completed_arm_receipts'])<len(SELECTED_ARMS),
                        'both arm receipts already accepted; verify lost pair evidence without re-execution')
            children = []
            for arm in profile['arm_profiles']:
                path = output/arm['selected_arm']
                if resume_gate is None:
                    require(not os.path.lexists(path), 'paired child already has state; preserve existing arm evidence')
                children.append(outputs._child(outputs.root,arm['selected_arm']))
            intended = {'schema':'DAMS-small-enterprise-paired-operation-1',
                        'profile':profile,'full_confirmation_pair_assignment':assignment(profile,limits),
                        'child_outputs':list(SELECTED_ARMS),'paired_world_complete':False,
                        'completed_arm_receipts':[]}
            outputs.write(outputs.root,'paired-operation.json',intended)
            accepted = []
            for arm, child in zip(profile['arm_profiles'],children):
                outputs.check()
                # Same original Scheduler/raw gate as single-arm execution. UID
                # locks for BOTH original cases are already held by execute().
                if resume_gate is None:
                    _execute_owned(arm,limits,child.path)
                else:
                    _execute_owned(arm,limits,child.path,resume_gate=resume_gate)
                outputs.check()
                observed = _OperationOutputs(child.path)
                try:
                    raw = observed.read(observed.root,'single-arm-result.json')
                    require(raw is not None, 'completed arm raw-gate receipt missing')
                    from research_tools.compute_only_control import parse
                    result = parse(raw)
                    require(result.get('status')=='COMPLETE_INDIVIDUAL_FULL_RAW_VERIFIED'
                            and result.get('case_id')==arm['selected_case_id']
                            and canonical(result.get('profile'))==canonical(arm)
                            and result.get('paired_world_complete') is False
                            and result.get('full_study_complete') is False,
                            'completed arm raw-gate receipt differs')
                    accepted.append({'arm':arm['selected_arm'],'case_id':arm['selected_case_id'],
                                     'relative_output':arm['selected_arm'],
                                     'single_arm_result_sha256':digest(raw),
                                     'manifest_sha256':result['manifest_sha256']})
                finally:
                    observed.close()
                if resume_gate is not None:resume_gate()
                intended['completed_arm_receipts']=list(accepted)
                outputs.write(outputs.root,'paired-operation.json',intended)
            if resume_gate is not None:resume_gate()
            validate_pair_profile(profile,limits)
            if resume_gate is not None:resume_gate()
            outputs.write(outputs.root,'pair-result.json',
                          {'status':'COMPLETE_PAIRED_WORLD_FULL_RAW_VERIFIED',
                           'profile_sha256':digest(canonical(profile)),
                           'world':profile['selected_world'],'arms':accepted,
                           'paired_world_complete':True,'independent_confirmation_worlds':1,
                           'full_study_complete':False,'empirical_validation':False})
    finally:
        outputs.close()
        if resume_gate is not None:resume_gate()


def _execute_owned(profile, limits, output, *, resume_gate=None):
    require(resume_gate is None or type(resume_gate) is _ResumeAdmission,
            'sealed resume admission required')
    validate_profile(profile,limits);limits,declaration,rows=plan(limits, world=profile['selected_world'])
    output=Path(output).absolute()
    outputs=_OperationOutputs(output)
    import fcntl
    try:
      lock_fd=outputs.root.open('.small-operation.lock',os.O_RDWR|os.O_CREAT)
      with os.fdopen(lock_fd,'a+b') as owner:
        require(stat.S_ISREG(os.fstat(owner.fileno()).st_mode) and os.fstat(owner.fileno()).st_nlink==1,'operation lock is not owned regular bytes')
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        case_path = output/'cases'/profile['selected_case_id']
        if resume_gate is None:
            require(not os.path.lexists(case_path),
                    'selected case already has local state; preserve original resume/verification path')
            require(outputs.read(outputs.root,'single-arm-result.json') is None,
                    'accepted single-arm result exists; completed case cannot be regenerated')
        else:
            resume_gate()
        intended={'profile':profile,'full_confirmation_pair_assignment':assignment(profile,limits)}
        raw=outputs.read(outputs.root,'small-operation.json')
        if raw is not None:
            from research_tools.compute_only_control import parse
            require(canonical(parse(raw))==canonical(intended),'operation intent changed')
            outputs.observed[outputs.root,'small-operation.json']=(raw,outputs._leaf(outputs.root,'small-operation.json'))
        else:
            require(not outputs.observed,'operation metadata exists without retained intent')
            outputs.write(outputs.root,'small-operation.json',intended)
        selected=next(r for r in rows if r['case_id']==profile['selected_case_id'])
        identity={'schema_version':3,'source_sha256':CORE,'pipeline_driver_sha256':DRIVER,'spec_sha256':profile['study_sha256']}
        stage=outputs.stage
        if resume_gate is not None:
            completed=_validated_complete_arm(profile,limits,output,rows,identity)
            accepted=outputs.read(outputs.root,'single-arm-result.json')
            if accepted is not None:
                require(completed is not None, 'accepted receipt lacks complete original raw case')
                result=json.loads(accepted)
                require(result.get('status')=='COMPLETE_INDIVIDUAL_FULL_RAW_VERIFIED'
                        and canonical(result.get('profile'))==canonical(profile)
                        and result.get('case_id')==profile['selected_case_id']
                        and result.get('manifest_sha256')==file_digest(completed[0]/'manifest.json')
                        and result.get('summary')==completed[1]['summary'],
                        'reused complete arm receipt differs from original raw gate')
                resume_gate()
                return
        from research_tools.compute_only_control import parse
        prior=outputs.read(stage,'case_inventory.json')
        require(prior is None or canonical(parse(prior))==canonical(rows),'operation full inventory changed')
        prior=outputs.read(stage,'manifest.json')
        if prior is not None:
            prior=parse(prior)
            require(all(prior.get(k)==v for k,v in identity.items())
                    and prior.get('inventory_sha256')==profile['pilot_inventory_sha256']
                    and prior.get('operational_selected_case_id')==profile['selected_case_id'],
                    'operation prior manifest source/config changed')
        prior=outputs.read(stage,'case_census.json')
        if prior is not None:
            prior=parse(prior)
            require(prior.get('identity')==identity and prior.get('inventory_sha256')==profile['pilot_inventory_sha256']
                    and prior.get('expected_cases')==len(rows),'operation prior census source/config changed')
        outputs.write(stage,'case_inventory.json',rows)
        meta={**identity,'inventory_sha256':profile['pilot_inventory_sha256'],
              'expected_rows':len(rows),'status':'running','exit_code':None,
              'operational_selected_case_id':profile['selected_case_id'],'full_study_gate':False}
        outputs.write(stage,'manifest.json',meta)
        scheduler=None
        try:
            # The original constructor creates no workers. Preserve all declared
            # rows before it can refuse resources, then census its empty prefix.
            outputs.check()
            if resume_gate is not None:resume_gate()
            scheduler=Scheduler(output,limits,DRIVER);outputs.check()
            if resume_gate is not None:
                # Operational wall-accounting adapter only; original transitions,
                # frozen Scheduler source and complete Config stay unchanged.
                scheduler._elapsed=_resume_elapsed
            configs=[Config.from_dict(selected['config'])]
            if resume_gate is None:scheduler.run(configs)
            else:_resume_run(scheduler,configs,resume_gate,resume_gate.output_root)
            if resume_gate is not None:resume_gate()
            outputs.check()
            require(scheduler.owned_workers_absent,'selected worker ownership not closed')
            validate_profile(profile,limits)
            reader=study.CheckedLongitudinalCases(output,{k:identity[k] for k in
                ('source_sha256','pipeline_driver_sha256','spec_sha256')})
            attempts=sorted(p for p in (output/'cases'/selected['case_id']).glob('attempt-*') if p.is_dir())
            require(attempts,'selected attempt missing')
            value=reader.validate_case(selected,attempts[-1],expected_provenance=limits.provenance)
            require(reader.result()['unique_complete_cases']==1,'individual full raw gate missing')
            if resume_gate is not None:resume_gate()
            outputs.write(outputs.root,'single-arm-result.json',{'status':'COMPLETE_INDIVIDUAL_FULL_RAW_VERIFIED',
                'profile':profile,'case_id':selected['case_id'],'manifest_sha256':file_digest(attempts[-1]/'manifest.json'),
                'summary':value['summary'],'paired_world_complete':False,'full_study_complete':False,
                'empirical_validation':False})
        finally:
            # Original census retains valid CPs, failed and the unstarted paired arm.
            if scheduler is None or scheduler.owned_workers_absent:
                outputs.check()
                census=stopped_case_census(output,rows,identity,expected_provenance=limits.provenance)
                census_sha=outputs.write(stage,'case_census.json',census)
                meta.update(status='partial',exit_code=1,partial_census_exact=True,
                            case_census_sha256=census_sha)
                outputs.write(stage,'manifest.json',meta)
    finally:
        outputs.close()
        if resume_gate is not None:resume_gate()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--population',type=int,choices=(300,),required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--runtime-limits',type=Path,required=True)
    p.add_argument('--plan-only',action='store_true')
    p.add_argument('--pair',action='store_true')
    p.add_argument('--world',type=int)
    p.add_argument('--arm',choices=SELECTED_ARMS)
    p.add_argument('--status-snapshot',type=Path)
    p.add_argument('--status-snapshot-sha256')
    a=p.parse_args(argv)
    if a.plan_only:
        require(a.world is not None and ((a.pair and a.arm is None) or (not a.pair and a.arm is not None)),
                'plan requires one world and either one exact arm or paired mode')
        require(a.status_snapshot is not None and a.status_snapshot_sha256 is not None,
                'planning requires the Root-pinned readonly status snapshot')
        limits=json.loads(a.runtime_limits.read_bytes())
        snapshot,_=_profile(a.status_snapshot,a.status_snapshot_sha256)
        if a.pair:
            profile=build_pair_profile(limits,world=a.world,status_snapshot=snapshot,
                                       status_snapshot_sha256=a.status_snapshot_sha256)
        else:
            profile=build_profile(limits,world=a.world,arm=a.arm,status_snapshot=snapshot,
                                  status_snapshot_sha256=a.status_snapshot_sha256)
        validate_profile(profile,limits)
        print(json.dumps({'status':'SOURCE_ONLY_UNISSUED','profile':profile,
              'assignment':assignment(profile,limits),'paired_world_complete':False,
              'full_study_complete':False,'paid_actions_authorized':False}),flush=True)
        return 0
    require(not a.pair and a.world is None and a.arm is None and a.status_snapshot is None
            and a.status_snapshot_sha256 is None,'execution selection belongs to sealed runtime profile')
    path=os.environ.get(ENV);pin=os.environ.get(SHA_ENV)
    require(path and pin,'root-enrolled exact single-arm runtime profile required')
    profile,observed=_profile(path,pin)
    limits=json.loads(a.runtime_limits.read_bytes())
    validate_profile(profile,limits)
    execute(profile,limits,a.output)
    require(_profile(path,pin)[1]==observed,'profile changed during operation')
    return 0

if __name__=='__main__':raise SystemExit(main())
