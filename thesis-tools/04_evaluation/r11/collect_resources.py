"""Collect measured resources of R11.3 runs from their validated receipts.

For each study directory: per-arm simulation wall seconds per simulated day,
validation wall seconds, peak resident memory, and the machine that ran it.
Only receipts of completed, validated arms (arm.json) and prehistories
(prehistory.json) are read; an arm resumed after an interruption records the
wall time of its final attempt only, so such arms are excluded from the
timing summaries and counted separately.

Usage: python 04_evaluation/r11/collect_resources.py STUDY_DIR LABEL MACHINE [...]
Writes 04_evaluation/r11/resources.json (merged by label).
"""
import json
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent/'resources.json'


def summary(values):
    v = sorted(values)
    return {'n': len(v), 'median': statistics.median(v), 'min': v[0], 'max': v[-1]} if v else None


def collect(root, label, machine):
    root = Path(root)
    arms, resumed = [], 0
    for f in sorted(root.glob('*/*/arm.json')):
        a = json.loads(f.read_text())
        out = f.parent
        if list(out.glob('failure-attempt-*.json')) or list(out.glob('interrupted-*')):
            resumed += 1
            continue
        days = a['days']-a['branch_day']
        arms.append({'s_per_day': a['simulation_wall_seconds']/days, 'validation_s': a['validation_wall_seconds'],
                     'rss_mb': a['peak_rss_mb'], 'members': a['final_active_members'], 'days': days})
    pre = [json.loads(f.read_text()) for f in sorted(root.glob('*/prehistory/prehistory.json'))]
    pre_t = [p['wall_seconds']/p['branch_day'] for p in pre if p['branch_day'] > 0]
    initial = json.loads(next(root.glob('*/prehistory/prehistory.json')).read_text())['config']['n']
    return {'label': label, 'machine': machine, 'initial_members': initial, 'arms_used': len(arms), 'arms_resumed_excluded': resumed,
            'arm_seconds_per_simulated_day': summary([a['s_per_day'] for a in arms]),
            'arm_validation_seconds': summary([a['validation_s'] for a in arms]),
            'arm_peak_rss_mb': summary([a['rss_mb'] for a in arms]),
            'arm_days': summary([a['days'] for a in arms]),
            'prehistory_seconds_per_simulated_day': summary(pre_t),
            'prehistory_peak_rss_mb': summary([p['peak_rss_mb'] for p in pre])}


if __name__ == '__main__':
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    args = sys.argv[1:]
    for i in range(0, len(args), 3):
        r = collect(*args[i:i+3])
        data[r['label']] = r
        print(json.dumps(r))
    OUT.write_text(json.dumps(data, indent=1, sort_keys=True)+'\n')
