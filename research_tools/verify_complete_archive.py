"""Independent stdlib-only bounded binary recovery verifier; never imports DAMS."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, io, json, os, resource, sys, time, traceback, zlib

OUT=Path(__file__).resolve().parent
BLOCK=64*1024
class Rejected(ValueError): pass
class Censored(RuntimeError): pass
def sha(data): return hashlib.sha256(data).hexdigest()
def native(path):
    s=path.stat()
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink,s.st_mode,s.st_blocks]
def reference(path):
    raw=path.read_bytes()
    return {'path':str(path),'bytes':len(raw),'sha256':sha(raw)}
class Budget:
    def __init__(self,root,seconds=60,rss=256*1024**2,free=96*1024**3):
        self.root=root;self.start=time.monotonic();self.deadline=self.start+seconds
        self.rss_limit=rss;self.free_floor=free;self.peak_rss=0;self.min_free=None;self.read_bytes=0;self.last_free=0
    def check(self,count=0,force=False):
        self.read_bytes+=count
        if time.monotonic()>=self.deadline:raise Censored('finite60s deadline reached; full binary verification not complete')
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform!='darwin':rss*=1024
        self.peak_rss=max(self.peak_rss,rss)
        if rss>self.rss_limit:raise Censored('256MiB process RSS limit exceeded')
        if force or self.read_bytes-self.last_free>=1024**2:
            s=os.statvfs(self.root);free=s.f_bavail*s.f_frsize
            self.min_free=free if self.min_free is None else min(self.min_free,free)
            self.last_free=self.read_bytes
            if free<self.free_floor:raise Censored('96GiB measured available-space floor not satisfied')

def decode_stream(stream,expected,sink,budget):
    """Bound decoded output and demand one complete zlib stream, no tail."""
    enc_sha=hashlib.sha256();raw_sha=hashlib.sha256();enc_bytes=0;raw_bytes=0
    dec=zlib.decompressobj()
    while True:
        data=stream.read(BLOCK)
        if not data:break
        budget.check(len(data));enc_bytes+=len(data);enc_sha.update(data)
        if enc_bytes>expected['encoded_bytes']:raise Rejected('encoded length exceeds pinned chunk length')
        if dec.eof:raise Rejected('data after zlib EOF')
        pending=data
        while True:
            try:raw=dec.decompress(pending,BLOCK)
            except zlib.error as error:raise Rejected('zlib decode rejected') from error
            pending=dec.unconsumed_tail
            budget.check()
            if raw:
                raw_bytes+=len(raw)
                if raw_bytes>expected['raw_bytes']:raise Rejected('decoded length exceeds pinned chunk length')
                raw_sha.update(raw);sink(raw)
            if dec.unused_data:raise Rejected('unused tail after zlib EOF')
            if dec.eof:
                if pending:raise Rejected('unconsumed tail at zlib EOF')
                break
            if pending:continue
            # Drain bounded buffered output without an unbounded flush().
            if len(raw)==BLOCK:
                pending=b'';continue
            break
    if not dec.eof:raise Rejected('truncated zlib stream without EOF')
    if dec.unused_data or dec.unconsumed_tail:raise Rejected('tail remained after zlib EOF')
    if enc_bytes!=expected['encoded_bytes']:raise Rejected('encoded chunk length differs')
    if enc_sha.hexdigest()!=expected['encoded_sha256']:raise Rejected('encoded chunk SHA-256 differs')
    if raw_bytes!=expected['raw_bytes']:raise Rejected('decoded chunk length differs')
    if raw_sha.hexdigest()!=expected['raw_sha256']:raise Rejected('decoded chunk SHA-256 differs')
    return {'encoded_bytes':enc_bytes,'encoded_sha256':enc_sha.hexdigest(),
        'raw_bytes':raw_bytes,'raw_sha256':raw_sha.hexdigest(),'zlib_eof':True,'unused_tail_bytes':0}

def controls(budget):
    data=(b'complete-case-independent-binary-check\x00\xff'*4000)
    encoded=zlib.compress(data,6)
    valid={'encoded_bytes':len(encoded),'encoded_sha256':sha(encoded),'raw_bytes':len(data),'raw_sha256':sha(data)}
    consumed=hashlib.sha256()
    good=decode_stream(io.BytesIO(encoded),valid,consumed.update,budget)
    assert consumed.hexdigest()==sha(data)
    results=[{'name':'valid stream spanning bounded decoded buffers','status':'PASS','result':good}]
    badcases=[('truncated encoded stream',encoded[:-1],{**valid,'encoded_bytes':len(encoded)-1,'encoded_sha256':sha(encoded[:-1])}),
        ('wrong encoded SHA',encoded,{**valid,'encoded_sha256':'0'*64}),
        ('wrong raw SHA',encoded,{**valid,'raw_sha256':'0'*64}),
        ('appended encoded tail with updated outer hash',encoded+b'tail',{**valid,'encoded_bytes':len(encoded)+4,'encoded_sha256':sha(encoded+b'tail')}),
        ('wrong declared decoded length',encoded,{**valid,'raw_bytes':len(data)-1})]
    for name,blob,expect in badcases:
        try:decode_stream(io.BytesIO(blob),expect,lambda b:None,budget)
        except Rejected as err:results.append({'name':name,'status':'REJECTED_AS_REQUIRED','reason':str(err)})
        else:raise AssertionError('negative control falsely accepted: '+name)
    return results

def main():
    import argparse, re
    p=argparse.ArgumentParser(description='Verify every byte in a pinned complete archive; never run DAMS or delete inputs.')
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--cas',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--minimum-free-gib',type=int,default=96)
    a=p.parse_args()
    if a.minimum_free_gib<0:p.error('minimum free GiB must be nonnegative')
    if a.manifest.is_symlink() or a.cas.is_symlink():p.error('manifest and CAS root must not be symlinks')
    if a.manifest.stat().st_size>8*1024**2:p.error('manifest exceeds bounded metadata limit')
    manifest_anchor=native(a.manifest)
    raw=a.manifest.read_bytes()
    if native(a.manifest)!=manifest_anchor:p.error('manifest changed while reading its external pin')
    if not re.fullmatch(r'[0-9a-f]{64}',a.manifest_sha256) or sha(raw)!=a.manifest_sha256:
        p.error('complete manifest SHA does not match the external pin')
    manifest=json.loads(raw)
    if manifest.get('all13_logical_files_preserved') is not True or len(manifest['files'])!=13:
        p.error('complete 13-file manifest required')
    if a.output.exists() or a.output.is_symlink():p.error('refuse to overwrite a result')
    os.nice(15)
    budget=Budget(a.cas,free=a.minimum_free_gib*1024**3)
    before=manifest_anchor
    result={'schema':'DAMS-complete-archive-public-binary-check-1','manifest_sha256':sha(raw),
        'scope':'Complete byte reconstruction only; no independent model semantics, off-device backup, retirement authority or new world.',
        'status':'RUNNING','files':[],'formal_worlds_added':0,'materialized_large_restoration_bytes':0}
    try:
        budget.check(force=True)
        result['controls']=controls(budget)
        referenced=0;decoded=0;objects={};object_pins={}
        for name,item in sorted(manifest['files'].items()):
            d=item['descriptor']
            if (d['schema'],d['codec'])!=(1,'deflate-chunks-v1'):raise Rejected('unsupported exact codec')
            if (d['raw_bytes'],d['raw_sha256'])!=(item['original_bytes'],item['original_sha256']):
                raise Rejected('descriptor differs from original full-file pins')
            full=hashlib.sha256();offset=0;encoded=0
            for chunk in d['chunks']:
                key=chunk['encoded_sha256']
                if not re.fullmatch(r'[0-9a-f]{64}',key):raise Rejected('invalid object key')
                if chunk['offset']!=offset:raise Rejected('chunk gap, overlap or order')
                obj=a.cas/key
                if obj.is_symlink() or not obj.is_file():raise Rejected('object must be a regular file')
                old=native(obj)
                if key in object_pins and object_pins[key]!=old:raise Rejected('shared object changed since its first reference')
                object_pins.setdefault(key,old)
                if old[2]!=chunk['encoded_bytes']:raise Rejected('object stat size differs')
                with obj.open('rb') as stream:r=decode_stream(stream,chunk,full.update,budget)
                if native(obj)!=old:raise Rejected('object changed during verification')
                if key in objects and objects[key]!=r['encoded_bytes']:raise Rejected('inconsistent shared-object size')
                objects[key]=r['encoded_bytes'];offset+=r['raw_bytes'];encoded+=r['encoded_bytes'];referenced+=1
            if offset!=d['raw_bytes'] or full.hexdigest()!=d['raw_sha256'] or encoded!=d['encoded_bytes']:
                raise Rejected('complete file SHA or size differs')
            decoded+=offset
            result['files'].append({'name':name,'raw_bytes':offset,'raw_sha256':full.hexdigest(),
                'encoded_reference_bytes':encoded,'chunk_references':len(d['chunks']),'status':'PASS'})
        if native(a.manifest)!=before:raise Rejected('pinned manifest changed during verification')
        for key,anchor in object_pins.items():
            obj=a.cas/key
            if obj.is_symlink() or not obj.is_file() or native(obj)!=anchor:
                raise Rejected('previously verified object changed before complete archive sealing')
        result.update(status='PASS',all13files_verified=True,decoded_raw_bytes=decoded,
            chunk_references=referenced,unique_objects=len(objects),unique_encoded_bytes=sum(objects.values()))
    except Exception as error:
        result.update(status='CENSORED' if isinstance(error,Censored) else 'FAILED',all13files_verified=False,
            error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc())
    result.update(wall_seconds=time.monotonic()-budget.start,peak_rss_bytes=budget.peak_rss,
        minimum_free_bytes=budget.min_free,sampled_limits_are_not_os_isolation=True)
    with a.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({'status':result['status'],'files':len(result['files']),
        'decoded_raw_bytes':result.get('decoded_raw_bytes'),'formal_worlds_added':0}))
    return 0 if result['status']=='PASS' else 2

if __name__=='__main__':sys.exit(main())
