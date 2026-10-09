"""Create two fixed-size local guest filesystems on the existing boot disk.

No provider/storage service, credentials, new budget, or scientific execution.
Fresh activation only.  A failed setup is retained for root reconciliation.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time


def blob(v): return (json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def require(ok, reason):
    if not ok: raise ValueError(reason)


def prepare(work, runtime, *, run=subprocess.run):
    from dams_sim.storage import source_hash
    from dams_sim.phase_storage import SCHEMA, PhaseGuard
    limits = runtime['runtime_limits']
    cap = limits.get('phase_max_output_bytes', 0)
    if not cap: return None
    require(os.name == 'posix' and os.uname().sysname == 'Linux' and os.geteuid() == 0,
            'bounded guest storage requires Linux root and real filesystem readback')
    work = Path(work).absolute()
    for p in (work, *work.parents): require(stat.S_ISDIR(p.lstat().st_mode), 'phase storage ancestor differs')
    deadline = __import__('datetime').datetime.fromisoformat(runtime['deadline_utc'].replace('Z','+00:00')).timestamp()
    def command(argv):
        remaining = deadline-time.time()
        require(remaining>2, 'original node deadline reached during filesystem preparation')
        result = run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     timeout=min(30,remaining-1), check=False)
        require(result.returncode == 0, 'bounded guest filesystem command failed; owned setup retained')
        return result.stdout
    def binary(name):
        for p in (Path('/usr/sbin')/name, Path('/usr/bin')/name, Path('/sbin')/name, Path('/bin')/name):
            if p.is_file(): return str(p)
        raise ValueError('fixed guest filesystem utility missing: '+name)
    receipt={'schema':SCHEMA,'source_sha256':source_hash(),'runtime_sha256':hashlib.sha256(blob(runtime)).hexdigest(),
             'physical_capacity_alone_is_not_logical_byte_proof':True,'science_complete':False}
    for key, target, size in (('raw',work/'output',cap),('spool',work/'spool',limits['phase_spool_bytes'])):
        image=work/('phase-'+key+'.ext4')
        require(not os.path.lexists(image) and not os.path.lexists(target), 'phase filesystem exists; no automatic retry')
        fd=os.open(image,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try: os.ftruncate(fd,size);os.fsync(fd)
        finally: os.close(fd)
        os.mkdir(target,0o700)
        command([binary('mkfs.ext4'),'-q','-F','-m','0','-E','lazy_itable_init=0,lazy_journal_init=0',str(image)])
        command([binary('mount'),'-t','ext4','-o','loop,nosuid,nodev,noexec',str(image),str(target)])
        data=json.loads(command([binary('findmnt'),'-J','--target',str(target),'--output','TARGET,SOURCE,FSTYPE,OPTIONS']))
        rows=data.get('filesystems',[])
        require(len(rows)==1 and rows[0]['target']==str(target) and rows[0]['fstype']=='ext4'
                and {'rw','nosuid','nodev','noexec'}<=set(rows[0]['options'].split(',')), 'phase mount readback differs')
        loops=json.loads(command([binary('losetup'),'--json','--list','--output','NAME,BACK-FILE,SIZELIMIT']))
        bound=[r for r in loops['loopdevices'] if r['name']==rows[0]['source']]
        require(len(bound)==1 and bound[0]['back-file']==str(image)
                and int(bound[0]['sizelimit']) in (0,size), 'phase loop backing differs')
        block_size=int(command([binary('blockdev'),'--getsize64',rows[0]['source']]).strip())
        require(block_size==size and image.stat().st_size==size, 'phase block capacity differs')
        fs=os.statvfs(target);total=fs.f_frsize*fs.f_blocks
        require(0<total<=size and target.stat().st_dev!=work.stat().st_dev, 'phase hard-cap filesystem not mounted')
        # ext4 makes an empty lost+found directory; it is not source data.
        require(not any(p.is_file() for p in target.rglob('*')), 'phase filesystem not initially empty')
        lost=target/'lost+found'
        if lost.is_dir():
            require(not any(lost.iterdir()), 'new filesystem recovery directory is not empty')
            lost.rmdir()
        receipt[key]={'target':str(target),'filesystem':'ext4','device':target.stat().st_dev,
                      'capacity_bytes':total,'image_bytes':size,'image_identity':[image.stat().st_dev,image.stat().st_ino,size]}
    require(receipt['raw']['device']!=receipt['spool']['device'],'phase filesystems share one device')
    reserve=work/'output'/'.phase-metadata-reserve'
    fd=os.open(reserve,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        # Not sparse: this filler protects terminal metadata after ENOSPC.
        os.posix_fallocate(fd,0,limits['phase_metadata_reserve_bytes']);os.fsync(fd);s=os.fstat(fd)
    finally: os.close(fd)
    require(s.st_blocks*512>=s.st_size,'phase metadata reserve is sparse')
    receipt['metadata_reserve_identity']=[s.st_dev,s.st_ino,s.st_size]
    minimum=limits.get('min_free_disk_bytes',0)
    require(type(minimum)is int and minimum>=0,'guest minimum free bytes must be an integer')
    fs=os.statvfs(work/'output');free=fs.f_bavail*fs.f_frsize
    require(free>=minimum+limits['phase_checkpoint_headroom_bytes'],
            'guest minimum free plus checkpoint headroom cannot fit phase filesystem')
    receipt['guest_min_free_disk_bytes']=minimum
    receipt['raw_available_after_metadata_reserve_bytes']=free
    receipt['checkpoint_headroom_bytes']=limits['phase_checkpoint_headroom_bytes']
    receipt_path=work/'phase-storage-receipt.json'
    fd=os.open(receipt_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as out:out.write(blob(receipt));out.flush();os.fsync(out.fileno())
    options={'schema':SCHEMA,'output_dir':str(work/'output'),'spool_dir':str(work/'spool'),
             'raw_bytes':cap,'spool_bytes':limits['phase_spool_bytes'],
             'checkpoint_headroom_bytes':limits['phase_checkpoint_headroom_bytes'],
             'metadata_reserve_bytes':limits['phase_metadata_reserve_bytes'],
             'receipt_file':str(receipt_path),'receipt_sha256':hashlib.sha256(blob(receipt)).hexdigest(),'source_sha256':source_hash()}
    path=work/'phase-storage-config.json';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as out:out.write(blob(options));out.flush();os.fsync(out.fileno())
    parent=os.open(work,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(parent)
    finally:os.close(parent)
    PhaseGuard(path).check()
    return {'config_file':str(path),'config_sha256':hashlib.sha256(blob(options)).hexdigest(),**receipt}
