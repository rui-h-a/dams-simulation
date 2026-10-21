#!/bin/bash
# Root has already uploaded the approved package. No fetch/auth/service start.
set -euo pipefail
exec /usr/bin/python3 -I -B - "$0" "$@" <<'PY'
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

class Refusal(ValueError):
    pass

def require(value):
    if not value:
        raise Refusal('fixed prepare contract refused')

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'

def unique(pairs):
    result={}
    for key,value in pairs:
        require(key not in result);result[key]=value
    return result

def parse(raw):
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(Refusal()))

def identity(info):
    return tuple(getattr(info,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_nlink'))

def absolute(value):
    require(isinstance(value,str) and value.startswith('/') and '\x00' not in value)
    require(all(part not in ('','.','..') for part in value.split('/')[1:]))
    return Path(value)

def dir_observation(path):
    observed={}
    for node in reversed((path,*path.parents)):
        info=node.lstat();require(stat.S_ISDIR(info.st_mode))
        observed[node]=(info.st_dev,info.st_ino)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    require((os.fstat(fd).st_dev,os.fstat(fd).st_ino)==observed[path])
    return fd,observed

def dirs_check(observed):
    for path,old in observed.items():
        info=path.lstat();require(stat.S_ISDIR(info.st_mode) and (info.st_dev,info.st_ino)==old)

def read_regular(rootfd,name,bound,check,retain=True):
    require(isinstance(name,str) and len(name)<=2048 and '\x00' not in name and not name.startswith('/'))
    parts=name.split('/');require(all(part not in ('','.','..') for part in parts))
    fd=os.dup(rootfd)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        leaf=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
        try:
            before=os.fstat(leaf);require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=bound)
            digest=hashlib.sha256();chunks=[];count=0
            while True:
                check();block=os.read(leaf,min(1024*1024,bound-count+1))
                if not block:break
                count+=len(block);require(count<=bound);digest.update(block)
                if retain:chunks.append(block)
            after=os.fstat(leaf);require(identity(before)==identity(after) and count==before.st_size)
            current=os.stat(parts[-1],dir_fd=fd,follow_symlinks=False);require(identity(current)==identity(before))
            return (b''.join(chunks) if retain else count),digest.hexdigest(),identity(before)
        finally:
            os.close(leaf)
    finally:
        os.close(fd)

class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise Refusal('prepare arguments refused')

def main():
    helper=absolute(sys.argv[1]);parser=Parser(add_help=True)
    for field in ('bootstrap-sha256','manifest-sha256','source-commit','spec','deadline-utc','receipt-file'):
        parser.add_argument('--'+field,required=True)
    parser.add_argument('--source-root',default='/opt/dams')
    for field in ('scale','max-seconds','max-source-bytes','max-source-files','max-log-bytes'):
        parser.add_argument('--'+field,type=int,required=True)
    parser.add_argument('--offline',action='store_true')
    args=parser.parse_args(sys.argv[2:])
    require(re.fullmatch('[0-9a-f]{64}',args.bootstrap_sha256) and re.fullmatch('[0-9a-f]{64}',args.manifest_sha256)
            and re.fullmatch('[0-9a-f]{40}',args.source_commit))
    require(args.spec in ('validation','historical-full-study','full-study','governance-scale','scale-confirmation','longitudinal-adoption-5y','longitudinal-adoption-10y')
            and args.scale>=2 and 3<=args.max_seconds<=3600 and 1<=args.max_source_files<=32768
            and 1<=args.max_source_bytes<=1024**3 and 1<=args.max_log_bytes<=8*1024**2)
    require(not helper.is_symlink() and helper.is_file() and sha(helper.read_bytes())==args.bootstrap_sha256)
    deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00'))
    require(deadline.tzinfo is not None and deadline.utcoffset().total_seconds()==0)
    wall_start,mono_start=time.time(),time.monotonic()
    allowed=min(args.max_seconds,deadline.timestamp()-wall_start);require(allowed>2)
    cutoff=mono_start+allowed-2
    def check_time():
        now=max(time.time(),wall_start+time.monotonic()-mono_start)
        require(now<deadline.timestamp() and time.monotonic()<cutoff)
    source=absolute(args.source_root);receipt=absolute(args.receipt_file)
    require(receipt.parent!=source and source not in receipt.parent.parents)
    rootfd,root_observed=dir_observation(source)
    workfd=None
    try:
        workfd,work_observed=dir_observation(receipt.parent)
        def check():
            check_time();dirs_check(root_observed);dirs_check(work_observed)
        manifest_raw,manifest_sha,manifest_anchor=read_regular(rootfd,'source-manifest.json',min(args.max_source_bytes,8*1024**2),check)
        require(manifest_sha==args.manifest_sha256)
        manifest=parse(manifest_raw)
        require(isinstance(manifest,dict) and set(manifest)=={'commit','source_files_sha256'} and manifest['commit']==args.source_commit)
        files=manifest['source_files_sha256']
        require(isinstance(files,dict) and 1<=len(files)<=args.max_source_files
                and {'run.sh','uv.lock','pyproject.toml'}.issubset(files) and 'source-manifest.json' not in files)
        for name,digest in files.items():
            require(isinstance(digest,str) and re.fullmatch('[0-9a-f]{64}',digest))
        def verify_source():
            check();count=0;anchors={}
            for name,expected in sorted(files.items()):
                size,digest,anchor=read_regular(rootfd,name,args.max_source_bytes-count,check,retain=False)
                count+=size;require(digest==expected and count<=args.max_source_bytes);anchors[name]=anchor
            seen=set();directory_count=0
            for current,dirs,names in os.walk(source,followlinks=False):
                check();directory_count+=1;require(directory_count<=args.max_source_files+32)
                for name in dirs:require(not (Path(current)/name).is_symlink())
                dirs[:]=[name for name in dirs if name not in ('.venv','runs') or Path(current)!=source]
                for name in names:
                    path=Path(current)/name;relative=path.relative_to(source).as_posix()
                    require(relative in files or relative=='source-manifest.json');seen.add(relative)
            require(seen==set(files)|{'source-manifest.json'})
            now,now_sha,now_anchor=read_regular(rootfd,'source-manifest.json',min(args.max_source_bytes,8*1024**2),check)
            require(now==manifest_raw and now_sha==manifest_sha and now_anchor==manifest_anchor)
            return count,anchors
        total,source_anchors=verify_source()
        recipe={'schema':'DAMS-compute-only-prepared-1','source_commit':args.source_commit,'source_manifest_sha256':manifest_sha,
                'bootstrap_sha256':args.bootstrap_sha256,'spec':args.spec,'scale':args.scale,'source_files':len(files),'source_total_bytes':total,
                'dependency_inputs_sha256':{name:files[name] for name in ('run.sh','uv.lock','pyproject.toml')},
                'prepare_bounds':{name:getattr(args,name) for name in ('deadline_utc','max_seconds','max_source_files','max_source_bytes','max_log_bytes','offline')},
                'prepare_command_exit_code':0,'source_before_and_after_verified':True,
                'scope':'PREPARE_COMMAND_EXIT_AND_SOURCE_OBSERVATION_ONLY_NO_GUEST_ADMISSION',
                'guest_hardware_admitted':False,'scientific_service_started':False,'science_complete':False}
        encoded=canonical(recipe);check()
        existing_receipt=None
        if os.path.lexists(receipt):
            old,_,existing_receipt=read_regular(workfd,receipt.name,32768,check);require(old==encoded)
        # A child receives public preparation pointers only, no inherited DAMS,
        # cloud/auth/token/runtime/transport configuration values.
        env={key:value for key,value in os.environ.items() if key in ('PATH','HOME','LANG','LC_ALL','TMPDIR','TZ')}
        env.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',
                   DAMS_PACKAGED_COMMIT=args.source_commit,DAMS_SOURCE_MANIFEST=str(source/'source-manifest.json'))
        if args.offline:env['DAMS_OFFLINE_DEPENDENCIES']='1'
        command=['/bin/bash',str(source/'run.sh'),'--prepare-only','--spec',args.spec,'--scale',str(args.scale)]
        check();require(verify_source()==(total,source_anchors))
        process=None;selector=selectors.DefaultSelector();log=bytearray();error=None
        try:
            process=subprocess.Popen(command,cwd=source,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,bufsize=0)
            os.set_blocking(process.stdout.fileno(),False);selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                check()
                for key,_ in selector.select(min(.1,max(0,cutoff-time.monotonic()))):
                    block=os.read(key.fileobj.fileno(),min(65536,args.max_log_bytes-len(log)+1))
                    if not block:selector.unregister(key.fileobj);continue
                    log.extend(block);require(len(log)<=args.max_log_bytes)
            check();require(process.wait(timeout=max(.001,cutoff-time.monotonic()))==0)
            try:os.killpg(process.pid,0)
            except ProcessLookupError:pass
            else:raise Refusal('owned prepare group remains present')
        except BaseException as caught:
            error=caught
        finally:
            if process is not None:
                try:
                    process.poll()
                    if error is not None:
                        try:os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                except BaseException as caught:
                    if error is None:error=caught
                    try:
                        if process.poll() is None:process.kill()
                    except BaseException as caught:
                        if error is None:error=caught
                try:
                    process.wait(timeout=2)
                except BaseException as caught:
                    if error is None:error=caught
                finally:process.stdout.close()
            selector.close()
        if error is not None:raise error
        check();require(verify_source()==(total,source_anchors))

        if os.path.lexists(receipt):
            old,_,anchor=read_regular(workfd,receipt.name,32768,check);require(old==encoded and anchor==existing_receipt)
            reused=True
        else:
            name='.prepared-'+str(os.getpid())+'-'+str(time.monotonic_ns())+'.tmp'
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=workfd)
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())
                current,_,_=read_regular(workfd,name,32768,check);require(current==encoded)
                os.link(name,receipt.name,src_dir_fd=workfd,dst_dir_fd=workfd,follow_symlinks=False)
                os.unlink(name,dir_fd=workfd);os.fsync(workfd)
            finally:
                try:os.unlink(name,dir_fd=workfd)
                except FileNotFoundError:pass
            reused=False
        # Receipt is not a case/scientific admission; root still needs doctor,
        # provider identity, runtime, heartbeat and explicit service start.
        check();current,_,_=read_regular(workfd,receipt.name,32768,check);require(current==encoded)
        require(verify_source()==(total,source_anchors));dirs_check(root_observed);dirs_check(work_observed)
        print(json.dumps({'prepared_command_only':True,'receipt_sha256':sha(encoded),'reused_exact_receipt':reused,
                          'source_manifest_sha256':manifest_sha,'prepare_stdout_bytes':len(log),'prepare_stdout_sha256':sha(bytes(log)),
                          'guest_hardware_admitted':False,'scientific_service_started':False,'science_complete':False}))
        return 0
    finally:
        if workfd is not None:os.close(workfd)
        os.close(rootfd)

try:
    sys.exit(main())
except Exception as error:
    print('COMPUTE_ONLY_PREPARE_REFUSED:'+type(error).__name__,file=sys.stderr)
    sys.exit(2)
PY
