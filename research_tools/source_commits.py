"""List the commits whose dams_sim sources give a recorded source_sha256 (dams_sim.storage.source_hash).

A study's plan.json and every receipt record source_sha256, the SHA-256 over the names and bytes of
dams_sim/*.py. Any listed commit reproduces the simulation of that study; tools outside dams_sim may differ.
Usage: python research_tools/source_commits.py SOURCE_SHA256_PREFIX [...]
"""
import hashlib
import subprocess
import sys


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, check=True).stdout


def main(prefixes):
    cache = {}
    for c in git('rev-list', '--all').decode().split():
        rows = [line.split('\t') for line in git('ls-tree', c, 'dams_sim/').decode().splitlines()]
        files = tuple(sorted((path.split('/')[-1], meta.split()[2]) for meta, path in rows
                             if path.endswith('.py') and meta.split()[1] == 'blob'))
        if not files:
            continue
        if files not in cache:
            cache[files] = hashlib.sha256(b''.join(n.encode()+b'\0'+git('cat-file', 'blob', s) for n, s in files)).hexdigest()
        h = cache[files]
        for p in prefixes:
            if h.startswith(p):
                print(h, c, git('log', '-1', '--format=%cs %s', c).decode().strip())


if __name__ == '__main__':
    main(sys.argv[1:])
