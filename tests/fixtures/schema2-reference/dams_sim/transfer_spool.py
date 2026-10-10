"""Execution-only, bounded immutable handoff; publication is not collector durability.

The private root collector owns ACKs. It must independently restore two verified
durable collector copies and run the checkpoint/full-raw gate before ACKing.
This module does not authenticate remote storage or replace that root gate.
Its private namespace, reservation history and ACK owner must remain intact.
An absent history is never a valid resume of an existing spool.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import builtins
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
import types
import uuid

from .storage import canonical, digest, source_hash

CONFIG_KEYS = {'stage_id','spool_dir','ack_dir','expected_source_sha256',
               'max_spool_bytes','max_transfer_bytes','deadline_utc'}
SCHEMA = 'dams-compute-only-transfer-v1'
MAX_FILES = 8192
MAX_METADATA_BYTES = 8 * 1024**2
MAX_RECORDS = 65536
_BOUND_CODEC = None
_BOUND_SOURCE_BYTES = None


def _sha(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('transfer SHA-256 differs')
    return value


def _natural(value,positive=False):
    if type(value) is not int or value<int(positive) or value>2**63-1:
        raise ValueError('transfer byte/count bound differs')
    return value


def _name(value):
    if (not isinstance(value,str) or not value or len(value)>4096 or '\\' in value
            or any(ord(c)<32 for c in value) or value.startswith('/')
            or any(p in ('','.','..') for p in value.split('/'))):
        raise ValueError('unsafe transfer relative name')
    return value


def _codec():
    # Execute the exact existing codec with its exact private dependency.
    # A pre-existing sys.modules entry is never accepted as current code.
    # No Gcloud/Store instance, credential or provider operation is created.
    global _BOUND_CODEC,_BOUND_SOURCE_BYTES
    directory=Path(__file__).resolve().parent.parent/'research_tools'
    sources={}
    for name in ('cloud_control.py','cloud_archive.py'):
        path=directory/name
        for parent in (path,*path.parents):
            if parent.is_symlink():raise ValueError('transfer codec source has a symlink ancestor')
        before=path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_METADATA_BYTES:
            raise ValueError('transfer codec source differs')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            held=os.fstat(stream.fileno());raw=stream.read(MAX_METADATA_BYTES+1);after=os.fstat(stream.fileno())
        signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if signature(before)!=signature(held) or signature(held)!=signature(after) or signature(after)!=signature(path.lstat()) or len(raw)!=before.st_size:
            raise ValueError('transfer codec source changed during read')
        sources[name]=raw
    if _BOUND_CODEC is not None:
        if sources!=_BOUND_SOURCE_BYTES:raise ValueError('transfer imported codec/dependency source changed')
        return _BOUND_CODEC
    control=types.ModuleType('_dams_transfer_bound_control');control.__file__=str(directory/'cloud_control.py')
    exec(compile(sources['cloud_control.py'],control.__file__,'exec'),control.__dict__)
    module=types.ModuleType('_dams_transfer_bound_archive');module.__file__=str(directory/'cloud_archive.py')
    def bound_import(name,*args,**kwargs):
        return control if name=='cloud_control' else builtins.__import__(name,*args,**kwargs)
    module.__dict__['__builtins__']={**vars(builtins),'__import__':bound_import}
    exec(compile(sources['cloud_archive.py'],module.__file__,'exec'),module.__dict__)
    _BOUND_SOURCE_BYTES=sources;_BOUND_CODEC=module
    return module


def _path(value):
    path=Path(value)
    if not path.is_absolute():raise ValueError('transfer operational path must be absolute')
    return _codec().lexical_path(path)


def _anchor(path):
    path=_path(path);info=path.lstat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('transfer file is not regular')
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _read(path,cap):
    before=_anchor(path)
    if before[2]>cap:raise ValueError('transfer metadata/file cap exceeded')
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        if _codec().stat_identity(os.fstat(stream.fileno()))!=before:
            raise ValueError('transfer open identity changed')
        raw=stream.read(cap+1)
        if _codec().stat_identity(os.fstat(stream.fileno()))!=before:
            raise ValueError('transfer held file changed')
    if len(raw)!=before[2] or _anchor(path)!=before:
        raise ValueError('transfer read identity changed')
    return raw


def _json(path):
    raw=_read(path,MAX_METADATA_BYTES)
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('duplicate transfer JSON field')
            result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=pairs),raw


def _hash_file(path,expected,check):
    before=_anchor(path)
    if before[2]!=expected['bytes']:raise ValueError('transfer actual source size differs')
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0));h=hashlib.sha256();length=0
    with os.fdopen(fd,'rb') as stream:
        if _codec().stat_identity(os.fstat(stream.fileno()))!=before:raise ValueError('transfer source open changed')
        while raw:=stream.read(_codec().CHUNK_BYTES):
            check();length+=len(raw);h.update(raw)
            if length>expected['bytes']:raise ValueError('transfer actual source grew')
        if _codec().stat_identity(os.fstat(stream.fileno()))!=before:raise ValueError('transfer held source changed')
    if _anchor(path)!=before or length!=expected['bytes'] or h.hexdigest()!=expected['sha256']:
        raise ValueError('transfer actual source bytes differ')


def _immutable(path,raw):
    _path(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    fd=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:os.fsync(fd)
    finally:os.close(fd)


class TransferSpool:
    """One shared stage lock, immutable generations, conservative charged holds.

    Reservation bytes include a worst-case compressed group plus metadata;
    they are never refunded, even after ACK or interrupted publication. This
    is a transport allowance, not the financial ledger or measured network I/O.
    """
    @classmethod
    def from_environment(cls,*,expected_source_sha256,deadline_monotonic=None):
        name=os.environ.get('DAMS_TRANSFER_CONFIG')
        if name is None:return None
        return cls(name,expected_source_sha256=expected_source_sha256,deadline_monotonic=deadline_monotonic)

    def __init__(self,config_file,*,expected_source_sha256,deadline_monotonic=None):
        self.local_deadline=deadline_monotonic
        self.config_file=_path(config_file)
        self.config,self.config_raw=_json(self.config_file)
        if not isinstance(self.config,dict) or set(self.config)!=CONFIG_KEYS:
            raise ValueError('transfer config fields differ')
        c=self.config
        if (not isinstance(c['stage_id'],str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',c['stage_id']) is None):
            raise ValueError('transfer stage identifier differs')
        if _sha(c['expected_source_sha256'])!=_sha(expected_source_sha256) or source_hash()!=expected_source_sha256:
            raise ValueError('transfer executing source differs')
        self.source=expected_source_sha256
        self.root=_path(c['spool_dir']);self.acks=_path(c['ack_dir'])
        if self.root==self.acks or self.root in self.acks.parents or self.acks in self.root.parents:
            raise ValueError('transfer spool and ACK namespaces must be separate')
        self.max_spool=_natural(c['max_spool_bytes'],True)
        self.max_transfer=_natural(c['max_transfer_bytes'],True)
        self.phase=None
        phase_file=self.config_file.parent/'phase-storage-config.json'
        if phase_file.exists():
            from .phase_storage import PhaseGuard
            self.phase=PhaseGuard(phase_file)
            if self.root!=self.phase.spool or self.max_spool>self.phase.value['spool_bytes']:
                raise ValueError('bounded spool/cumulative raw allowance differs')
        if not isinstance(c['deadline_utc'],str):raise ValueError('transfer deadline differs')
        try:date=datetime.fromisoformat(c['deadline_utc'].replace('Z','+00:00'))
        except ValueError:raise ValueError('transfer deadline differs') from None
        if date.tzinfo is None or date.utcoffset().total_seconds()!=0:
            raise ValueError('transfer deadline must be UTC')
        self.deadline=date.timestamp()
        self.codec_sha=digest(_read(Path(_codec().__file__).absolute(),MAX_METADATA_BYTES))
        self.dependencies_sha={name:digest(raw) for name,raw in _BOUND_SOURCE_BYTES.items()}
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.acks.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.root_anchor=self._directory(self.root);self.ack_anchor=self._directory(self.acks)
        with self._locked():
            binding=self.root/'binding.json'
            expected={'schema':SCHEMA,'config_sha256':digest(self.config_raw),'config':c,'codec_sha256':self.codec_sha,
                      'codec_dependencies_sha256':self.dependencies_sha}
            if binding.exists():
                if _json(binding)[0]!=expected:raise ValueError('transfer binding changed; counters cannot reset')
                if not (self.root/'reservations').is_dir() or not (self.root/'groups').is_dir():
                    raise ValueError('transfer history missing; cannot reset')
            else:
                if {p.name for p in self.root.iterdir()}!={'lock'}:
                    raise ValueError('transfer binding missing from existing spool; cannot reset')
                _immutable(binding,canonical(expected)+b'\n')
                (self.root/'reservations').mkdir();(self.root/'groups').mkdir()
                _immutable(self.root/'accounting.json',canonical({'schema':SCHEMA,'sequence':0,
                           'previous_sha256':None,'reserved_bytes':0})+b'\n')
            self._history()
            self._reconcile_acks()
            self._capacity(0)

    @staticmethod
    def _directory(path):
        _path(path);s=path.lstat()
        if not stat.S_ISDIR(s.st_mode):raise ValueError('transfer namespace is not a directory')
        return s.st_dev,s.st_ino

    def _check(self,deadline_monotonic=None):
        if self.local_deadline is not None:
            deadline_monotonic=min(self.local_deadline,deadline_monotonic) if deadline_monotonic is not None else self.local_deadline
        if time.time()>=self.deadline or deadline_monotonic is not None and time.monotonic()>=deadline_monotonic:
            raise TimeoutError('transfer original absolute/attempt deadline reached; evidence retained')
        if (_read(self.config_file,MAX_METADATA_BYTES)!=self.config_raw or source_hash()!=self.source
                or digest(_read(Path(_codec().__file__).absolute(),MAX_METADATA_BYTES))!=self.codec_sha):
            raise ValueError('transfer frozen config/source/codec changed')
        if self._directory(self.root)!=self.root_anchor or self._directory(self.acks)!=self.ack_anchor:
            raise ValueError('transfer namespace identity changed')

    @contextmanager
    def _locked(self):
        lock=self.root/'lock';_path(lock)
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('unsafe transfer lock')
            # Shared publishers wait only inside the original absolute and
            # whole-attempt bounds. Lock contention creates no new retry.
            while True:
                self._check()
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:time.sleep(.01)
            if (os.fstat(fd).st_dev,os.fstat(fd).st_ino)!=(lock.stat().st_dev,lock.stat().st_ino):
                raise ValueError('transfer lock identity changed')
            yield
        finally:os.close(fd)

    def _usage(self):
        count=total=0
        for parent,dirs,files in os.walk(self.root,followlinks=False):
            self._directory(Path(parent))
            for name in dirs:self._directory(Path(parent)/name)
            for name in files:
                count+=1;total+=_anchor(Path(parent)/name)[2]
                if count>MAX_RECORDS*4+MAX_FILES*MAX_RECORDS:
                    raise ValueError('transfer namespace file bound exceeded')
                if total>self.max_spool:raise RuntimeError('transfer spool byte bound exceeded; evidence retained')
        return total

    def _capacity(self,extra):
        if self._usage()+extra>self.max_spool:
            if self.phase is not None:
                from .phase_storage import PhaseCensored
                raise PhaseCensored('bounded encoded storage exhausted; no new allowance after ACK')
            raise RuntimeError('transfer spool capacity exhausted; unACKed evidence retained')
        if shutil.disk_usage(self.root).free<extra+8192:
            raise RuntimeError('transfer filesystem capacity exhausted; evidence retained')

    def _history(self):
        directory=self.root/'reservations';self._directory(directory)
        records=sorted(directory.iterdir());previous=None;total=0;result={}
        account,account_raw=_json(self.root/'accounting.json')
        if (not isinstance(account,dict) or set(account)!={'schema','sequence','previous_sha256','reserved_bytes'}
                or account['schema']!=SCHEMA):raise ValueError('transfer accounting cursor differs')
        _natural(account['sequence']);_natural(account['reserved_bytes'])
        if account['sequence']>len(records):raise ValueError('transfer history rolled back; counters cannot reset')
        if account['sequence']==0 and (account['previous_sha256'] is not None or account['reserved_bytes']!=0):
            raise ValueError('transfer accounting initial cursor differs')
        if len(records)>MAX_RECORDS:raise ValueError('transfer reservation count exceeded')
        keys={'schema','sequence','previous_sha256','stage_id','source_sha256','generation','reserved_bytes'}
        for sequence,path in enumerate(records,1):
            if path.name!=f'{sequence:08d}.json':raise ValueError('transfer history incomplete; cannot reset')
            value,raw=_json(path)
            if (not isinstance(value,dict) or set(value)!=keys or value['schema']!=SCHEMA
                    or type(value['sequence']) is not int or value['sequence']!=sequence
                    or value['previous_sha256']!=previous or value['stage_id']!=self.config['stage_id']
                    or value['source_sha256']!=self.source or value['generation'] in result):
                raise ValueError('transfer reservation history changed')
            _sha(value['generation']);total+=_natural(value['reserved_bytes'],True)
            if total>self.max_transfer:raise ValueError('transfer cumulative allowance exceeded')
            result[value['generation']]=value;previous=digest(raw)
            if sequence==account['sequence'] and (total!=account['reserved_bytes'] or previous!=account['previous_sha256']):
                raise ValueError('transfer accounting/history prefix differs')
        groups=self.root/'groups';self._directory(groups)
        for path in groups.iterdir():
            self._directory(path)
            if path.name not in result:raise ValueError('transfer generation has no reservation')
        self.records=result;self.reserved=total;self.previous=previous
        if len(records)>account['sequence']:
            # An interrupted reservation remains charged. Advance only through
            # the complete checked append-only prefix; never decrease counters.
            updated={'schema':SCHEMA,'sequence':len(records),'previous_sha256':previous,'reserved_bytes':total}
            temporary=self.root/('.accounting-'+uuid.uuid4().hex)
            raw=canonical(updated)+b'\n';self._capacity(len(raw))
            _immutable(temporary,raw)
            if _read(self.root/'accounting.json',MAX_METADATA_BYTES)!=account_raw:
                raise ValueError('transfer accounting cursor changed')
            os.replace(temporary,self.root/'accounting.json')
            fd=os.open(self.root,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(fd)
            finally:os.close(fd)
        return result

    def publish_checkpoint(self,attempt,item,index,metadata,*,deadline_monotonic=None):
        if not isinstance(item,dict) or len(item.get('files',[]))!=2:
            raise ValueError('transfer requires complete legacy checkpoint JSON/SQLite pair; native CAS unsupported')
        if index.get('snapshots',[None])[0]!=item:
            raise ValueError('transfer sealed checkpoint index/item differs')
        names=[part['file'] for part in item['files']]+['checkpoint-index.json']
        expected={part['file']:{'sha256':part['sha256'],'bytes':part['bytes']} for part in item['files']}
        index_raw=canonical(index)+b'\n'
        expected['checkpoint-index.json']={'sha256':digest(index_raw),'bytes':len(index_raw)}
        return self._publish(attempt,'checkpoint',item['day'],metadata,names,expected,
                             {'item':item,'index':index},deadline_monotonic)

    def publish_final(self,attempt,metadata,*,deadline_monotonic=None):
        if metadata.get('status')!='complete' or metadata.get('exit_code')!=0:
            raise ValueError('transfer final group requires completed manifest')
        attempt=Path(attempt).absolute()
        names=sorted(metadata['output_sha256'])+['manifest.json']
        expected={name:{'sha256':sha,'bytes':_anchor(attempt/name)[2]} for name,sha in metadata['output_sha256'].items()}
        raw=canonical(metadata)+b'\n';expected['manifest.json']={'sha256':digest(raw),'bytes':len(raw)}
        actual={str(p.relative_to(attempt)) for p in attempt.rglob('*') if p.is_file()}
        if actual!=set(names):raise ValueError('transfer completed raw roster differs')
        day=metadata['config']['days']
        return self._publish(attempt,'final',day,metadata,names,expected,{'manifest':metadata},deadline_monotonic)

    def _publish(self,attempt,kind,day,metadata,names,expected,sealed,deadline_monotonic):
        attempt=_path(Path(attempt).absolute());self._directory(attempt)
        if (attempt==self.root or attempt in self.root.parents or self.root in attempt.parents
                or attempt==self.acks or attempt in self.acks.parents or self.acks in attempt.parents):
            raise ValueError('transfer namespace must be outside scientific outputs')
        if metadata.get('source_sha256')!=self.source:raise ValueError('transfer group source differs')
        _sha(metadata['config_sha256']);_natural(day)
        if metadata['config_sha256']!=digest(canonical(metadata['config'])):
            raise ValueError('transfer group config differs')
        if not 1<=len(names)<=MAX_FILES or len(set(names))!=len(names):raise ValueError('transfer group file roster differs')
        for name in names:_name(name);_sha(expected[name]['sha256']);_natural(expected[name]['bytes'])
        case=metadata.get('scientific_case_id',metadata['config_sha256']);_name(case);_name(attempt.name)
        if Path(case).name!=case:raise ValueError('transfer scientific case identifier must be a single component')
        identity={'stage_id':self.config['stage_id'],'source_sha256':self.source,'config_sha256':metadata['config_sha256'],
                  'case_id':case,'attempt':attempt.name,'kind':kind,'day':day}
        generation=digest(canonical(identity))
        group=self.root/'groups'/generation
        with self._locked():
            self._check(deadline_monotonic);self._history();self._reconcile_acks()
            if generation in self.records:
                if not (group/'seal.json').exists():raise RuntimeError('prior transfer publication incomplete; no fresh retry')
                manifest,raw,seal=self._manifest(group)
                for part in manifest['files']:
                    if part['name']=='checkpoint-index.json' and kind=='checkpoint':continue
                    if expected.get(part['name'])!={'sha256':part['codec']['raw_sha256'],'bytes':part['codec']['raw_bytes']}:
                        raise ValueError('same transfer generation differs from prior source bytes')
                for name in names:_hash_file(attempt/name,expected[name],lambda:self._check(deadline_monotonic))
                if not (group/'ACK.json').exists():self._validate_parts(group,manifest,deadline_monotonic)
                self._check(deadline_monotonic)
                return seal
            anchors={name:_anchor(attempt/name) for name in names}
            chunks=sum(math.ceil(expected[name]['bytes']/_codec().CHUNK_BYTES) for name in names)
            metadata_upper=len(canonical({'identity':identity,'sealed':sealed,'names':names}))+4096+len(names)*2048+chunks*768
            if metadata_upper>MAX_METADATA_BYTES:raise ValueError('transfer group metadata allowance exceeded')
            upper=sum(expected[name]['bytes'] for name in names)+chunks*4096+metadata_upper*2+4096
            if self.reserved+upper>self.max_transfer:
                if self.phase is not None:
                    from .phase_storage import PhaseCensored
                    raise PhaseCensored('bounded cumulative raw publication exhausted; no reset/refund')
                raise RuntimeError('transfer cumulative allowance exhausted; no reset/refund')
            self._capacity(upper+2*_codec().MAX_ENCODED_CHUNK)
            if len(self.records)>=MAX_RECORDS:raise RuntimeError('transfer reservation bound exhausted')
            record={'schema':SCHEMA,'sequence':len(self.records)+1,'previous_sha256':self.previous,
                    'stage_id':self.config['stage_id'],'source_sha256':self.source,'generation':generation,'reserved_bytes':upper}
            _immutable(self.root/'reservations'/f'{record["sequence"]:08d}.json',canonical(record)+b'\n')
            self._history()
            group.mkdir();(group/'parts').mkdir();files=[]
            for number,name in enumerate(names):
                self._check(deadline_monotonic)
                if _anchor(attempt/name)!=anchors[name]:raise ValueError('transfer source group changed before encoding')
                part_directory=group/'parts'/f'{number:05d}';part_directory.mkdir()
                def publish(encoded,chunk):
                    self._check(deadline_monotonic)
                    raw=_read(encoded,_codec().MAX_ENCODED_CHUNK)
                    if len(raw)!=chunk['encoded_bytes'] or digest(raw)!=chunk['encoded_sha256']:
                        raise ValueError('transfer encoded part bytes differ')
                    self._capacity(len(raw))
                    _immutable(part_directory/f'{chunk["offset"]:020d}.z',raw)
                codec=_codec().encode_file(attempt/name,publish,max_raw_bytes=expected[name]['bytes'],
                                           minimum_free_bytes=8192,scratch=group)
                if {'sha256':codec['raw_sha256'],'bytes':codec['raw_bytes']}!=expected[name]:
                    raise ValueError('transfer actual source bytes differ from sealed group')
                files.append({'name':name,'part_directory':f'parts/{number:05d}','codec':codec})
            if any(_anchor(attempt/name)!=anchor for name,anchor in anchors.items()):
                raise ValueError('transfer source group changed during publication')
            group_sha=digest(canonical([{'name':p['name'],'bytes':p['codec']['raw_bytes'],'sha256':p['codec']['raw_sha256']} for p in files]))
            manifest={'schema':SCHEMA,'generation':generation,**identity,'group_sha256':group_sha,
                      'sealed':sealed,'files':files,'codec_source_sha256':self.codec_sha,
                      'codec_dependencies_sha256':self.dependencies_sha}
            raw=canonical(manifest)+b'\n'
            if len(raw)>metadata_upper:raise ValueError('transfer descriptor exceeds reserved metadata')
            self._check(deadline_monotonic);self._capacity(len(raw)+1024)
            _immutable(group/'manifest.json',raw)
            seal={'schema':SCHEMA,'generation':generation,'manifest_sha256':digest(raw),
                  'group_sha256':group_sha,'manifest_bytes':len(raw),
                  'encoded_bytes':sum(p['codec']['encoded_bytes'] for p in files)}
            if seal['encoded_bytes']+len(raw)+len(canonical(seal))+1>upper:
                raise ValueError('transfer actual publication exceeds charged reservation')
            self._validate_parts(group,manifest,deadline_monotonic)
            self._check(deadline_monotonic)
            _immutable(group/'seal.json',canonical(seal)+b'\n')
            self._capacity(0)
            return seal

    def _manifest(self,group):
        self._directory(group);manifest,raw=_json(group/'manifest.json');seal,_=_json(group/'seal.json')
        if (seal.get('schema')!=SCHEMA or seal.get('generation')!=group.name
                or seal.get('manifest_sha256')!=digest(raw) or seal.get('manifest_bytes')!=len(raw)
                or manifest.get('generation')!=group.name or manifest.get('schema')!=SCHEMA
                or manifest.get('source_sha256')!=self.source or manifest.get('stage_id')!=self.config['stage_id']
                or manifest.get('codec_source_sha256')!=self.codec_sha
                or manifest.get('codec_dependencies_sha256')!=self.dependencies_sha):
            raise ValueError('transfer sealed manifest binding differs')
        files=manifest['files']
        if not isinstance(files,list) or not 1<=len(files)<=MAX_FILES:raise ValueError('transfer manifest roster differs')
        names=[];encoded=0
        for number,part in enumerate(files):
            if set(part)!={'name','part_directory','codec'} or part['part_directory']!=f'parts/{number:05d}':
                raise ValueError('transfer part namespace differs')
            names.append(_name(part['name']));_codec().validate_manifest(part['codec']);encoded+=part['codec']['encoded_bytes']
        if len(set(names))!=len(names):raise ValueError('duplicate transfer file')
        group_sha=digest(canonical([{'name':p['name'],'bytes':p['codec']['raw_bytes'],'sha256':p['codec']['raw_sha256']} for p in files]))
        if manifest['group_sha256']!=group_sha or seal['group_sha256']!=group_sha or seal['encoded_bytes']!=encoded:
            raise ValueError('transfer whole group commitment differs')
        return manifest,raw,seal

    def _validate_parts(self,group,manifest,deadline_monotonic=None):
        expected={'manifest.json','seal.json'} if (group/'seal.json').exists() else {'manifest.json'}
        expected|={'ACK.json'} if (group/'ACK.json').exists() else set()
        if {p.name for p in group.iterdir()}!=expected|{'parts'}:
            raise ValueError('transfer generation contains foreign objects')
        self._directory(group/'parts')
        if {p.name for p in (group/'parts').iterdir()}!={f'{i:05d}' for i in range(len(manifest['files']))}:
            raise ValueError('transfer encoded file roster differs')
        for part in manifest['files']:
            folder=group/part['part_directory'];self._directory(folder)
            if {p.name for p in folder.iterdir()}!={f'{c["offset"]:020d}.z' for c in part['codec']['chunks']}:
                raise ValueError('transfer encoded chunk roster differs')
            h=hashlib.sha256();length=0
            for chunk in part['codec']['chunks']:
                self._check(deadline_monotonic)
                raw=_codec().decode_chunk(_read(folder/f'{chunk["offset"]:020d}.z',_codec().MAX_ENCODED_CHUNK),chunk)
                h.update(raw);length+=len(raw)
            if h.hexdigest()!=part['codec']['raw_sha256'] or length!=part['codec']['raw_bytes']:
                raise ValueError('transfer decoded flat file differs')

    def _reconcile_acks(self):
        for generation in self.records:
            group=self.root/'groups'/generation
            ack=self.acks/f'{generation}.json'
            if not ack.exists() and not ack.is_symlink():continue
            manifest,raw,seal=self._manifest(group)
            value,ackraw=_json(ack)
            keys={'schema','stage_id','source_sha256','generation','manifest_sha256','group_sha256','gate','copies'}
            gate='checkpoint-full-state' if manifest['kind']=='checkpoint' else 'final-full-raw'
            if (not isinstance(value,dict) or set(value)!=keys or value['schema']!=SCHEMA
                    or value['stage_id']!=self.config['stage_id'] or value['source_sha256']!=self.source
                    or value['generation']!=generation or value['manifest_sha256']!=seal['manifest_sha256']
                    or value['group_sha256']!=seal['group_sha256'] or value['gate']!=gate):
                raise ValueError('transfer ACK binding/gate differs; evidence retained')
            copies=value['copies'];ids=[]
            if not isinstance(copies,list) or len(copies)!=2:raise ValueError('transfer ACK requires two independent restoration receipts')
            for copy in copies:
                if not isinstance(copy,dict) or set(copy)!={'restoration_id','receipt_sha256','group_sha256','gate'}:
                    raise ValueError('transfer ACK restoration receipt differs')
                _name(copy['restoration_id']);_sha(copy['receipt_sha256']);ids.append(copy['restoration_id'])
                if copy['group_sha256']!=seal['group_sha256'] or copy['gate']!=gate:
                    raise ValueError('transfer ACK restoration result differs')
            if len(set(ids))!=2 or copies[0]['receipt_sha256']==copies[1]['receipt_sha256']:
                raise ValueError('transfer ACK restorations are not distinct')
            retained=group/'ACK.json'
            if retained.exists():
                if _read(retained,MAX_METADATA_BYTES)!=ackraw:raise ValueError('transfer retained ACK changed')
            else:
                self._validate_parts(group,manifest)
                self._capacity(len(ackraw))
                _immutable(retained,ackraw)
            # ACK evidence and manifest remain. Crash-partial deletion resumes
            # only inside the exact previously verified generation namespace.
            if self.phase is not None:
                # In this finite phase retain every encoded occurrence.  Its
                # hard filesystem cap therefore also bounds cumulative cache
                # payload; ACK cannot make a new storage allowance.
                self.phase.check()
                continue
            parts=group/'parts'
            if parts.exists():
                allowed={f'{i:05d}':{f'{c["offset"]:020d}.z' for c in p['codec']['chunks']} for i,p in enumerate(manifest['files'])}
                self._directory(parts)
                for directory in parts.iterdir():
                    self._directory(directory)
                    if directory.name not in allowed:raise ValueError('foreign ACKed transfer directory')
                    for file in directory.iterdir():
                        self._check()
                        _anchor(file)
                        if file.name not in allowed[directory.name]:raise ValueError('foreign ACKed transfer part')
                        file.unlink()
                    directory.rmdir()
                parts.rmdir()
