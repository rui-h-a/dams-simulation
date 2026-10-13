"""Stdlib read-only file-roster preflight. No Model import or execution."""
import hashlib,json,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'publication-manifest.json').read_bytes())
for name,pin in manifest['files'].items():
    relative=Path(name)
    if relative.is_absolute() or any(part in ('.','..') for part in relative.parts):raise ValueError('unsafe manifest name')
    path=ROOT/relative
    if not path.is_relative_to(ROOT):raise ValueError('manifest path leaves candidate')
    for parent in (path,*path.parents):
        if parent==ROOT.parent:break
        if parent.is_symlink():raise ValueError('source alias')
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('source regular single-link file required')
    if info.st_size!=pin['bytes']:raise ValueError('source size differs: '+name)
    raw=path.read_bytes()
    if len(raw)!=pin['bytes'] or hashlib.sha256(raw).hexdigest()!=pin['sha256']:raise ValueError('source bytes differ: '+name)
core=sorted((ROOT/'source/dams_sim').glob('*.py'))
if len(core)!=35:raise ValueError('core roster differs')
value=hashlib.sha256(b''.join(p.name.encode()+b'\0'+p.read_bytes() for p in core)).hexdigest()
if value!=manifest['source_sha256']:raise ValueError('model source hash differs')
print(json.dumps({'status':'SOURCE_ROSTER_VERIFIED_ONLY','source_sha256':value,'files':len(manifest['files']),'Model_calls':0,'formal_worlds_added':0}))
