from hashlib import sha256
import json
from math import isclose
from pathlib import Path
from statistics import mean, stdev

PHASES = ((731, 913), (913, 2557), (731, 2557))


def estimate(values):
    center = mean(values)
    width = 1.9977296543176926*stdev(values)/len(values)**0.5
    return [center, center-width, center+width]


def endpoints(totals):
    revenue, cost, days, stock = totals
    assert days > 0 and stock >= 0
    return (revenue-cost)*365.25/days, stock


def main():
    raw = Path(__file__).with_name('phase-totals.json').read_bytes()
    if sha256(raw).hexdigest() != '0f5a54faabeb170e53624c2099f6671f36ba945b3b695dc268429eda48e169ce':
        raise SystemExit('phase-totals.json failed SHA-256 verification')
    rows = json.loads(raw)
    assert [row['world'] for row in rows] == list(range(1000,1065))
    for row in rows:
        for name in ('staged','triggered'):
            first, second, full = row[name]['phases']
            assert all(isclose(a+b,c,rel_tol=1e-12) for a,b,c in zip(first,second,full,strict=True))
    results = []
    for i, (start, stop) in enumerate(PHASES):
        values = [[endpoints(row[name]['phases'][i]) for name in ('staged','triggered')] for row in rows]
        results.append(dict(start=start, stop_exclusive=stop,
                            net_operating_per_member_year=estimate([a[0]-b[0] for a,b in values]),
                            outstanding_record_days=estimate([a[1]-b[1] for a,b in values])))
    print(json.dumps(results,indent=2))


if __name__ == '__main__':
    main()
