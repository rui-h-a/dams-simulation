"""Finite root-only GCP lifecycle over the original stage and Collector holds.

Library and single-entry composition; no credentials enter the uploaded package.
Creation is never retried. SDK/SSH dispatch occurs only on root invocation.
"""
from __future__ import annotations
from contextlib import ExitStack
from datetime import datetime,timezone
import base64,hashlib,io,json,os,re,select,shlex,stat,subprocess,tarfile,threading,time
from pathlib import Path
from research_tools import cloud_control as cc
from research_tools.compute_only_control import Collector,blob,parse,read,require
from research_tools.compute_only_transport import ProcessStream
from research_tools.compute_only_entry import _directory
from dams_sim.storage import digest,source_hash
from dams_sim.longitudinal_pipeline import driver_hash

SCHEMA='dams-compute-only-live-lifecycle-v1'
FIELDS={'schema','phase','ledger_path','approval','collector_admission','assignment','minimum_head',
 'head_receipt_dir','ssh_key_file','known_hosts','network_tag','runtime','transfer','ack_generation',
 'terminal_copy_dirs','max_terminal_files','max_terminal_bytes','max_command_seconds','max_stdout_bytes','overhead_bytes'}
PHASES={'launch','activate','heartbeat','poll','ack','closeout'}
MAX_IO=32*1024**2

def finite(n,maxvalue):require(type(n)is int and 0<n<=maxvalue,'explicit lifecycle bound');return n

class Live:
    def __init__(self,entry,options,*,g=None,popen=subprocess.Popen):
        self.entry=entry;self.c=entry.c;self.s=entry.stage;self.o=options;self.raw=blob(options);self.stack=ExitStack();self.popen=popen
        self.g=g;self.vm=None;self.disk=None;self.remote_context=None;self.runtime=None;self.closed=False
        try:
            require(isinstance(options,dict) and set(options)==FIELDS and options['schema']==SCHEMA and options['phase']in PHASES,'live lifecycle exact options')
            require(self.c.get('paid_actions_authorized')is True,'root original paid authorization required')
            finite(options['max_command_seconds'],3600);finite(options['max_stdout_bytes'],MAX_IO)
            finite(options['overhead_bytes'],MAX_IO);finite(options['max_terminal_files'],8192);finite(options['max_terminal_bytes'],1024**4)
            require(re.fullmatch('[a-z][a-z0-9-]{0,62}',options['network_tag'] or '') is not None,'explicit no-SA network tag')
            require(len(options['terminal_copy_dirs'])==2 and len(set(options['terminal_copy_dirs']))==2,'two terminal destinations')
            self.terminal_copies=[self.stack.enter_context(_directory(p)) for p in options['terminal_copy_dirs']]
            require(all(a.path not in b.path.parents for a in self.terminal_copies for b in self.terminal_copies if a is not b),'terminal destinations overlap')
            self.ledger_dir=self.stack.enter_context(_directory(Path(options['ledger_path']).parent))
            ledger_raw,_=read(self.ledger_dir,Path(options['ledger_path']).name);d=parse(ledger_raw)
            require(d['authorization_id']==self.c['authorization_id'] and d['deadline_utc']==self.c['global_deadline_utc']
                and cc.money(d['cap_usd'])==cc.money(self.c['budget_cap_usd']) and cc.money(d['reserve_usd'])==cc.money(self.c['reserve_usd'])
                and cc.money(d['prior_spend_usd'])==cc.money(self.c['prior_spend_usd']), 'original ledger/cap/deadline cannot reset')
            require(self.s['id'] in d['entries'],'root must reserve the original stage BEFORE invoking live lifecycle')
            self.e=d['entries'][self.s['id']]
            self.approval=parse(entry.reference(options['approval']))
            require(set(self.approval)=={'schema','authorization_id','stage_id','intent_sha256','reserved_usd','termination_utc','source_manifest_sha256','paid_actions_authorized'}
                and self.approval['schema']=='dams-root-compute-live-approval-v1' and self.approval['paid_actions_authorized']is True
                and self.approval['authorization_id']==self.c['authorization_id'] and self.approval['stage_id']==self.s['id']
                and all(self.approval[k]==self.e[k] for k in ('intent_sha256','reserved_usd','termination_utc'))
                and self.approval['source_manifest_sha256']==entry.manifest_sha,'external root approval differs from original hold/source')
            require(cc.utc(self.e['termination_utc'])==entry.termination and cc.stage_cost(self.c,self.s)<=cc.money(self.e['reserved_usd']),'stage expiry/cost exceeds retained hold')
            expected=self.s.get('expected_guest',{})
            require(isinstance(expected,dict) and set(expected)=={'architecture','vcpus','memory_gib_min','memory_gib_max'}
                and expected['architecture']=='x86_64' and type(expected['vcpus']) is int and expected['vcpus']>0
                and 0<cc.money(expected['memory_gib_min'])<cc.money(expected['memory_gib_max']),'compute-only needs full frozen guest expectations before create')
            self.ledger=cc.Ledger(options['ledger_path'],self.c)
            # Existing Ledger revalidates its original frozen intent, without any new stage.
            require(self.ledger.reserve(self.s)==self.e,'existing stage intent changed')
            admission=parse(entry.reference(options['collector_admission']));assignment=entry.reference(options['assignment'])
            require(admission['stage_id']==self.s['id'] and admission['source_sha256']==source_hash()
                and admission['pipeline_driver_sha256']==driver_hash()
                and all(admission[k]==self.s[k] for k in ('assignment_sha256','spec_sha256','inventory_sha256')),'original Collector/assigned source pins')
            require(cc.utc(admission['deadline_utc'])<=entry.termination,'Collector cannot extend node expiry')
            minimum=None if options['minimum_head']is None else parse(entry.reference(options['minimum_head']))
            self.collector=self.stack.enter_context(Collector(options['collector_admission']['path'],admission_sha256=options['collector_admission']['sha256'],assignment_raw=assignment,minimum_head=minimum))
            self.retained=self.stack.enter_context(_directory(options['head_receipt_dir']))
            require(self.collector.head['sequence']==0 or minimum is not None,'existing Collector requires externally retained latest floor')
            self.key_dir=self.stack.enter_context(_directory(Path(options['ssh_key_file']).parent))
            self.key_name=Path(options['ssh_key_file']).name;self.key_anchors={}
            for n in (self.key_name,self.key_name+'.pub'):
                info=os.stat(n,dir_fd=self.key_dir.fd,follow_symlinks=False);require(stat.S_ISREG(info.st_mode)and info.st_nlink==1,'enrolled existing SSH keypair')
                self.key_anchors[n]=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            self.host_path=None;self.host_raw=None
            if options['known_hosts'] is not None:
                self.host_raw=entry.reference(options['known_hosts']);self.host_path=options['known_hosts']['path']
                require(self.host_raw and b'PRIVATE KEY'not in self.host_raw,'enrolled host-key receipt is empty/wrong')
            self.events=self.stack.enter_context(_directory(entry.state.path))
            if self.g is None:self.g=cc.Gcloud(self.c,entry.state.path/'provider-commands')
            if options['phase']!='launch':
                self._load_provider()
                if self.host_path is None:self.load_host_enrollment()
            self.check()
        except BaseException:self.stack.close();raise

    def check(self):
        self.entry.check();require(blob(self.o)==self.raw,'live options changed');self.collector.check();self.retained.check();self.key_dir.check()
        for n,a in self.key_anchors.items():
            i=os.stat(n,dir_fd=self.key_dir.fd,follow_symlinks=False);require((i.st_dev,i.st_ino,i.st_size,i.st_mtime_ns,i.st_ctime_ns)==a,'enrolled key replaced')
        require(self.o['phase']=='closeout' or time.time()<cc.utc(self.e['termination_utc']).timestamp(),'fixed node expiry reached')
        return min(self.o['max_command_seconds'],cc.utc(self.e['termination_utc']).timestamp()-time.time())
    def event(self,name,value):
        self.entry.check(allow_expired=name=='deleted');leaf=self.s['id']+'-'+name+'.json';raw=blob(value);self.events.write_new(leaf,raw)
        require(read(self.events,leaf)[0]==raw,'lifecycle event readback');return value
    def provider_call(self,args):
        remaining=self.check();require(remaining>2,'SDK cutoff lacks reaping margin');r=self.g.run(args,timeout=max(1,min(120,int(remaining-1))))
        self.check();require(r.get('exit')==0,'provider result unknown; hold retained');return json.loads(r['stdout']) if r.get('stdout','').strip()else None
    def _context(self):
        p=self.entry.options['prepare'];c={'project':self.c['project'],'zone':self.vm['zone'].rsplit('/',1)[-1],'instance_id':str(self.vm['id']),
            'source_root':'/opt/dams','work_root':'/var/lib/dams-compute','source_commit':self.c['source_commit'],
            'source_manifest_sha256':self.entry.manifest_sha,'deadline_utc':self.e['termination_utc'],
            'remote_helper_sha256':self.entry.options['component_sha256']['research_tools/compute_only_remote.py'],
            'max_source_bytes':p['max_source_bytes'],'max_source_files':p['max_source_files'],'stage_id':self.s['id'],
            'source_sha256':source_hash(),'max_terminal_files':self.o['max_terminal_files'],'max_terminal_bytes':self.o['max_terminal_bytes']}
        if self.runtime is not None:c.update(runtime_sha256=digest(blob(self.runtime)),provider_identity_sha256=digest(blob({'vm':self.vm,'disk':self.disk})))
        return c
    def _load_provider(self):
        p=parse(read(self.events,self.s['id']+'-provider.json')[0]);self.vm=p['vm'];self.disk=p['disk']
        require(p['termination_utc']==self.e['termination_utc'] and p['approval_sha256']==self.o['approval']['sha256'],'retained provider/approval changed')
        cc.validate_compute_only_instance(self.c,self.s,self.e,self.vm)
    def load_host_enrollment(self):
        receipt=parse(read(self.events,self.s['id']+'-host-enrollment.json')[0])
        require(receipt['instance_id']==str(self.vm['id']) and receipt['project']==self.c['project']
            and receipt['zone']==self.vm['zone'].rsplit('/',1)[-1],'retained host enrollment target differs')
        leaf=self.s['id']+'-known-hosts';raw=read(self.events,leaf)[0]
        require(digest(raw)==receipt['known_hosts_sha256'] and raw.startswith(('compute.'+str(self.vm['id'])+' ').encode()),'retained provider host keys changed')
        self.host_path=str(self.events.path/leaf);self.host_raw=raw
        self.entry.reference({'path':self.host_path,'sha256':receipt['known_hosts_sha256']})
    def enroll_host(self):
        metadata={r['key']:r['value'] for r in self.vm.get('metadata',{}).get('items',[])}
        require(metadata.get('enable-guest-attributes','').upper()=='TRUE','hostkey publishing not enabled on exact created VM')
        args=['gcloud','compute','instances','get-guest-attributes',self.e['instance_name'],'--query-path=hostkeys/',
            '--project='+self.c['project'],'--zone='+self.vm['zone'].rsplit('/',1)[-1],'--format=json']
        # Read-only readiness polling, no create/SSH retry. Every SDK attempt is
        # durably recorded by the existing wrapper; fixed prepare/D bound remains.
        until=min(cc.utc(self.entry.options['prepare']['deadline_utc']).timestamp(),time.time()+self.o['max_command_seconds'],cc.utc(self.e['termination_utc']).timestamp())
        data=None
        for _ in range(6):
            self.check();remaining=until-time.time();require(remaining>2,'host enrollment fixed readiness bound')
            r=self.g.run(args,timeout=max(1,min(15,int(remaining-1))))
            if r.get('exit')==0:
                data=json.loads(r['stdout']);break
            # Only exact provider not-yet-published guest-attribute readiness is
            # retryable. Auth/offline/unknown errors do not imply absent keys.
            error=r.get('stderr','')
            require('hostkeys/' in error and 'Guest Attribute' in error and 'not found' in error,'host-key provider response unknown')
            time.sleep(min(1,max(0,until-time.time()-2)))
        require(data is not None,'host keys were not published within original readiness bound')
        rows=data.get('queryValue',{}).get('items') if isinstance(data,dict) else data
        require(isinstance(rows,list) and 0<len(rows)<=8,'provider hostkey response shape')
        keys={}
        for row in rows:
            require(isinstance(row,dict) and row.get('namespace')=='hostkeys','foreign guest attribute')
            kind=row.get('key');value=row.get('value')
            require(kind in ('ssh-ed25519','ecdsa-sha2-nistp256','ssh-rsa') and kind not in keys
                and isinstance(value,str) and 0<len(value)<=16384 and not any(ch.isspace() for ch in value),'invalid provider host key')
            decoded=base64.b64decode(value,validate=True)
            require(base64.b64encode(decoded).decode()==value and len(decoded)>8 and int.from_bytes(decoded[:4],'big')==len(kind)
                and decoded[4:4+len(kind)]==kind.encode(),'provider host key wire type/base64 mismatch')
            keys[kind]=value
        alias='compute.'+str(self.vm['id']);raw=''.join(alias+' '+k+' '+v+'\n' for k,v in sorted(keys.items())).encode()
        leaf=self.s['id']+'-known-hosts';self.events.write_new(leaf,raw);require(read(self.events,leaf)[0]==raw,'enrolled known-hosts readback')
        self.event('host-enrollment',{'project':self.c['project'],'zone':self.vm['zone'].rsplit('/',1)[-1],
            'instance_id':str(self.vm['id']),'provider_response_sha256':digest(blob(data)),
            'known_hosts_sha256':digest(raw),'sdk_host_alias':'compute.<instance-id>','source':'exact provider guestAttributes hostkeys/'})
        self.load_host_enrollment()
    def _fresh_provider(self):
        vm=self.provider_call(['gcloud','compute','instances','describe',self.e['instance_name'],'--project='+self.c['project'],'--zone='+self.vm['zone'].rsplit('/',1)[-1],'--format=json'])
        require(str(vm['id'])==str(self.vm['id']),'provider instance replaced');cc.validate_compute_only_instance(self.c,self.s,self.e,vm);return vm
    def _retain(self,ticket,attempt):
        head=ticket['head'];raw=blob({'head':head,'attempt':attempt})
        self.retained.write_new(f'{head["sequence"]:08d}.json',raw);require(read(self.retained,f'{head["sequence"]:08d}.json')[0]==raw,'durable head retention')
        require(self.collector.head==head,'head advanced after retention')
        record=read(self.collector.records,f'{ticket["sequence"]:08d}.json')[0];require(digest(record)==ticket['reservation_sha256'],'reservation record changed')
        return head,record
    def exchange(self,action,input_raw=b'',bound=None):
        require(isinstance(input_raw,bytes) and len(input_raw)<=MAX_IO,'bounded SSH input');bound=finite(bound or self.o['max_stdout_bytes'],MAX_IO)
        require(self.host_path is not None,'exact provider host-key enrollment must precede SSH')
        c=self._context();c.update(action=action,input_sha256=digest(input_raw),input_bytes=len(input_raw),output_bound=bound)
        helper=(self.entry.root.path/'research_tools/compute_only_remote.py').read_bytes();require(digest(helper)==c['remote_helper_sha256'],'inline remote helper pin')
        token=base64.b64encode(blob(c)).decode();script=helper.decode()+'\nmain('+repr(token)+')\n'
        command='/usr/bin/sudo -n /usr/bin/python3 -I -B -c '+shlex.quote(script)
        argv=['gcloud','compute','ssh',self.e['instance_name'],'--project='+self.c['project'],'--zone='+c['zone'],
            '--configuration='+self.c['gcloud_configuration'],'--account='+self.c['gcloud_account'],'--tunnel-through-iap',
            '--strict-host-key-checking=yes','--quiet','--verbosity=error','--ssh-key-file='+self.o['ssh_key_file'],
            '--ssh-flag=-T','--ssh-flag=-oBatchMode=yes','--ssh-flag=-oConnectTimeout=15',
            '--ssh-flag=-oUserKnownHostsFile='+self.host_path,'--command='+command]
        maximum=len(input_raw)+bound+65536+len(command.encode())+self.o['overhead_bytes']
        require(maximum<MAX_IO,'one SSH operation exceeds original metadata reservation format; split before dispatch')
        ticket=self.collector.reserve_metadata_attempt(maximum)
        attempt={'action':action,'argv_sha256':digest(blob(argv)),'input_sha256':digest(input_raw),'input_bytes':len(input_raw),'deadline_utc':c['deadline_utc']}
        head,record=self._retain(ticket,attempt);remaining=self.check();require(remaining>3,'SSH cutoff lacks owned reap')
        def gate():
            self.check();require(self.collector.head==head and read(self.collector.records,f'{ticket["sequence"]:08d}.json')[0]==record,'retained head drift before SSH')
        gate();results=[];name=f'{self.s["id"]}-ssh-{head["sequence"]:08d}'
        self.event(name+'-BEGIN',attempt)
        def spawn(argv,**kw):gate();kw['stdin']=subprocess.PIPE;return self.popen(argv,**kw)
        stream=ProcessStream(argv,bound=bound+1,stderr_bound=65536,cutoff=time.monotonic()+remaining-2,check=gate,completed=results.append,popen=spawn)
        errors=[]
        def write_input():
            try:
                fd=stream.proc.stdin.fileno();os.set_blocking(fd,False);view=memoryview(input_raw)
                while view:
                    gate();require(time.monotonic()<stream.cutoff,'SSH input deadline')
                    try:n=os.write(fd,view[:65536])
                    except BlockingIOError:select.select([],[fd],[],.05);continue
                    require(n>0,'SSH short input write');view=view[n:]
                stream.proc.stdin.close()
            except BaseException as e:errors.append(e)
        writer=threading.Thread(target=write_input,daemon=True);writer.start();data=bytearray()
        try:
            while True:
                block=stream.read(65536)
                if not block:break
                data.extend(block);require(len(data)<=bound,'SSH output overread')
            writer.join(timeout=1);require(not writer.is_alive()and not errors,'SSH input/cleanup incomplete')
        finally:
            try:stream.close()
            finally:writer.join(timeout=2)
        require(results and results[-1]['status']=='accepted-stream'and not writer.is_alive(),'SSH parent/owned process result unknown')
        gate();raw=bytes(data);observed=input_raw+raw # application bounds; not network/billing measurement.
        with self.collector.locked():self.collector._accept_metadata(ticket,observed)
        self.event(name+'-END',{'result':results[-1],'output_sha256':digest(raw),'output_bytes':len(raw),'head':self.collector.head})
        return raw

    def preflight(self):
        require(self.g.inventory()==[],'dedicated project is not empty; no additional VM')
        cc.preflight_quotas(self.g,self.c,self.s)
        project=self.provider_call(['gcloud','projects','describe',self.c['project'],'--format=json'])
        require(project['projectId']==self.c['project'] and project.get('labels',{}).get('dams-task')==cc.digest(self.c['authorization_id'])[:24],'dedicated project label')
        image=self.provider_call(['gcloud','compute','images','describe',self.c['image'].rsplit('/',1)[-1],'--project='+self.c['image'].split('/projects/')[1].split('/')[0],'--format=json'])
        require(str(image['id'])==str(self.c['image_id']) and image['selfLink']==self.c['image'] and image['status']=='READY' and image['architecture']=='X86_64','fixed provider image')
        if cc.stage_capabilities(self.c,self.s)['purchase_mode']=='STANDARD':require({'GVNIC','UEFI_COMPATIBLE'}<={r['type']for r in image.get('guestOsFeatures',[])},'fixed image features')
        subnet=self.provider_call(['gcloud','compute','networks','subnets','describe',self.c['subnet'].rsplit('/',1)[-1],'--region='+self.c['region'],'--project='+self.c['project'],'--format=json'])
        require(subnet['region'].rsplit('/',1)[-1]==self.c['region'] and subnet.get('privateIpGoogleAccess'),'fixed subnet/private Google access')
        rules=self.provider_call(['gcloud','compute','firewall-rules','list','--project='+self.c['project'],'--format=json']);seen={}
        desired={('INGRESS','allow'):(['35.235.240.0/20'],[{'IPProtocol':'tcp','ports':['22']}]),('INGRESS','deny'):(['0.0.0.0/0'],[{'IPProtocol':'all'}]),('EGRESS','allow'):(['0.0.0.0/0'],[{'IPProtocol':'tcp','ports':['443']}]),('EGRESS','deny'):(['0.0.0.0/0'],[{'IPProtocol':'all'}])}
        for r in rules:
            if r.get('network')!=subnet['network']or r.get('disabled')or r.get('targetServiceAccounts'):continue
            if r.get('targetTags')and self.o['network_tag']not in r['targetTags']:continue
            action='allow'if r.get('allowed')else'deny';k=(r.get('direction'),action)
            require(k in desired and k not in seen and (r.get('sourceRanges'if k[0]=='INGRESS'else'destinationRanges',[]),r.get('allowed'if action=='allow'else'denied'))==desired[k],'no-SA applicable firewall differs')
            seen[k]=r['priority']
        require(set(seen)==set(desired)and all(seen[(d,'allow')]<seen[(d,'deny')]for d in ('INGRESS','EGRESS')),'no-SA IAP/HTTPS allow+deny missing')
        if self.c['network_mode']=='internal-offline':
            r=self.c.get('image_dependency_receipt',{});require(r.get('image_id')==str(self.c['image_id'])and r.get('offline_prepare_exit')==0 and r.get('uv_lock_sha256')==digest((self.entry.root.path/'uv.lock').read_bytes()),'internal image locked dependencies unavailable')
        self.event('environment',{'image':image,'subnet':subnet,'no_sa_firewall_verified':True,'science_complete':False})
    def launch(self):
        require(self.e['state']=='reserved'and self.e['attempts']==0,'create already attempted; root reconciliation required')
        self.preflight();self.entry.check(full=True)
        argv=cc.create_compute_only_command(self.c,self.s,self.e,self.c['zones'][0],self.entry.root.path/'cloud/compute-only-wait-source.sh',startup_sha256=self.entry.options['component_sha256']['cloud/compute-only-wait-source.sh'])
        argv+=['--tags='+self.o['network_tag']]
        for i,arg in enumerate(argv):
            if arg.startswith('--metadata='):argv[i]=arg+',enable-guest-attributes=TRUE'
        self.ledger.event(self.s['id'],state='creating',attempts=1,zone=self.c['zones'][0]);created=False
        try:
            self.provider_call(argv);created=True
            vm=self.provider_call(['gcloud','compute','instances','describe',self.e['instance_name'],'--project='+self.c['project'],'--zone='+self.c['zones'][0],'--format=json'])
            require(vm.get('name')==self.e['instance_name']and all(vm.get('labels',{}).get(k)==v for k,v in cc.labels(self.c,self.s).items()),'created VM ownership mismatch')
            self.vm=vm;cc.validate_compute_only_instance(self.c,self.s,self.e,vm);cc.verify_boot_disk(self.g,self.c,self.s,vm)
            self.disk=self.provider_call(['gcloud','compute','disks','describe',vm['disks'][0]['source'].rsplit('/',1)[-1],'--project='+self.c['project'],'--zone='+self.c['zones'][0],'--format=json'])
            self.event('provider',{'vm':vm,'disk':self.disk,'termination_utc':self.e['termination_utc'],'approval_sha256':self.o['approval']['sha256']})
            self.ledger.event(self.s['id'],state='running',instance_id=str(vm['id']),boot_disk_id=str(self.disk['id']))
            if self.host_path is None:self.enroll_host()
            archive=io.BytesIO();manifest_raw=self.entry.reference(self.entry.options['package_manifest']);m=parse(manifest_raw)
            with tarfile.open(fileobj=archive,mode='w:')as t:
                for n in sorted(m['source_files_sha256']):
                    raw=(self.entry.root.path/n).read_bytes();require(digest(raw)==m['source_files_sha256'][n],'source changed before upload')
                    item=tarfile.TarInfo(n);item.size=len(raw);item.mode=0o700 if os.stat(self.entry.root.path/n).st_mode&0o111 else 0o600;t.addfile(item,io.BytesIO(raw))
                item=tarfile.TarInfo('source-manifest.json');item.size=len(manifest_raw);item.mode=0o600;t.addfile(item,io.BytesIO(manifest_raw))
            self.entry.check(full=True);uploaded=parse(self.exchange('upload',archive.getvalue()));self.event('uploaded',uploaded)
            p=self.entry.options['prepare'];remaining=min(p['max_seconds'],cc.utc(p['deadline_utc']).timestamp()-time.time(),self.check())
            require(remaining>=7,'prepare cannot fit original deadline')
            prep={'bootstrap_sha256':self.entry.options['component_sha256']['cloud/prepare-compute-only.sh'],'source_root':'/opt/dams',
                'manifest_sha256':self.entry.manifest_sha,'source_commit':self.c['source_commit'],'spec':self.s['spec'],'scale':self.s['scale'],
                'deadline_utc':datetime.fromtimestamp(time.time()+remaining-4,timezone.utc).isoformat(),'max_seconds':max(1,int(remaining-4)),
                'max_source_bytes':p['max_source_bytes'],'max_source_files':p['max_source_files'],'max_log_bytes':p['max_log_bytes'],
                'receipt_file':'/var/lib/dams-compute/prepared.json','offline':p['offline']}
            prepared=parse(self.exchange('prepare',blob(prep)));self.event('prepared',prepared)
            doctor=parse(self.exchange('doctor'));caps=cc.stage_capabilities(self.c,self.s)
            from research_tools.cloud_worker import verify_guest
            verified=verify_guest({'purchase_mode':caps['purchase_mode'],'machine_type':self.s['machine_type'],'expected_guest':self.s.get('expected_guest',{})},doctor)
            require(verified['hardware_frozen_verified']and str(doctor['instance_id'])==str(vm['id'])and doctor['zone']==self.c['zones'][0],'actual doctor differs')
            return self.event('doctor',{'measurements':doctor,'verification':verified,'source_manifest_sha256':self.entry.manifest_sha,
                'provider_identity_sha256':digest(blob({'vm':self.vm,'disk':self.disk})),'root_runtime_seal_required_before_activation':True,'science_complete':False})
        except BaseException:
            self.ledger.event(self.s['id'],state='uncertain',failure='compute-only launch/prepare failed; hold retained')
            # Ambiguous creation gets exact inventory reconciliation; never another create.
            if self.vm is None:
                try:
                    rows=self.g.inventory();matching=[v for v in rows if v.get('name')==self.e['instance_name'] and all(v.get('labels',{}).get(k)==x for k,x in cc.labels(self.c,self.s).items())]
                    if len(matching)==1:self.vm=matching[0]
                except BaseException:pass
            if self.vm is not None:
                try:self.delete()
                except BaseException:pass # original provider-side absolute DELETE remains, unknown is retained.
            raise
    def load_runtime(self):
        runtime_raw=self.entry.reference(self.o['runtime']);transfer_raw=self.entry.reference(self.o['transfer'])
        self.runtime=parse(runtime_raw);transfer=parse(transfer_raw)
        require(runtime_raw==blob(self.runtime) and transfer_raw==blob(transfer),'sealed runtime/transfer must be exact canonical bytes uploaded')
        from research_tools.compute_only_worker import validate_runtime,verify_identity
        validate_runtime(self.runtime)
        doctor=parse(read(self.events,self.s['id']+'-doctor.json')[0]);r=self.runtime
        require(r['source_commit']==self.c['source_commit']and r['source_manifest_sha256']==self.entry.manifest_sha and r['spec']==self.s['spec']and r['scale']==self.s['scale'],'runtime scientific source/selection')
        require(r['deadline_utc']==self.e['termination_utc']and r['provider_identity_sha256']==digest(blob({'vm':self.vm,'disk':self.disk}))and r['transfer_config_sha256']==digest(blob(transfer)),'runtime provider/transfer/fixed expiry')
        require(r['machine_type']==self.s['machine_type']and r['purchase_mode']==cc.stage_capabilities(self.c,self.s)['purchase_mode']and r['expected_guest']==self.s.get('expected_guest',{}),'runtime exact catalog hardware')
        require(transfer['stage_id']==self.s['id']and transfer['expected_source_sha256']==source_hash()and transfer['deadline_utc']==r['deadline_utc'],'original transfer source/deadline')
        expected_limits={**self.s['runtime_limits'],**cc.stage_science_deadlines(self.s,self.e['termination_utc'])}
        require(r['runtime_limits']==expected_limits and r['pipeline_stop_grace_seconds']==self.s.get('pipeline_stop_grace_seconds',60),'runtime cannot change admitted resources/science stop or grace')
        require(cc.utc(r['watchdog_shutdown_utc']).timestamp()==cc.utc(self.e['termination_utc']).timestamp()-30,'independent watchdog must retain original D-minus-30')
        require(0<transfer['max_spool_bytes']<=int(cc.money(self.s['max_result_gib'])*2**30)
            and 0<transfer['max_transfer_bytes']<=int(cc.money(self.s['max_egress_gib'])*2**30),'guest spool/transfer exceeds original stage envelope')
        require(r['controller_lease_timeout_seconds']<=self.s['max_seconds'],'controller lease exceeds original node lifetime')
        verify_identity(r,doctor['measurements']);return transfer
    def activate(self):
        transfer=self.load_runtime();self._fresh_provider();self.entry.check(full=True)
        require(not any(n==self.s['id']+'-activation-BEGIN.json'for n in os.listdir(self.events.fd)),'activation already attempted; no automatic retry')
        heartbeat={'schema':'DAMS-compute-controller-lease-1','runtime_sha256':digest(blob(self.runtime)),'sequence':0,'observed_utc':cc.stamp()}
        self.event('activation-BEGIN',{'runtime_sha256':digest(blob(self.runtime)),'science_complete':False})
        try:result=parse(self.exchange('activate',blob({'runtime':self.runtime,'transfer':transfer,'heartbeat':heartbeat})))
        except BaseException:
            # Services may already be running when their SSH reply is lost.
            # Preserve the attempted activation and its original expiry/hold;
            # only the normal ACK/terminal preservation gate may actively delete.
            self.ledger.event(self.s['id'],state='uncertain',failure='service activation result unknown; no active delete requested, original expiry and hold retained')
            raise
        self.event('lease-00000000',heartbeat);return self.event('activated',result)
    def heartbeat(self):
        self.load_runtime();names=sorted(n for n in os.listdir(self.events.fd)if re.fullmatch(re.escape(self.s['id'])+r'-lease-[0-9]{8}\.json',n))
        require(names,'initial lease missing');previous=parse(read(self.events,names[-1])[0]);v={**previous,'sequence':previous['sequence']+1,'observed_utc':cc.stamp()}
        pending=self.s['id']+'-lease-pending-'+str(v['sequence'])+'.json'
        require(pending not in os.listdir(self.events.fd),'lease dispatch unknown; explicit root reconciliation required')
        self.event('lease-pending-'+str(v['sequence']),v);result=parse(self.exchange('heartbeat',blob(v)))
        self.event(f'lease-{v["sequence"]:08d}',v);return result
    def poll(self):
        self.load_runtime();result=parse(self.exchange('poll'))
        require(result['schema']=='dams-compute-live-poll-v1' and result['source_sha256']==source_hash()
            and result['runtime_sha256']==digest(blob(self.runtime)) and result['science_complete']is False,'poll source/runtime binding')
        return result
    def ack(self):
        self.load_runtime();g=self.o['ack_generation'];require(isinstance(g,str)and re.fullmatch('[0-9a-f]{64}',g),'exact ACK generation')
        raw=read(self.collector.state,g+'.ACK.json')[0];a=parse(raw)
        require(a['stage_id']==self.s['id']and a['source_sha256']==source_hash()and a['generation']==g,'accepted ACK differs')
        # Retained two receipts are read back before upload; no fabricated caller ACK.
        for d,copy in zip(self.collector.copies,a['copies']):
            with _directory(d.path/g)as group:
                receipt=read(group,'receipt.json')[0];require(digest(receipt)==copy['receipt_sha256'],'ACK restoration receipt changed')
                retained=parse(receipt);m=parse(read(group,'manifest.json')[0])
                self.collector._guard_files(group.path/'cases'/m['case_id']/m['attempt'],retained['files'])
        return parse(self.exchange('ack',raw))
    def pull_terminal(self):
        self.load_runtime();roster_raw=self.exchange('terminal-roster');roster=parse(roster_raw)
        require(roster['source_sha256']==source_hash()and roster['runtime_sha256']==digest(blob(self.runtime))and roster['case_payloads_require_original_collector']is True,'terminal inventory binding')
        require(roster['terminal']['owned_pipeline_group_verification']['owned_pipeline_group_absent']is True,'terminal group not absent')
        require(len(roster['files'])<=self.o['max_terminal_files']and sum(x['bytes']for x in roster['files'])<=self.o['max_terminal_bytes'],'terminal bounds')
        import shutil
        from collections import Counter
        needed=Counter()
        for d in self.terminal_copies:needed[os.fstat(d.fd).st_dev]+=sum(f['bytes']for f in roster['files'])+len(roster['files'])*8192+len(roster_raw)
        for d in self.terminal_copies:require(shutil.disk_usage(d.path).free>=needed[os.fstat(d.fd).st_dev]+self.collector.config['min_free_bytes'],'terminal copies disk capacity')
        original=roster_raw;observed=[];written={};parent_anchors={}
        for f in roster['files']:
            n=f['path'];require(isinstance(n,str)and not n.startswith('/')and all(x not in ('','.','..')for x in n.split('/')),'terminal path')
            from dams_sim._committed_pages import Directory
            dirs=[];fds=[];anchors=[];hashes=[hashlib.sha256(),hashlib.sha256()]
            try:
                for root in self.terminal_copies:
                    d=root
                    for part in n.split('/')[:-1]:
                        try:os.mkdir(part,0o700,dir_fd=d.fd);os.fsync(d.fd)
                        except FileExistsError:pass
                        d=Directory(d.path/part);dirs.append(d)
                    fd=d.open(n.split('/')[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);fds.append(fd);anchors.append((os.fstat(fd).st_dev,os.fstat(fd).st_ino));observed.append((d,n.split('/')[-1],f))
                require(anchors[0]!=anchors[1],'terminal copies share physical inode')
                offset=0
                while offset<f['bytes']:
                    count=min(4*1024**2,f['bytes']-offset);raw=self.exchange('terminal-read',blob({'file':f,'offset':offset,'length':count}),bound=count)
                    require(len(raw)==count,'terminal range short')
                    for i,fd in enumerate(fds):
                        view=memoryview(raw)
                        while view:w=os.write(fd,view);require(w>0,'terminal destination short write');view=view[w:]
                        hashes[i].update(raw)
                    offset+=count
                for i,(h,fd) in enumerate(zip(hashes,fds)):
                    os.fsync(fd);require(os.fstat(fd).st_size==f['bytes']and h.hexdigest()==f['sha256'],'terminal exact file SHA/size')
                    from dams_sim._committed_pages import identity
                    written[(str(self.terminal_copies[i].path),n)]=identity(os.fstat(fd))
            finally:
                for fd in fds:os.close(fd)
                for d in reversed(dirs):d.close()
        require(self.exchange('terminal-roster')==original,'terminal source namespace/files changed')
        # Actual closed-file readback for both distinct copies, not write-stream hashes only.
        for root in self.terminal_copies:
            for f in roster['files']:
                path=root.path/f['path'];before=path.lstat();require(stat.S_ISREG(before.st_mode)and before.st_nlink==1,'closed terminal leaf')
                from dams_sim._committed_pages import identity
                require(identity(before)==written[(str(root.path),f['path'])],'closed terminal output replaced')
                parent=Directory(path.parent);h=hashlib.sha256();fd=parent.open(path.name,os.O_RDONLY)
                try:
                    while True:
                        self.check();raw=os.read(fd,65536)
                        if not raw:break
                        h.update(raw)
                    require(h.hexdigest()==f['sha256']and (os.fstat(fd).st_dev,os.fstat(fd).st_ino,os.fstat(fd).st_size,os.fstat(fd).st_mtime_ns,os.fstat(fd).st_ctime_ns,os.fstat(fd).st_nlink)==(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_nlink) and (path.lstat().st_dev,path.lstat().st_ino,path.lstat().st_size,path.lstat().st_mtime_ns,path.lstat().st_ctime_ns,path.lstat().st_nlink)==(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_nlink),'closed terminal readback drift')
                finally:
                    os.close(fd);parent.check();parent.close()
            root.write_new('terminal-roster.json',original);require(read(root,'terminal-roster.json')[0]==original,'terminal receipt readback')
        self._terminal_files=written;self._terminal_roster=original
        self._terminal_parents={}
        for root in self.terminal_copies:
            for f in roster['files']:
                ancestor=(root.path/f['path']).parent
                while ancestor!=root.path:
                    i=ancestor.lstat();require(stat.S_ISDIR(i.st_mode),'closed terminal ancestor')
                    self._terminal_parents[ancestor]=(i.st_dev,i.st_ino);ancestor=ancestor.parent
        return self.event('terminal-preserved',{'roster_sha256':digest(original),'two_physical_copies_verified':True,'case_census_present':any(f['path'].endswith('/case_census.json')for f in roster['files']),
            'case_payloads_separately_require_original_collector':True,'full_study_gate':False,'science_complete':False})
    def _terminal_copy_guard(self):
        require(hasattr(self,'_terminal_files'),'closeout has no completed two-copy readback')
        from dams_sim._committed_pages import identity
        for root in self.terminal_copies:
            root.check();require(read(root,'terminal-roster.json')[0]==self._terminal_roster,'terminal receipt changed before delete')
            for (base,n),old in self._terminal_files.items():
                if base==str(root.path):require(identity((root.path/n).lstat())==old,'terminal bytes changed before delete')
        for p,old in self._terminal_parents.items():
            i=p.lstat();require(stat.S_ISDIR(i.st_mode)and(i.st_dev,i.st_ino)==old,'terminal ancestor changed before delete')

    def delete(self,*,preservation_gate=None):
        require(self.vm is not None,'unknown VM ownership; root provider reconciliation required')
        zone=self.vm['zone'].rsplit('/',1)[-1];name=self.e['instance_name'];self.entry.check(allow_expired=True)
        # SDK cleanup remains usable after science/node deadline; no resource/expiry extension.
        def command(args):
            r=self.g.run(args,timeout=120);require(r.get('exit')==0,'cleanup provider result unknown; hold retained');return json.loads(r['stdout'])if r.get('stdout','').strip()else None
        rows=command(['gcloud','compute','instances','list','--project='+self.c['project'],'--format=json'])
        matching=[v for v in rows if v.get('name')==name and v.get('zone','').rsplit('/',1)[-1]==zone]
        require(len(matching)<=1,'ambiguous instance identity')
        if matching:
            require(str(matching[0]['id'])==str(self.vm['id'])and all(matching[0].get('labels',{}).get(k)==v for k,v in cc.labels(self.c,self.s).items()),'cleanup instance replaced/foreign')
            if preservation_gate is not None:preservation_gate()
            command(['gcloud','compute','instances','delete',name,'--project='+self.c['project'],'--zone='+zone,'--quiet'])
        disks=command(['gcloud','compute','disks','list','--project='+self.c['project'],'--format=json'])
        boot=self.vm['disks'][0]['source'].rsplit('/',1)[-1]
        for disk in disks:
            if disk.get('name')!=boot or disk.get('zone','').rsplit('/',1)[-1]!=zone:continue
            require(self.disk is not None and str(disk['id'])==str(self.disk['id'])and not disk.get('users'),'boot disk identity/users unverified')
            if preservation_gate is not None:preservation_gate()
            command(['gcloud','compute','disks','delete',boot,'--project='+self.c['project'],'--zone='+zone,'--quiet'])
        rows=command(['gcloud','compute','instances','list','--project='+self.c['project'],'--format=json'])
        disks=command(['gcloud','compute','disks','list','--project='+self.c['project'],'--format=json'])
        ops=command(['gcloud','compute','operations','list','--project='+self.c['project'],'--format=json'])
        require(rows==[]and disks==[]and all(r.get('status')=='DONE'for r in ops),'compute/disk absence or pending operations unknown')
        self.ledger.event(self.s['id'],state='uncertain',compute_absence_verified=True,cleanup_verified_utc=cc.stamp(),science_verified=False)
        return self.event('deleted',{'vm_id':str(self.vm['id']),'boot_disk_id':None if self.disk is None else str(self.disk['id']),
            'compute_and_disk_absence_verified':True,'holds_released':False,'project_closed':False,'science_complete':False})
    def _preservation_ack_closure(self,snapshot):
        """Require EVERY original reserved generation and current two-copy ACK."""
        require(snapshot.get('schema')=='dams-compute-live-poll-v1' and snapshot.get('science_complete')is False
            and snapshot.get('source_sha256')==source_hash() and snapshot.get('runtime_sha256')==digest(blob(self.runtime)), 'closeout poll binding')
        ids=snapshot.get('reservation_generations');sealed=snapshot.get('sealed_generations');head=snapshot.get('spool_high_water')
        require(isinstance(ids,list) and len(ids)==len(set(ids)) and isinstance(sealed,list)
            and snapshot.get('unsealed_generations')==[] and isinstance(head,dict)
            and type(head.get('sequence'))is int and head['sequence']==len(ids)
            and {r['generation']for r in sealed}==set(ids) and len(sealed)==len(ids),'closeout reserved/sealed generation closure missing')
        known={n[:-9]for n in os.listdir(self.collector.state.fd)if n.endswith('.ACK.json')}
        require(known==set(ids),'closeout original Collector ACK coverage incomplete or guest history rolled back')
        observations=[];groups=[];accepted=[]
        try:
            for row in sealed:
                g=row['generation'];require(row.get('ack_present')is True and isinstance(row.get('ack_sha256'),str),'closeout guest ACK not applied')
                ar,ai=read(self.collector.state,g+'.ACK.json');a=parse(ar)
                require(digest(ar)==row['ack_sha256'] and a['generation']==g,'closeout guest/local ACK differs')
                observations.append((self.collector.state,g+'.ACK.json',ar,ai))
                manifest=None
                for d,copy in zip(self.collector.copies,a['copies']):
                    group=self.stack.enter_context(_directory(d.path/g));groups.append(group)
                    mr,mi=read(group,'manifest.json');sr,si=read(group,'seal.json');m,seal=self.collector._validate_group(mr,sr)
                    require(digest(mr)==row['manifest_sha256'] and digest(sr)==row['seal_sha256']
                        and len(mr)==row['manifest_bytes'] and len(sr)==row['seal_bytes']
                        and all(m[k]==row[k]for k in ('generation','case_id','attempt','kind','day','config_sha256','group_sha256')),
                        'closeout sealed/local generation differs')
                    expected_gate='checkpoint-full-state'if m['kind']=='checkpoint'else'final-full-raw'
                    require(a['schema']==m['schema'] and all(a[k]==m[k]for k in ('stage_id','source_sha256','generation','group_sha256'))
                        and a['manifest_sha256']==seal['manifest_sha256'] and a['gate']==expected_gate,'closeout original ACK gate/binding differs')
                    rr,ri=read(group,'receipt.json');r=parse(rr)
                    require(digest(rr)==copy['receipt_sha256'] and r['admission_sha256']==self.collector.admission_sha
                        and r['generation']==g and r['restoration_id']==copy['restoration_id'] and r['individual_case_only']is True and r['manifest_sha256']==seal['manifest_sha256']
                        and r['group_sha256']==copy['group_sha256']==seal['group_sha256'] and r['gate']==copy['gate']==a['gate']
                        and r['science_complete']is False and r['full_study_gate']is False,'closeout accepted restoration receipt differs')
                    self.collector._history(r['counter_high_water'])
                    attempt=group.path/'cases'/m['case_id']/m['attempt'];self.collector._guard_files(attempt,r['files'])
                    observations.extend([(group,'manifest.json',mr,mi),(group,'seal.json',sr,si),(group,'receipt.json',rr,ri)])
                    if manifest is not None:require(m==manifest,'closeout two restored manifests differ')
                    manifest=m
                    accepted.append((attempt,r['files']))
                require(len(a['copies'])==2 and len({x['restoration_id']for x in a['copies']})==2
                    and a['copies'][0]['receipt_sha256']!=a['copies'][1]['receipt_sha256'],'closeout requires two accepted copies')
                for n in accepted[-2][1]:require(accepted[-2][1][n]['identity'][:2]!=accepted[-1][1][n]['identity'][:2],'closeout copies share physical file')
            def guard():
                self.check()
                for d,n,raw,i in observations:require(read(d,n)==(raw,i),'closeout ACK/receipt drift before delete')
                for attempt,files in accepted:self.collector._guard_files(attempt,files)
            guard()
            return {r['generation']:parse(read(groups[i*2],'manifest.json')[0]) for i,r in enumerate(sealed)},guard
        except BaseException:raise

    def _preservation_census(self,manifests):
        """Literal declared stage partitions and latest raw/CP pointers, not full study."""
        from dams_sim.storage import canonical
        allids=set(self.collector.rows);observations=[];covered=set();classifications={}
        roster_raw=read(self.terminal_copies[0],'terminal-roster.json')[0];roster=parse(roster_raw)
        require(read(self.terminal_copies[1],'terminal-roster.json')[0]==roster_raw,'closeout terminal copy inventories differ')
        files={f['path']:f for f in roster['files']};require(len(files)==len(roster['files']),'closeout duplicate terminal path')
        def raw_file(n):
            require(n in files and files[n]['bytes']<=MAX_IO,'closeout census file missing/bound')
            result=None
            for root in self.terminal_copies:
                group=self.stack.enter_context(_directory((root.path/n).parent));raw,i=read(group,Path(n).name,MAX_IO)
                require(len(raw)==files[n]['bytes'] and digest(raw)==files[n]['sha256'],'closeout terminal census bytes changed')
                if result is not None:require(result==raw,'closeout census copies differ')
                result=raw;observations.append((group,Path(n).name,raw,i))
            return parse(result)
        inventories=sorted(n for n in files if n.endswith('/case_inventory.json') and not n.startswith('cases/'))
        require(inventories,'closeout exact declared case inventory/census unavailable')
        for n in inventories:
            stage=str(Path(n).parent);rows=raw_file(n)
            require(isinstance(rows,list) and rows and len({r['case_id']for r in rows})==len(rows)
                and all(r['case_id']in allids and r==self.collector.rows[r['case_id']]for r in rows),'closeout stage inventory differs from original assignment')
            ids={r['case_id']for r in rows};inv=digest(canonical(rows));meta=raw_file(stage+'/manifest.json')
            require(meta.get('schema_version')==3 and all(meta.get(k)==self.collector.config[k]for k in ('source_sha256','pipeline_driver_sha256','spec_sha256'))
                and meta.get('inventory_sha256')==inv and meta.get('expected_rows')==len(rows),'closeout stage source/roster differs')
            if meta.get('status')=='complete':
                require(meta.get('exit_code')==0 and meta.get('completed_rows')==len(rows),'closeout complete stage differs')
                group={key:'complete'for key in ids};records={key:{}for key in ids}
                for name,sha in meta.get('output_sha256',{}).items():require(stage+'/'+name in files and files[stage+'/'+name]['sha256']==sha,'closeout complete stage output missing')
            else:
                census_name=stage+'/case_census.json';census=raw_file(census_name)
                require(meta.get('partial_census_exact')is True and meta.get('case_census_sha256')==files[census_name]['sha256']
                    and census.get('schema')=='DAMS-stopped-case-census-1' and census.get('identity')=={k:meta[k]for k in ('schema_version','source_sha256','pipeline_driver_sha256','spec_sha256')}
                    and census.get('inventory_sha256')==inv and census.get('expected_cases')==len(rows)
                    and census.get('full_study_gate')is False and census.get('unexpected_case_ids')==[],'closeout stopped census binding')
                partitions=census['groups'];require(set(partitions)=={'complete','valid-latest-checkpoint','failed','unstarted'},'closeout census classifications')
                group={}
                for kind,keys in partitions.items():
                    require(isinstance(keys,list) and len(keys)==len(set(keys)) and not set(keys)&set(group),'closeout census duplicate partition')
                    group.update({key:kind for key in keys})
                require(set(group)==ids and census['remaining_case_ids']==sorted(ids-set(partitions['complete'])),'closeout census remaining/coverage differs')
                records={r['case_id']:r for r in census['records']}
                require(len(records)==len(census['records']) and set(records)==ids
                    and all(records[k].get('classification')==group[k]for k in ids),'closeout census records differ')
            for key,kind in group.items():
                require(key not in classifications or classifications[key]==kind,'closeout overlapping stage classification differs')
                classifications[key]=kind
                matches=[m for m in manifests.values()if m['case_id']==key]
                record=records[key]
                if kind=='complete':
                    require(any(m['kind']=='final' and ('attempt'not in record or record['attempt']=='cases/'+key+'/'+m['attempt'])for m in matches),'closeout complete case lacks accepted final raw')
                elif kind=='valid-latest-checkpoint':
                    require(any(m['kind']=='checkpoint' and m['day']==record.get('checkpoint_day')
                        and record.get('attempt')=='cases/'+key+'/'+m['attempt']
                        and m['sealed']['item']['file']==record.get('checkpoint_file')
                        and m['sealed']['item']['sha256']==record.get('checkpoint_sha256')
                        and any(p['name']=='checkpoint-index.json' and p['codec']['raw_sha256']==record.get('checkpoint_index_sha256')for p in m['files'])for m in matches),
                        'closeout latest checkpoint lacks exact accepted generation')
                elif kind=='unstarted':require(not matches and not any(x.startswith('cases/'+key+'/')for x in files),'closeout unstarted case has native outputs')
            covered|=ids
        require(covered==allids,'closeout declared census does not cover exact original assignment')
        actual={n.split('/')[1]for n in files if n.startswith('cases/') and len(n.split('/'))>=3}
        require(actual<=allids,'closeout undeclared native case bytes')
        def guard():
            self.check()
            for d,n,raw,i in observations:require(read(d,n)==(raw,i),'closeout census drift before delete')
        guard();return {'assigned_cases':len(allids),'classifications':classifications,'full_study_gate':False},guard

    def closeout(self):
        # Failed preservation/ACK closure retains the VM and original holds.
        # Server absolute DELETE remains unchanged; it is never renewed here.
        try:
            self.load_runtime();snapshot=self.poll();manifests,ack_guard=self._preservation_ack_closure(snapshot)
            preserved=self.pull_terminal();census,census_guard=self._preservation_census(manifests)
            require(self.poll()==snapshot,'closeout guest reservation/ACK roster changed')
            def guard():ack_guard();census_guard();self._terminal_copy_guard()
            guard();self.event('preservation-closure',{'terminal':preserved,'census':census,
                'spool_high_water':snapshot['spool_high_water'],'ack_generations':sorted(manifests),'science_complete':False})
            absence=self.delete(preservation_gate=guard)
            return {'terminal':preserved,'census':census,'cleanup':absence,'science_complete':False}
        except BaseException:
            self.ledger.event(self.s['id'],state='uncertain',failure='normal closeout refused; preservation unverified, no active delete requested, absolute expiry unchanged',science_verified=False)
            raise
    def run(self):return getattr(self,self.o['phase'])()
    def close(self):
        if not self.closed:self.closed=True;self.stack.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def execute(entry,options):
    try:
        with Live(entry,options)as live:return live.run()
    except RuntimeError as error:
        raise ValueError('compute lifecycle refused:'+type(error).__name__) from None
