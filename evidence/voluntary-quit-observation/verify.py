"""Calculate comparable descriptive quit flows from retained monthly totals."""
from collections import defaultdict
from csv import DictReader
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
ARMS = ('control-hierarchy', 'control-process', 'dams-witness', 'linear-witness')
SERIES = {
    'JTS100000000000000QUR': 'all_private',
    'JTS100000000000004QUR': 'establishments_250_to_999',
    'JTS100000000000006QUR': 'establishments_5000_or_more',
}
PERIODS = {
    '2021–2023': (2021, 2023), '2024': (2024, 2024),
    '2022–2023': (2022, 2023), '2024–2025': (2024, 2025),
}


def rows(name):
    with (ROOT / name).open(newline='') as stream:
        yield from DictReader(stream)


def mean(values):
    return sum(values) / len(values)


def decimal_string(value):
    return str(value.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP))


def extract_bls():
    source = {}
    for row in rows('bls-monthly.csv'):
        key = row['series'], int(row['year']), int(row['month'])
        assert key not in source
        source[key] = Decimal(row['quits_per_100_employed'])
    assert set(source) == {(s, y, m) for s in SERIES for y in range(2021, 2026) for m in range(1, 13)}
    return {
        label: {
            name: decimal_string(12 * mean([source[s, y, m] for y in range(first, last + 1)
                                            for m in range(1, 13)]))
            for name, (first, last) in PERIODS.items()
        }
        for s, label in SERIES.items()
    }


def main_rows():
    report = json.loads((ROOT / 'main-quits-provenance.json').read_text())
    assert report['checks']['arm_final_state_summary_and_daily_hashes_matched'] == 40
    assert report['calendar']['branch_date'] == '2022-01-01'
    for world, records in report['worlds'].items():
        for arm, arm_data in records['arms'].items():
            for year, annual in arm_data['annual'].items():
                counts = annual['voluntary_exits_by_month']
                rosters = annual['active_members_on_monthly_12th']
                assert len(counts) == len(rosters) == 12 and sum(counts) == annual['voluntary_exits']
                for month, (count, roster) in enumerate(zip(counts, rosters, strict=True), 1):
                    yield {'initial_members': 10000, 'world': world, 'arm': arm, 'year': year,
                           'month': month, 'voluntary_quits': count, 'active_on_twelfth': roster}


def extract_model(data, size, world_count, year_range, periods):
    seen = set()
    grouped = defaultdict(list)
    for row in data:
        assert int(row['initial_members']) == size
        year, month, world = (int(row[k]) for k in ('year', 'month', 'world'))
        arm = row['arm']
        quit_count, roster = (int(row[k]) for k in ('voluntary_quits', 'active_on_twelfth'))
        key = world, arm, year, month
        assert key not in seen and 0 <= quit_count and 0 < roster
        if 'all_exits' in row:
            assert quit_count <= int(row['all_exits']) and roster <= int(row['person_calendar_days'])
        seen.add(key)
        for name in periods:
            first, last = PERIODS[name]
            if first <= year <= last:
                grouped[arm, name].append((quit_count, roster))
    assert seen == {(w, a, y, m) for w in range(1000, 1000 + world_count)
                    for a in ARMS for y in year_range for m in range(1, 13)}
    results = {}
    for arm in ARMS:
        results[arm] = {}
        for name in periods:
            cases = grouped[arm, name]
            first, last = PERIODS[name]
            months = 12 * (last - first + 1)
            assert len(cases) == world_count * months
            flows = [Decimal(1200) * q / h for q, h in cases]
            results[arm][name] = {
                'annualized_quits_per_100': decimal_string(mean(flows)),
                'voluntary_quits': sum(c[0] for c in cases),
                'world_months': len(cases),
                'min_active_on_twelfth': min(c[1] for c in cases),
                'max_active_on_twelfth': max(c[1] for c in cases),
            }
    return results


def main():
    with localcontext() as ctx:
        ctx.prec = 35
        result = {
            'statistic': '12 times equally weighted monthly quits per 100 roster or payroll positions',
            'bls': extract_bls(),
            '300_founders': extract_model(rows('founding-quits.csv'), 300, 57, range(2020, 2025),
                                         ('2021–2023', '2024')),
        }
        result['10000_adoption'] = extract_model(main_rows(), 10000, 10, range(2020, 2027),
                                                   ('2022–2023', '2024–2025'))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
