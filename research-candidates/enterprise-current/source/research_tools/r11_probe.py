"""Short resource probe of the enterprise model at a given population.

Builds the same configuration as a study world (context, growth, history and
horizon only set the configuration; the probe runs the first DAYS days of the
history under the hierarchy) and records, per simulated day, wall seconds,
peak resident memory and the size of the working directory. A probe measures
growth curves for planning; it is not a study world and adds no sample.

Usage: python research_tools/r11_probe.py --scale 100000 --days 30 --out DIR
           [--context multinational] [--growth organic] [--world 0]
Writes DIR/probe.json and keeps the working directory only with --keep.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dams_sim.config import Config  # noqa: E402
from dams_sim.model import Model  # noqa: E402
from research_tools.r11_design import base_config  # noqa: E402
from research_tools.r11_study import CHUNK, _rss_mb, source_hash  # noqa: E402


def dir_bytes(p):
    return sum(f.stat().st_size for f in Path(p).rglob('*') if f.is_file())


def probe(scale, days, out, context='multinational', growth='organic', world=0, keep=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    work = out/'work'
    if work.exists():
        shutil.rmtree(work)
    d, branch = base_config(scale=scale, context=context, growth=growth, world=world, seed=20261017, pre_years=2, post_years=5)
    t0 = time.monotonic()
    m = Model(Config.from_dict(d), storage_dir=work, journal_chunk_bytes=CHUNK)
    report = {'kind': 'r11-probe', 'source_sha256': source_hash(), 'scale': scale, 'context': context, 'growth': growth,
              'world': world, 'init_seconds': time.monotonic()-t0, 'init_peak_rss_mb': _rss_mb(), 'days': []}
    path = out/'probe.json'
    for day in range(1, days+1):
        t = time.monotonic()
        m.run(day)
        report['days'].append({'day': day, 'wall_seconds': time.monotonic()-t, 'peak_rss_mb': _rss_mb(),
                               'work_bytes': dir_bytes(work), 'active': len(m._long.active)})
        path.write_text(json.dumps(report, indent=1))  # partial results survive an interruption
    m._long.ledger.close()
    if not keep:
        shutil.rmtree(work)
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--scale', type=int, required=True)
    ap.add_argument('--days', type=int, required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--context', default='multinational')
    ap.add_argument('--growth', default='organic')
    ap.add_argument('--world', type=int, default=0)
    ap.add_argument('--keep', action='store_true')
    ns = ap.parse_args()
    r = probe(ns.scale, ns.days, ns.out, ns.context, ns.growth, ns.world, ns.keep)
    print(json.dumps({k: r[k] for k in ('scale', 'init_seconds', 'init_peak_rss_mb')} | {'last_day': r['days'][-1]}))
