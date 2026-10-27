"""Collect measured run time and memory of the enterprise model by population size.

Reads the retained receipts in the study bucket: the prehistory events of the
large pilots (one record per world: wall seconds and peak memory for the
two-year history before adoption) and the per-day series of the short resource
probes. Writes 04_evaluation/r11/scale.json; the 300-member points come from
resources.json, which is built from the validated arm receipts.

Every value is a measurement on the named machine family; nothing here is
extrapolated. Probes cover only their first days, which are not representative
of a whole history, and are kept apart from complete histories.

Usage: python 04_evaluation/r11/scale_points.py
"""
from __future__ import annotations

import json
import statistics
import subprocess
from pathlib import Path

import os
B = os.environ.get('DAMS_STUDY_BUCKET', 'gs://YOUR-STUDY-BUCKET')  # store holding the study receipts
HERE = Path(__file__).resolve().parent
# (members, prefix, machine family, code): 'list' is the source before commit f826b26,
# which tested attack-cohort membership against a daily list (a cost growing with
# the square of the population); 'set' is the source after it. Outputs of the two
# are byte-identical (research_tools/r11_equivalence.py); only the timing differs.
PILOTS = [(10000, 'pilot/n10000-r11-3-20261018', 'Cloud C4D', 'list'),
          (50000, 'pilot/n50000-r11-3-20261018', 'Cloud C4D', 'list'),
          (100000, 'pilot/n100000-r11-3-20261021b', 'Cloud E2', 'set')]
PROBES = [(100000, 'probe/r11-n100000-20261021', 'Cloud E2', 'list'),
          (500000, 'probe/r11-n500000-20261021b', 'Cloud E2', 'set')]
HISTORY_DAYS = 731


def gs(*args):
    r = subprocess.run(['gcloud', 'storage', *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


def latest(prefix, stem):
    names = sorted(l for l in gs('ls', f'{B}/{prefix}/{stem}*').split() if l)
    return names[-1] if names else None


def span(values):
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values), 'n': len(values)}


def pilot(n, prefix, machine, code):
    path = latest(prefix, 'events.jsonl')
    if not path:
        return None
    worlds = {}
    for line in gs('cat', path).splitlines():
        e = json.loads(line)
        if e.get('kind') == 'prehistory' and not e.get('error') and e.get('result'):
            worlds[e['world']] = e['result']  # repeated records of one world carry the same receipt
    if not worlds:
        return None
    return {'members': n, 'machine': machine, 'code': code, 'phase': 'two-year history before adoption', 'source': path,
            'seconds_per_day': span([r['wall_seconds']/HISTORY_DAYS for r in worlds.values()]),
            'peak_rss_mb': span([r['peak_rss_mb'] for r in worlds.values()])}


def probe(n, prefix, machine, code):
    path = latest(prefix, 'probe.json')
    if not path:
        return None
    d = json.loads(gs('cat', path))
    days = d['days']
    return {'members': n, 'machine': machine, 'code': code, 'phase': f'first {len(days)} days of a history (probe)', 'source': path,
            'init_seconds': d['init_seconds'], 'days_measured': len(days),
            # mean over calendar days, as for the histories (weekend days take a few seconds)
            'seconds_per_day': span([statistics.fmean(x['wall_seconds'] for x in days)]) if days else None,
            'workday_seconds_median': statistics.median(x['wall_seconds'] for x in days if x['wall_seconds'] > 60) if days else None,
            'peak_rss_mb': span([days[-1]['peak_rss_mb']]) if days else {'min': d['init_peak_rss_mb'], 'median': d['init_peak_rss_mb'],
                                                                        'max': d['init_peak_rss_mb'], 'n': 1},
            'work_bytes_last': days[-1]['work_bytes'] if days else None}


if __name__ == '__main__':
    out = {'histories': [p for row in PILOTS if (p := pilot(*row))],
           'probes': [p for row in PROBES if (p := probe(*row))]}
    (HERE/'scale.json').write_text(json.dumps(out, indent=1, sort_keys=True)+'\n')
    for k in out:
        for p in out[k]:
            print(k, p['members'], p['code'], p['phase'], round(p['seconds_per_day']['median'], 2) if p['seconds_per_day'] else None, 's/day',
                  round(p['peak_rss_mb']['median']), 'MiB')
