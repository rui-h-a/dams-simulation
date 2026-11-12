from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, stdev

FIELDS = ('confirmed', 'p95_days', 'pending_review', 'pending_settlement', 'pending_appeal')


def interval(values):
    center = mean(values)
    width = 1.9977296543176926 * stdev(values) / len(values)**0.5
    return [center, center-width, center+width]


def main():
    raw = Path(__file__).with_name('terminal-counts.json').read_bytes()
    if sha256(raw).hexdigest() != '3dfc7e33ae085eedab3a16e740924f8bcca1cdde3e77275a5bce8f7284b79afe':
        raise SystemExit('terminal-counts.json failed SHA-256 verification')
    rows = json.loads(raw)
    assert [row['world'] for row in rows] == list(range(1000, 1065))
    for row in rows:
        for arm in (row['staged'], row['triggered']):
            assert arm['confirmed'] > 0
            assert all(arm[k] >= 0 for k in (*FIELDS, 'delay_sum'))

    def contrast(extract):
        return interval([extract(row['staged'])-extract(row['triggered']) for row in rows])

    results = {key: contrast(lambda arm:arm[key]) for key in FIELDS}
    results['mean_delay_days'] = contrast(lambda arm:arm['delay_sum']/arm['confirmed'])
    results['pending_excluding_appeals'] = contrast(lambda arm:arm['pending_review']+arm['pending_settlement'])
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
