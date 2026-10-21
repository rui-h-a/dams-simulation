"""Verify a published evidence SHA256 and safely restore immutable raw files."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path,PurePosixPath
import shutil
import tarfile

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive',type=Path,required=True);ap.add_argument('--sha256',required=True)
    ap.add_argument('--destination',type=Path,default=Path('.'));args=ap.parse_args()
    h=hashlib.sha256()
    with args.archive.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    if h.hexdigest()!=args.sha256:raise ValueError('archive SHA256 mismatch; nothing extracted')
    dest=args.destination.resolve();dest.mkdir(parents=True,exist_ok=True);count=0
    with tarfile.open(args.archive,'r:gz') as archive:
        for member in archive:
            name=PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not member.isfile():raise ValueError('unsafe archive member')
            if not (name.parts[:2]==('runs','revision-v2') or name.parts[:2]==('evidence','benchmarks')):raise ValueError('outside evidence allowlist')
            target=dest/str(name)
            if not target.resolve().is_relative_to(dest):raise ValueError('destination escapes through a link')
            target.parent.mkdir(parents=True,exist_ok=True)
            source=archive.extractfile(member)
            if source is None:raise ValueError('missing archive data')
            if target.exists():
                old=hashlib.sha256();new=hashlib.sha256()
                with target.open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):old.update(block)
                for block in iter(lambda:source.read(1024*1024),b''):new.update(block)
                if old.digest()!=new.digest():raise ValueError('refuse to overwrite differing evidence: '+str(name))
            else:
                with target.open('xb') as f:shutil.copyfileobj(source,f)
            count+=1
    print(f'verified archive; {count} immutable raw files restored or matched')
if __name__=='__main__':main()
