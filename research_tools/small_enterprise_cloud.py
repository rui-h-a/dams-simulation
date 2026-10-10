"""One source-bound operational arm of the unchanged small-enterprise study.

Only the existing Scheduler executes cases. All eight pilot rows remain declared.
This does not complete a pair, redefine the study, or authorize paid resources.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
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

SCHEMA = 'DAMS-small-enterprise-cloud-arm-1'
FIELDS = {'schema','source_sha256','pipeline_driver_sha256','study_entry_sha256',
          'study_sha256','pilot_inventory_sha256','population','days','seed',
          'selected_world','selected_arm','selected_case_id','selected_config_sha256'}
CORE = study.CORE
DRIVER = study.DRIVER
STUDY_IMPORT_SHA = '2732dc6ff3d73e9848c022522e02a744ca0e77b775d472227585e8229784494a'
ENV = 'DAMS_SMALL_ENTERPRISE_CLOUD_PROFILE'
SHA_ENV = 'DAMS_SMALL_ENTERPRISE_CLOUD_PROFILE_SHA256'

def require(ok, message):
    if not ok: raise ValueError(message)

def plan(limits):
    limits = limits if isinstance(limits,RuntimeLimits) else RuntimeLimits.from_dict(limits)
    require(limits.journal_chunk_bytes == 1048576 and limits.per_world_rss_bytes is not None,
            'original small-study lossless journal/RSS contract missing')
    require(limits.max_output_bytes>=study.CAPS[300] and limits.batch_max_output_bytes>=study.CAPS[300],
            'single complete-case resource allowance below original16GiB envelope')
    template, declaration = study.spec(300)
    require(template.days == 1917 and declaration['full_observation_years'] == 5
            and declaration['pilot_world_ids'] == [42000,42001,42002,42003]
            and declaration['arms'] == ['startup-existing','startup-founding'],
            'literal small-enterprise study changed')
    rows = [{k:r[k] for k in ('case_id','config','tags')}
            for w in declaration['pilot_world_ids'] for r in study.rows(300,w,limits)]
    require(len(rows)==8 and len({r['case_id'] for r in rows})==8,'pilot inventory differs')
    return limits, declaration, rows

def build_profile(limits, *, world, arm):
    _, declaration, rows = plan(limits)
    selected = [r for r in rows if r['tags']['world']==world and r['tags']['arm_id']==arm]
    require(type(world) is int and len(selected)==1,'selected literal pilot arm unavailable')
    row=selected[0]
    return {'schema':SCHEMA,'source_sha256':CORE,'pipeline_driver_sha256':DRIVER,
            'study_entry_sha256':STUDY_IMPORT_SHA,'study_sha256':digest(canonical(declaration)),
            'pilot_inventory_sha256':digest(canonical(rows)),'population':300,'days':1917,
            'seed':declaration['seed'],'selected_world':world,'selected_arm':arm,
            'selected_case_id':row['case_id'],'selected_config_sha256':digest(canonical(row['config']))}

def validate_profile(profile, limits):
    require(isinstance(profile,dict) and set(profile)==FIELDS and profile['schema']==SCHEMA,
            'unsupported small-enterprise cloud profile')
    require(source_hash()==CORE and driver_hash()==DRIVER
            and file_digest(Path(study.__file__))==STUDY_IMPORT_SHA,'executing f9 science/study entry differs')
    require(type(profile['selected_world']) is int and type(profile['population']) is int
            and type(profile['days']) is int and type(profile['seed']) is int,'profile numeric types differ')
    expected=build_profile(limits,world=profile['selected_world'],arm=profile['selected_arm'])
    require(profile==expected,'source/study/full calendar/seed/literal arm/config pin differs')
    return profile

def assignment(profile, limits):
    validate_profile(profile,limits)
    _, declaration, rows=plan(limits)
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
    def __init__(self, output):
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
            self.stage = self._child(root, 'pilot-operation')
            for directory,names in ((root,('small-operation.json','single-arm-result.json')),
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


def execute(profile, limits, output):
    validate_profile(profile,limits);limits,declaration,rows=plan(limits)
    output=Path(output).absolute()
    outputs=_OperationOutputs(output)
    import fcntl
    try:
      lock_fd=outputs.root.open('.small-operation.lock',os.O_RDWR|os.O_CREAT)
      with os.fdopen(lock_fd,'a+b') as owner:
        require(stat.S_ISREG(os.fstat(owner.fileno()).st_mode) and os.fstat(owner.fileno()).st_nlink==1,'operation lock is not owned regular bytes')
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        intended={'profile':profile,'full_pilot_assignment':assignment(profile,limits)}
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
            outputs.check();scheduler=Scheduler(output,limits,DRIVER);outputs.check()
            scheduler.run([Config.from_dict(selected['config'])])
            outputs.check()
            require(scheduler.owned_workers_absent,'selected worker ownership not closed')
            validate_profile(profile,limits)
            reader=study.CheckedLongitudinalCases(output,identity)
            attempts=sorted((output/'cases'/selected['case_id']).glob('attempt-*'))
            require(attempts,'selected attempt missing')
            value=reader.validate_case(selected,attempts[-1],expected_provenance=limits.provenance)
            require(reader.result()['unique_complete_cases']==1,'individual full raw gate missing')
            outputs.write(outputs.root,'single-arm-result.json',{'status':'COMPLETE_INDIVIDUAL_FULL_RAW_VERIFIED',
                'profile':profile,'case_id':selected['case_id'],'manifest_sha256':file_digest(attempts[-1]/'manifest.json'),
                'summary':value['summary'],'paired_world_complete':False,'full_study_complete':False,
                'empirical_validation':False})
        finally:
            # Original census retains valid CPs, failed and all seven unstarted rows.
            if scheduler is None or scheduler.owned_workers_absent:
                outputs.check()
                census=stopped_case_census(output,rows,identity,expected_provenance=limits.provenance)
                census_sha=outputs.write(stage,'case_census.json',census)
                meta.update(status='partial',exit_code=1,partial_census_exact=True,
                            case_census_sha256=census_sha)
                outputs.write(stage,'manifest.json',meta)
    finally: outputs.close()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--population',type=int,choices=(300,),required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--runtime-limits',type=Path,required=True)
    a=p.parse_args(argv)
    path=os.environ.get(ENV);pin=os.environ.get(SHA_ENV)
    require(path and pin,'root-enrolled exact single-arm runtime profile required')
    profile,observed=_profile(path,pin)
    limits=json.loads(a.runtime_limits.read_bytes())
    validate_profile(profile,limits)
    execute(profile,limits,a.output)
    require(_profile(path,pin)[1]==observed,'profile changed during operation')
    return 0

if __name__=='__main__':raise SystemExit(main())
