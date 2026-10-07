"""Package only new-repository synthetic evidence; never follow external symlinks."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
ROOT=Path(__file__).resolve().parents[1]

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();out=args.output.resolve();out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():raise ValueError('refuse to overwrite a retained evidence archive')
    selected=[];excluded=[]
    for relative in ('runs/revision-v2','evidence/benchmarks'):
        for p in sorted((ROOT/relative).rglob('*')):
            rel=p.relative_to(ROOT)
            # layout links point to private thesis sources, so neither their
            # targets nor any layout rendering work files enter this archive.
            if 'layout' in rel.parts or '__pycache__' in rel.parts or p.is_symlink():
                if p.is_file() or p.is_symlink():excluded.append(str(rel))
                continue
            if p.is_file():selected.append(p)
    if not selected:raise ValueError('no executed evidence available')
    entries=[]
    with tarfile.open(out,'w:gz',compresslevel=6) as archive:
        for p in selected:
            data=p.read_bytes();sha=hashlib.sha256(data).hexdigest()
            rel=str(p.relative_to(ROOT));archive.add(p,arcname=rel,recursive=False)
            # Detect a changing raw input, not just a successful archive write.
            if hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise RuntimeError('input changed during packing: '+rel)
            entries.append({'path':rel,'bytes':len(data),'sha256':sha})
    # Chunk hashing avoids another multi-GB allocation.
    h=hashlib.sha256()
    with out.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    index={'archive':out.name,'archive_sha256':h.hexdigest(),'archive_bytes':out.stat().st_size,
      'files':entries,'excluded_private_layout_or_symlinks':excluded,
      'scope':'synthetic study and local CPU benchmark evidence; original metadata and retained failures unchanged'}
    out.with_suffix(out.suffix+'.json').write_text(json.dumps(index,indent=2)+'\n')
    print(json.dumps({k:v for k,v in index.items() if k not in ('files','excluded_private_layout_or_symlinks')},indent=2))
if __name__=='__main__':main()
