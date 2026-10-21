"""Check that a performance change leaves the enterprise model's output unchanged.

Runs the same short worlds under two source trees and compares, for each case,
the complete storage directory byte for byte and the state digest without the
recorded source hash (the only header field that names the code). Cases cover a
mid-life history, a larger multinational population and the four attack modes.

Usage: python research_tools/r11_equivalence.py OLD_TREE NEW_TREE
       (OLD_TREE e.g. from `git archive <commit> | tar -x -C DIR`)
Exit 0 only if every case is identical.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CASES = [('traditional-300', 300, 'traditional', 'none', 120), ('multinational-2000', 2000, 'multinational', 'none', 40)]
CASES += [(f'attack-{a}-300', 300, 'startup', a, 40) for a in ('forge', 'censor', 'duplicate', 'freeride')]

RUN = r'''
import dataclasses, hashlib, json, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.storage import canonical
from research_tools.r11_design import base_config
from research_tools.r11_study import CHUNK
out = {}
for name, n, ctx, attack, days in json.loads(sys.argv[2]):
    d, _ = base_config(scale=n, context=ctx, growth='organic', world=3, seed=20261017, pre_years=0, post_years=1)
    d['attack'] = attack
    w = Path(tempfile.mkdtemp(dir=sys.argv[3]))/'work'
    m = Model(Config.from_dict(d), storage_dir=w, journal_chunk_bytes=CHUNK)
    for day in range(1, days+1):
        m.run(day)
    L = m._long
    h = hashlib.sha256(canonical({k: v for k, v in L.header().items() if k != 'execution_source_sha256'}))
    for a in L.agents:
        h.update(canonical(dataclasses.asdict(a)))
    h.update(L.ledger.semantic_digest().encode())
    L.ledger.close()
    files = {str(f.relative_to(w)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(w.rglob('*')) if f.is_file()}
    out[name] = {'state': h.hexdigest(), 'files': files}
    shutil.rmtree(w.parent)
print(json.dumps(out))
'''


def run(tree, tmp):
    r = subprocess.run([sys.executable, '-c', RUN, str(tree), json.dumps(CASES), str(tmp)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'{tree}: run failed\n'+'\n'.join(r.stderr.splitlines()[-5:]))
    return json.loads(r.stdout)


if __name__ == '__main__':
    old, new = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    tmp = Path(__file__).resolve().parent.parent/'runs'/'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    a, b = run(old, tmp), run(new, tmp)
    ok = True
    for name, *_ in CASES:
        same = a[name] == b[name]
        ok &= same
        print(f'{name}: {"identical" if same else "DIFFERENT"} ({len(a[name]["files"])} files)')
    sys.exit(0 if ok else 1)
