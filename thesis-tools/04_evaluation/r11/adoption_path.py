"""Yearly paired path of net operating result (revenue minus operating cost per
mean active member) for the founding n=300 study, arms minus the hierarchy.

Usage: python 04_evaluation/r11/adoption_path.py STUDY_DIR
"""
import csv, glob, os, statistics, sys


def yearly(path, years=5):
    rows = list(csv.DictReader(open(path)))
    out = []
    for y in range(years):
        seg = rows[y*365+1:(y+1)*365+1]
        rev = sum(float(r['revenue_resource_units_daily']) for r in seg)
        cost = sum(float(r['operating_resource_units_daily']) for r in seg)
        out.append((rev-cost)/statistics.fmean(float(r['active_members']) for r in seg))
    return out


if __name__ == '__main__':
    root = sys.argv[1]
    diffs = {a: [[] for _ in range(5)] for a in ('control-process', 'linear-witness', 'dams-witness')}
    for w in sorted(glob.glob(os.path.join(root, '*-w1*'))):
        ref = os.path.join(w, 'control-hierarchy', 'timeseries.csv')
        if not os.path.exists(ref):
            continue
        b = yearly(ref)
        for a in diffs:
            f = os.path.join(w, a, 'timeseries.csv')
            if os.path.exists(f):
                for y, v in enumerate(yearly(f)):
                    diffs[a][y].append(v-b[y])
    for a, v in diffs.items():
        print(a, 'worlds', len(v[0]), ' '.join(f'{statistics.fmean(x):.1f}' for x in v))
