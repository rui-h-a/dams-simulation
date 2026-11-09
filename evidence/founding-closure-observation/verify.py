#!/usr/bin/env python3
"""Verify pinned CBS cells and optionally check the retained native files."""
from collections import Counter
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ARMS = {'startup-existing': 'hierarchy', 'startup-founding': 'sublinear'}
CBS_SECTORS = {'307500 ', '354200 ', '391600 '}
CBS_COUNTS = {
    ('307500 ', 2018): (74, 49), ('307500 ', 2019): (104, 71),
    ('354200 ', 2018): (170, 124), ('354200 ', 2019): (168, 125),
    ('391600 ', 2018): (57, 44), ('391600 ', 2019): (79, 45),
}
CONFIG_FIELDS = (
    'closure_day', 'closure_after_unfunded_workdays', 'suspension_day',
    'initial_cash_per_member', 'demand_schedule', 'workforce_targets',
    'payroll_resource_units_per_member_workday',
    'maintenance_resource_units_per_member_workday',
    'revenue_resource_units_per_work_unit',
)


def checked_bytes(path, expected):
    payload = path.read_bytes()
    if sha256(payload).hexdigest() != expected:
        raise ValueError(f'Checksum mismatch: {path}')
    return payload


def verify_cbs():
    sources = json.loads((HERE / 'cbs-source.json').read_bytes())
    snapshots = {
        name: json.loads(checked_bytes(HERE / name, source['sha256']))['value']
        for name, source in sources.items()
    }
    table, = snapshots['cbs-table.json']
    assert table['Identifier'] == '85204ENG'
    size, = snapshots['cbs-size.json']
    assert (size['Key'], size['Title']) == ('A050858', '10 employees or more')
    statuses = {period['Key']: period['Status'] for period in snapshots['cbs-periods.json']}
    assert statuses['2023JJ00'] == 'Definitief' and statuses['2024JJ00'] == 'Voorlopig'
    titles = {row['Key']: row['Title'] for row in snapshots['cbs-sectors.json']}
    assert titles.keys() == CBS_SECTORS
    assert {x['Key'] for x in snapshots['cbs-topics.json']} == {
        'EmployerEnterpriseBirths_2', 'k_5YearSurvival_8'}
    rows = snapshots['cbs-cells.json']
    assert all(row['PersonsEmployed'] == size['Key'] for row in rows)
    cells = {(row['SIC2008'], int(row['Periods'][:4])): row for row in rows}
    assert len(cells) == len(rows) == 12
    results = []
    for (sector, birth_year), expected in sorted(CBS_COUNTS.items()):
        final_year = birth_year + 5
        start, final = cells[sector, birth_year], cells[sector, final_year]
        births, survivors = start['EmployerEnterpriseBirths_2'], final['k_5YearSurvival_8']
        assert (births, survivors) == expected
        results.append({
            'sector': titles[sector], 'birth_year': birth_year,
            'five_year_reporting_year': final_year,
            'reporting_status': 'final' if final_year == 2023 else 'provisional',
            'birth_row_id': start['ID'], 'survival_row_id': final['ID'],
            'employer_births': births, 'five_year_survivors': survivors,
            'five_year_survival_percentage': 100 * survivors / births,
        })
    return {'table': table['Identifier'], 'last_modified': table['Modified'],
            'cells_sha256': sources['cbs-cells.json']['sha256'], 'rows': results}


def verify_native(root, bindings):
    bindings_raw = bindings.read_bytes()
    frozen = json.loads(bindings_raw)
    result = {}
    for n, expected_start, targets in [
        ('30', 50000, [[0, 30], [366, 38], [1096, 45], [1827, 45]]),
        ('120', 51000, [[0, 120], [366, 150], [1096, 180], [1827, 180]]),
    ]:
        pins = frozen[n]['frozen']['observations']
        assert set(map(int, pins)) == set(range(expected_start, expected_start + 66))
        counts = {arm: Counter() for arm in ARMS}
        configs = {arm: set() for arm in ARMS}
        for world, pin in sorted(pins.items()):
            study = root / f'n{n}'
            directory = study / 'confirmation' / f'world-{world}'
            pair = json.loads(checked_bytes(directory / 'paired-result.json', pin['paired_result_sha256']))
            assert (pair['world'], pair['initial_n'], pair['stage'], pair['days'], pair['status']) == (
                int(world), int(n), 'confirmation', 1917, 'full-paired-raw-validated')
            assert pair['producer_workers_absent'] is True
            assert pair['identity']['source_sha256'] == frozen[n]['frozen']['source_sha256']
            observations = checked_bytes(directory / 'observations.csv', pin['observations_sha256'])
            common = [row for row in csv.DictReader(io.StringIO(observations.decode()))
                      if row['window_id'] == 'common-end']
            cases = pair['cases']
            assert len(common) == len(cases) == 2
            by_arm = {row['arm_id']: row for row in common}
            assert by_arm.keys() == {case['arm'] for case in cases} == ARMS.keys()
            for case in cases:
                arm = case['arm']
                row = by_arm[arm]
                assert row['case_id'] == case['case_id'] and int(row['world']) == int(world)
                assert (row['window_start_day'], row['window_end_day_exclusive'],
                        row['observed_end_day_exclusive'], row['calendar_start'],
                        row['calendar_end_exclusive'], row['window_status']) == (
                    '0', '1827', '1827', '2020-01-01', '2025-01-01', 'complete')
                assert (row['closed'], row['closure_day'], row['suspended']) == ('False', '', 'False')
                assert float(row['operating_unfunded_resource_units']) == 0
                assert case['summary']['closure_day'] is None and case['summary']['suspension_day'] is None
                manifest_path = study / case['attempt'] / 'manifest.json'
                manifest = json.loads(checked_bytes(manifest_path, case['manifest_sha256']))
                assert manifest['source_sha256'] == pair['identity']['source_sha256']
                assert manifest['scientific_case_id'] == case['case_id']
                config = manifest['config']
                assert config['world'] == int(world) and config['n'] == int(n)
                assert config['regime'] == ARMS[arm] and config['backend'] == 'central'
                longitudinal = config['longitudinal']
                assert longitudinal['workforce_targets'] == targets
                assert longitudinal['closure_day'] is None and longitudinal['suspension_day'] is None
                assert longitudinal['closure_after_unfunded_workdays'] == 20
                scope = {key: longitudinal[key] for key in CONFIG_FIELDS}
                scope.update({key: config[key] for key in ('backend', 'regime', 'fault_start_day', 'fault_stop_day')})
                configs[arm].add(json.dumps(scope, sort_keys=True))
                counts[arm].update(['complete_five_year_windows', 'zero_unfunded_operating_units',
                                    'no_closure_in_full_1917_days', 'no_suspension_in_full_1917_days'])
        assert all(len(scopes) == 1 for scopes in configs.values())
        result[n] = {'world_pairs': len(pins), 'source_sha256': frozen[n]['frozen']['source_sha256'],
                     'arm_counts': counts,
                     'constant_config_by_arm': {arm: json.loads(next(iter(scopes)))
                                                for arm, scopes in configs.items()}}
    return {'receipt_bindings_sha256': sha256(bindings_raw).hexdigest(),
            'complete_five_year_arm_windows': 264, 'by_initial_size': result}


if __name__ == '__main__':
    if len(sys.argv) == 1:
        print(json.dumps(verify_cbs(), indent=2, sort_keys=True))
    elif len(sys.argv) == 3:
        print(json.dumps({'native': verify_native(Path(sys.argv[1]), Path(sys.argv[2])),
                          'cbs': verify_cbs()}, indent=2, sort_keys=True))
    else:
        raise SystemExit('usage: python3 verify.py [NATIVE_ROOT SOURCE_RECEIPT_BINDINGS_JSON]')
