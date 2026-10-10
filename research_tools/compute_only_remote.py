"""Root-authorized compute-only guest I/O. No object-store credentials."""
from __future__ import annotations
import base64, hashlib, io, json, os, re, stat, subprocess, sys, tarfile, time
from pathlib import Path

BOUND=32*1024**2

def require(ok, reason):
    if not ok: raise ValueError(reason)
def sha(raw): return hashlib.sha256(raw).hexdigest()
def blob(value): return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def parse(raw):
    def pairs(items):
        d={}
        for k,v in items: require(k not in d,'duplicate JSON key');d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
def deadline(c):
    from datetime import datetime
    require(time.time()<datetime.fromisoformat(c['deadline_utc'].replace('Z','+00:00')).timestamp(),'fixed deadline reached')
def name(n):
    require(isinstance(n,str) and n and not n.startswith('/') and all(x not in ('','.','..') for x in n.split('/')),'unsafe path');return n
def anchor(st): return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns,st.st_nlink)
def path(p):
    p=Path(p);require(p.is_absolute(),'absolute path required')
    for item in reversed((p,*p.parents)):
        st=item.lstat();require(not stat.S_ISLNK(st.st_mode),'symlink ancestor')
        if item!=p:require(stat.S_ISDIR(st.st_mode),'non-directory ancestor')
    return p

def stable(p,bound=BOUND):
    p=path(p);before=p.lstat();require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=bound,'private regular byte bound')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        require(anchor(os.fstat(fd))==anchor(before),'open identity changed');raw=bytearray()
        while True:
            block=os.read(fd,min(65536,bound+1-len(raw)))
            if not block:break
            raw.extend(block);require(len(raw)<=bound,'overread')
        require(anchor(os.fstat(fd))==anchor(before) and anchor(p.lstat())==anchor(before),'read drift')
        return bytes(raw)
    finally:os.close(fd)

def write(p,raw,previous=None):
    parent=path(Path(p).parent);fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    initial=anchor(os.fstat(fd))[:2];tmp='.live-'+str(time.time_ns())
    try:
        require(previous is None or stable(p)==previous,'replacement predecessor changed')
        if previous is None:require(not os.path.lexists(p),'exclusive destination exists')
        out=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            view=memoryview(raw)
            while view:n=os.write(out,view);require(n>0,'short write');view=view[n:]
            os.fsync(out)
        finally:os.close(out)
        require(anchor(parent.lstat())[:2]==initial,'parent namespace changed')
        if previous is None:
            os.link(tmp,Path(p).name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False);os.unlink(tmp,dir_fd=fd)
        else:
            require(stable(p)==previous,'late predecessor changed');os.replace(tmp,Path(p).name,src_dir_fd=fd,dst_dir_fd=fd)
        os.fsync(fd);require(stable(p)==raw and anchor(parent.lstat())[:2]==initial,'publication drift')
    finally:
        try:os.unlink(tmp,dir_fd=fd)
        except FileNotFoundError:pass
        os.close(fd)

def context(c,get_identity=None):
    deadline(c)
    require(c['source_root']=='/opt/dams' and c['work_root']=='/var/lib/dams-compute','unsupported guest namespace')
    if get_identity is None:
        import urllib.request
        def get_identity():
            def get(n):
                req=urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/'+n,headers={'Metadata-Flavor':'Google'})
                with urllib.request.urlopen(req,timeout=3) as r:
                    raw=r.read(1025);require(len(raw)<=1024,'metadata bound');return raw.decode()
            return {'project':get('project/project-id'),'zone':get('instance/zone').rsplit('/',1)[-1],'instance_id':get('instance/id')}
    require(get_identity()=={k:c[k] for k in ('project','zone','instance_id')},'provider identity differs')

def package(c):
    root=path(c['source_root']);raw=stable(root/'source-manifest.json');require(sha(raw)==c['source_manifest_sha256'],'source manifest SHA')
    m=parse(raw);require(set(m)=={'commit','source_files_sha256'} and m['commit']==c['source_commit'],'source manifest shape')
    total=0
    for n,s in m['source_files_sha256'].items():
        data=stable(root/name(n));total+=len(data);require(total<=c['max_source_bytes'] and sha(data)==s,'source bytes mismatch')
    require(len(m['source_files_sha256'])<=c['max_source_files'],'source file bound')
    return root,m

def upload(c,raw):
    require(len(raw)<=BOUND and sha(raw)==c['input_sha256'],'upload bytes bound/hash')
    root=path(c['source_root']);members=[]
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as tar:
        for item in tar:
            name(item.name);require(item.isfile() and not item.issparse() and item.linkname=='' and item.size<=BOUND,'unsafe archive member')
            require(item.name not in [n for n,_,_ in members],'duplicate upload member')
            data=tar.extractfile(item).read(item.size+1);require(len(data)==item.size,'truncated upload member');members.append((item.name,data,item.mode))
    mapping={n:d for n,d,_ in members};require('source-manifest.json' in mapping,'upload manifest missing')
    require(sha(mapping['source-manifest.json'])==c['source_manifest_sha256'],'upload manifest SHA')
    m=parse(mapping['source-manifest.json']);require(set(m)=={'commit','source_files_sha256'} and m['commit']==c['source_commit'],'upload commit')
    require(set(mapping)==set(m['source_files_sha256'])|{'source-manifest.json'},'upload exact roster')
    require(len(m['source_files_sha256'])<=c['max_source_files'] and sum(map(len,mapping.values()))<=c['max_source_bytes'],'upload resource bound')
    for n,s in m['source_files_sha256'].items():require(sha(mapping[n])==s,'upload source mismatch')
    # Namespace is exclusively empty; an interrupted upload is not silently retried.
    require(list(root.iterdir())==[],'guest source is nonempty; root reconciliation required')
    for n,data,mode in members:
        target=root/n
        current=root
        for part in Path(n).parts[:-1]:
            current/=part
            if not os.path.lexists(current):os.mkdir(current,0o700)
            path(current);require(current.is_dir(),'upload ancestor')
        write(target,data);os.chmod(target,0o700 if mode&0o111 else 0o600)
    package(c)
    return {'source_manifest_sha256':c['source_manifest_sha256'],'source_files':len(m['source_files_sha256']),'input_sha256':sha(raw),'science_complete':False}

def spool_snapshot(c,work):
    """Read-only native reservation/manifest view. Never reconcile/delete parts."""
    import fcntl
    from dams_sim.transfer_spool import TransferSpool,SCHEMA,MAX_RECORDS,CONFIG_KEYS,_codec
    transfer_raw=stable(work/'transfer-config.json');transfer=parse(transfer_raw)
    runtime=parse(stable(work/'compute-runtime.json'))
    require(set(transfer)==CONFIG_KEYS and sha(transfer_raw)==runtime['transfer_config_sha256']
        and transfer['stage_id']==c['stage_id'] and transfer['expected_source_sha256']==c['source_sha256']
        and transfer['deadline_utc']==c['deadline_utc'],'poll transfer binding')
    root=path(transfer['spool_dir']);acks=path(transfer['ack_dir'])
    require(root==work/'spool' and acks==work/'acks','poll spool namespaces')
    lock=path(root/'lock');before=anchor(lock.lstat());fd=os.open(lock,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        require(anchor(os.fstat(fd))==before and stat.S_ISREG(os.fstat(fd).st_mode),'poll lock identity')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        codec=_codec();dependencies={n:sha(stable(Path(codec.__file__).parent/n))for n in ('cloud_archive.py','cloud_control.py')}
        binding=parse(stable(root/'binding.json'))
        require(binding=={'schema':SCHEMA,'config_sha256':sha(transfer_raw),'config':transfer,
            'codec_sha256':dependencies['cloud_archive.py'],'codec_dependencies_sha256':dependencies},'poll original spool binding')
        view=object.__new__(TransferSpool);view.config=transfer;view.source=c['source_sha256']
        view.codec_sha=dependencies['cloud_archive.py'];view.dependencies_sha=dependencies
        reservations=path(root/'reservations');groups=path(root/'groups')
        initial=sorted(x.name for x in reservations.iterdir());group_names=sorted(x.name for x in groups.iterdir())
        require(len(initial)<=MAX_RECORDS and len(group_names)<=8192,'poll history bound')
        previous=None;total=0;ids=[];history=[];sealed=[];unsealed=[]
        for number,n in enumerate(initial,1):
            deadline(c);require(n==f'{number:08d}.json','poll reservation gap')
            raw=stable(reservations/n);r=parse(raw)
            require(set(r)=={'schema','sequence','previous_sha256','stage_id','source_sha256','generation','reserved_bytes'}
                and r['schema']==SCHEMA and type(r['sequence'])is int and r['sequence']==number
                and r['previous_sha256']==previous and r['stage_id']==c['stage_id'] and r['source_sha256']==c['source_sha256']
                and isinstance(r['generation'],str) and re.fullmatch('[0-9a-f]{64}',r['generation'])
                and r['generation']not in ids and type(r['reserved_bytes'])is int and r['reserved_bytes']>0,'poll original reservation differs')
            total+=r['reserved_bytes'];require(total<=transfer['max_transfer_bytes'],'poll original transfer allowance')
            previous=sha(raw);history.append({'file':n,'sha256':previous});generation=r['generation'];ids.append(generation)
            group=groups/generation
            if generation not in group_names or not os.path.lexists(group/'manifest.json') or not os.path.lexists(group/'seal.json'):
                unsealed.append(generation);continue
            path(group);m,mr,seal=view._manifest(group);sr=stable(group/'seal.json')
            require(type(m['day'])is int and m['day']>=0 and m['kind']in ('checkpoint','final')
                and isinstance(m['sealed'],dict) and set(m['sealed'])==({'item','index'}if m['kind']=='checkpoint'else{'manifest'}),'poll native sealed payload shape')
            ident={k:m[k]for k in ('stage_id','source_sha256','config_sha256','case_id','attempt','kind','day')}
            from dams_sim.storage import canonical
            require(generation==sha(canonical(ident)),'poll generation identity')
            ack_sha=None;incoming=acks/(generation+'.json');retained=group/'ACK.json'
            if os.path.lexists(incoming) or os.path.lexists(retained):
                require(os.path.lexists(incoming) and os.path.lexists(retained),'poll ACK not reconciled')
                ar=stable(incoming);require(stable(retained)==ar,'poll retained ACK changed');a=parse(ar)
                require(set(a)=={'schema','stage_id','source_sha256','generation','manifest_sha256','group_sha256','gate','copies'}
                    and a['schema']==SCHEMA and all(a[k]==m[k]for k in ('stage_id','source_sha256','generation','group_sha256'))
                    and a['manifest_sha256']==seal['manifest_sha256']
                    and a['gate']==('checkpoint-full-state'if m['kind']=='checkpoint'else'final-full-raw'),'poll exact ACK binding')
                ack_sha=sha(ar)
            sealed.append({'generation':generation,'manifest_sha256':sha(mr),'manifest_bytes':len(mr),
                'seal_sha256':sha(sr),'seal_bytes':len(sr),'group_sha256':m['group_sha256'],
                'case_id':m['case_id'],'attempt':m['attempt'],'kind':m['kind'],'day':m['day'],
                'config_sha256':m['config_sha256'],'ack_present':ack_sha is not None,'ack_sha256':ack_sha})
        account=parse(stable(root/'accounting.json'))
        require(account=={'schema':SCHEMA,'sequence':len(initial),'previous_sha256':previous,'reserved_bytes':total},'poll accounting/prefix differs')
        require(set(group_names)<=set(ids),'poll generation has no original reservation')
        require(initial==sorted(x.name for x in reservations.iterdir()) and group_names==sorted(x.name for x in groups.iterdir())
            and anchor(lock.lstat())==before and anchor(os.fstat(fd))==before,'poll enumeration/lock changed')
        return {'sealed_generations':sealed,'unsealed_generations':unsealed,'reservation_generations':ids,
            'spool_high_water':{**account,'history_sha256':sha(blob(history))}}
    finally:os.close(fd)

def operation(action,c,raw,get_identity=None):
    context(c,get_identity)
    if action=='upload':return blob(upload(c,raw))
    root,m=package(c);sys.path[:0]=[str(root),str(root/'research_tools')]
    # The inline helper itself must be among the root-approved uploaded bytes.
    require(m['source_files_sha256'].get('research_tools/compute_only_remote.py')==c['remote_helper_sha256'],'remote executing helper pin')
    from research_tools import compute_only_worker as w
    work=path(c['work_root'])
    if action=='prepare':
        opts=parse(raw);argv=['/bin/bash',str(root/'cloud/prepare-compute-only.sh')]
        for key,value in opts.items():
            if key=='offline':
                if value:argv.append('--offline')
            else:argv+=['--'+key.replace('_','-'),str(value)]
        env=dict(os.environ)
        for k in ('DAMS_CLOUD_PRIVATE_CONFIG','DAMS_CLOUD_STATE_DIR','DAMS_TRANSFER_CONFIG','GOOGLE_APPLICATION_CREDENTIALS'):env.pop(k,None)
        remaining=w.utc(opts['deadline_utc']).timestamp()-time.time();require(remaining>4,'prepare remaining bound')
        r=subprocess.run(argv,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=remaining+2)
        require(r.returncode==0,'prepare-only failed');value=parse(stable(work/'prepared.json'))
        require(value['source_manifest_sha256']==c['source_manifest_sha256'] and value['source_commit']==c['source_commit'],'prepare receipt pin')
        return blob(value)
    if action=='doctor':return blob(w.measured_guest())
    if action=='activate':
        v=parse(raw);require(set(v)=={'runtime','transfer','heartbeat'},'activate payload shape')
        r=w.validate_runtime(v['runtime']);t=v['transfer'];lease=v['heartbeat']
        require(sha(blob(r))==c['runtime_sha256'] and sha(blob(t))==r['transfer_config_sha256'],'runtime/transfer pins')
        require(r['provider_identity_sha256']==c['provider_identity_sha256'] and r['source_manifest_sha256']==c['source_manifest_sha256'],'provider/source binding')
        require(r['deadline_utc']==c['deadline_utc'] and t['deadline_utc']==r['deadline_utc'],'fixed activation deadline')
        from dams_sim.storage import source_hash
        require(t['expected_source_sha256']==source_hash() and t['stage_id']==c['stage_id'],'transfer stage/source')
        require(t['spool_dir']==str(work/'spool') and t['ack_dir']==str(work/'acks'),'fixed spool namespaces')
        w.verify_identity(r,w.measured_guest())
        require(lease['runtime_sha256']==sha(blob(r)) and lease['sequence']==0,'initial lease binding')
        phase=None
        if r['runtime_limits'].get('phase_max_output_bytes',0):
            from research_tools.bounded_phase_storage import prepare
            phase=prepare(work,r)
            require(t['max_spool_bytes']<=r['runtime_limits']['phase_spool_bytes']
                and t['max_transfer_bytes']<=r['runtime_limits']['phase_max_output_bytes'],'bounded transfer exceeds phase storage')
        for n in (('acks',) if phase is not None else ('spool','acks')):
            require(not os.path.lexists(work/n),'preexisting spool requires reconciliation');os.mkdir(work/n,0o700)
        for n,value in (('compute-runtime.json',r),('transfer-config.json',t),('controller-heartbeat.json',lease)):write(work/n,blob(value))
        with w.directory(work) as d:w.lease(d,sha(blob(r)),r['controller_lease_timeout_seconds'],time.time())
        require(time.time()<w.utc(r['runtime_limits']['deadline_utc']).timestamp(),'science cutoff before activation')
        result=subprocess.run(['/bin/bash',str(root/'cloud/compute-only-services.sh')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=min(30,max(1,w.utc(r['runtime_limits']['deadline_utc']).timestamp()-time.time())))
        require(result.returncode==0,'service activation unknown');return blob({'runtime_sha256':sha(blob(r)),'services_activated':True,'phase_storage':phase,'science_complete':False})
    runtime=parse(stable(work/'compute-runtime.json'));require(sha(blob(runtime))==c['runtime_sha256'],'remote runtime drift')
    if action=='heartbeat':
        old=stable(work/'controller-heartbeat.json');v=parse(raw);previous=parse(old)
        require(v['runtime_sha256']==c['runtime_sha256'] and v['sequence']==previous['sequence']+1,'lease exact successor')
        require(w.utc(v['observed_utc'])>=w.utc(previous['observed_utc']),'lease time rollback')
        write(work/'controller-heartbeat.json',raw,old)
        with w.directory(work) as d:w.lease(d,c['runtime_sha256'],runtime['controller_lease_timeout_seconds'],time.time(),previous)
        return blob({'heartbeat_sha256':sha(raw),'sequence':v['sequence']})
    if action=='ack':
        a=parse(raw);require(a['stage_id']==c['stage_id'] and a['source_sha256']==c['source_sha256'] and re.fullmatch('[0-9a-f]{64}',a['generation']),'ACK target')
        from dams_sim.transfer_spool import TransferSpool
        spool=TransferSpool(work/'transfer-config.json',expected_source_sha256=c['source_sha256'])
        with spool._locked():
            spool._check();spool._history()
            require(a['generation'] in spool.records,'unknown generation ACK')
            destination=work/'acks'/(a['generation']+'.json')
            if os.path.lexists(destination):require(stable(destination)==raw,'existing ACK differs')
            else:write(destination,raw)
            spool._reconcile_acks()
        return blob({'generation':a['generation'],'ack_sha256':sha(raw),'ack_applied':True,'science_complete':False})
    if action=='poll':
        snapshot=spool_snapshot(c,work)
        return blob({'schema':'dams-compute-live-poll-v1','source_sha256':c['source_sha256'],
            'runtime_sha256':c['runtime_sha256'],**snapshot,
            'terminal_present':os.path.lexists(work/'compute-terminal.json'),'science_complete':False})
    terminal=parse(stable(work/'compute-terminal.json'))
    require(terminal['runtime_sha256']==c['runtime_sha256'] and terminal['owned_pipeline_group_verification']['owned_pipeline_group_absent'] is True,'terminal/owned group missing')
    require(terminal['inputs_unchanged'] is True,'terminal source guard failed')
    pid=terminal['owned_pipeline_group_verification']['owned_pipeline_group_id']
    try:os.killpg(pid,0)
    except ProcessLookupError:pass
    else:raise ValueError('owned pipeline group still exists')
    output=path(work/'output')
    if action=='terminal-roster':
        files=[];namespace=[];total=0
        for parent,dirs,names in os.walk(output,followlinks=False):
            dirs.sort();path(parent)
            for d in dirs:require(stat.S_ISDIR((Path(parent)/d).lstat().st_mode),'terminal symlink directory')
            for n in sorted(names):
                p=Path(parent)/n;st=p.lstat();require(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'terminal nonregular leaf')
                rel=p.relative_to(output).as_posix();namespace.append({'path':rel,'bytes':st.st_size})
                # Preserve all stable native bytes, including interrupted-case
                # SQLite sidecars; this is forensic transport, never state admission.
                before=anchor(st);h=hashlib.sha256();fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    while True:
                        deadline(c);block=os.read(fd,65536)
                        if not block:break
                        h.update(block)
                    require(anchor(os.fstat(fd))==before and anchor(p.lstat())==before,'terminal roster source drift')
                finally:os.close(fd)
                total+=st.st_size;files.append({'path':rel,'bytes':st.st_size,'sha256':h.hexdigest(),'identity':list(before)})
                require(len(files)<=c['max_terminal_files'] and total<=c['max_terminal_bytes'],'terminal admitted bound')
        return blob({'schema':'dams-compute-terminal-roster-v1','files':files,'namespace':namespace,'terminal':terminal,
            'source_sha256':c['source_sha256'],'runtime_sha256':c['runtime_sha256'],'case_payloads_require_original_collector':True,'stable_case_bytes_preserved_as_unvalidated_evidence':True,'science_complete':False})
    require(action=='terminal-read','unknown remote operation')
    req=parse(raw);require(set(req)=={'file','offset','length'},'terminal read shape');v=req['file'];p=output/name(v['path'])
    require(type(req['offset']) is int and type(req['length']) is int and 0<=req['offset']<=v['bytes'] and 0<=req['length']<=4*1024**2 and req['offset']+req['length']<=v['bytes'],'terminal range')
    require(list(anchor(path(p).lstat()))==v['identity'],'terminal file replaced')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        require(list(anchor(os.fstat(fd)))==v['identity'],'terminal FD replaced');os.lseek(fd,req['offset'],os.SEEK_SET);data=os.read(fd,req['length'])
        # bounded range itself may not end at EOF; do not overread the next range.
        data=data[:req['length']];require(len(data)==req['length'],'terminal short read')
        require(list(anchor(os.fstat(fd)))==v['identity'] and list(anchor(p.lstat()))==v['identity'],'terminal read changed');deadline(c);return data
    finally:os.close(fd)

def main(token):
    try:
        c=parse(base64.b64decode(token,validate=True));require(c['input_bytes']<=BOUND,'remote input bound')
        raw=sys.stdin.buffer.read(c['input_bytes']+1);require(len(raw)==c['input_bytes'] and sha(raw)==c['input_sha256'],'remote input length/SHA')
        out=operation(c.pop('action'),c,raw);require(len(out)<=c['output_bound'],'remote output bound');deadline(c)
        sys.stdout.buffer.write(out);sys.stdout.buffer.flush()
    except Exception:
        print('COMPUTE_ONLY_REMOTE_REFUSED',file=sys.stderr);raise SystemExit(2)
