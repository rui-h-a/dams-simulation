"""Read retained 10,000-member state and verify lifecycle counts without a model run."""
from argparse import ArgumentParser
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import csv
from datetime import date, timedelta
from hashlib import file_digest, sha256
from io import StringIO
from itertools import accumulate
import json
from pathlib import Path, PurePosixPath
import tarfile


WORLDS = tuple(range(1000, 1010))
ARMS = ('control-hierarchy', 'control-process', 'dams-witness', 'linear-witness')
START = date(2020, 1, 1)
BRANCH_DAY = 731
DAYS = 2557
SOURCE_COMMIT = '956725516ddac7f7c2336b0ccc23a69c5d7a27c5'
LIFECYCLE_SOURCE_SHA256 = '270e89a4789ddedc039515838e849f19d70f2cd0efad829b2795b5386a8839ff'
SELECTED_YEARS = (2022, 2023, 2024, 2025)
DATES = [START + timedelta(days=i) for i in range(DAYS)]
DAY_YEARS = [d.year for d in DATES]
DAY_MONTHS = [d.month for d in DATES]
YEARS = tuple(range(START.year, DATES[-1].year + 1))
MONTH_DAYS = {year: [(date(year, month, 12) - START).days for month in range(1, 13)] for year in YEARS}
YEAR_BOUNDS = {year: ((date(year, 1, 1) - START).days, (date(year + 1, 1, 1) - START).days) for year in YEARS}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def member_bytes(archive, member, expected=None):
    data = archive.extractfile(member).read()
    digest = sha256(data).hexdigest()
    require(len(data) == member.size, f'Short tar member {member.name}')
    if expected is not None:
        require(digest == expected, f'Tar member SHA-256 differs: {member.name}')
    return data, digest


def check_daily(raw, roster):
    rows = csv.DictReader(StringIO(raw.decode('utf-8'), newline=''))
    total = 0
    rows_seen = 0
    for day, row in enumerate(rows):
        count = roster[day]
        total += count
        require(int(row['day']) == day and row['calendar_date'] == DATES[day].isoformat(), 'Daily calendar differs')
        require(int(row['active_members']) == count, f'Daily roster differs on {DATES[day]}')
        require(float(row['active_member_calendar_days_daily']) == count, 'Daily exposure differs')
        require(float(row['active_member_calendar_days_cumulative']) == total, 'Cumulative daily exposure differs')
        rows_seen += 1
    require(rows_seen == DAYS, 'Incomplete daily series')


def check_people(state):
    people = state['people']
    changes = [0] * (DAYS + 1)
    annual_exits = {year: Counter() for year in YEARS}
    annual_voluntary_by_month = {year: [0] * 12 for year in YEARS}
    observed_person_days = 0
    full_exits = Counter()
    require(state['closed_day'] is None and state['suspended_day'] is None, 'Unexpected closed or suspended arm')
    for identifier, member in enumerate(people):
        entered = member['entered_day']
        exited = member['exited_day']
        reason = member['exit_reason']
        require(member['id'] == identifier and type(entered) is int and 0 <= entered <= DAYS, 'Invalid entry or identity')
        stop = DAYS if exited is None else exited
        require(type(stop) is int and entered <= stop <= DAYS, 'Invalid exit day')
        require((exited is None and reason is None) or (
            exited is not None and exited < DAYS and isinstance(reason, str) and bool(reason)
        ), 'Exit day and exit cause differ')
        exposure = stop - entered
        require(member['active_calendar_days'] == exposure, f'Person calendar exposure differs for record {identifier}')
        observed_person_days += exposure
        changes[entered] += 1
        changes[stop] -= 1
        if exited is not None:
            year = DAY_YEARS[exited]
            annual_exits[year][reason] += 1
            full_exits[reason] += 1
            if reason == 'voluntary_exit':
                annual_voluntary_by_month[year][DAY_MONTHS[exited] - 1] += 1
    roster = list(accumulate(changes))
    require(roster.pop() == 0 and min(roster) >= 0 and sum(roster) == observed_person_days, 'Roster sum differs from people exposure')
    annual = {}
    for year in YEARS:
        start, stop = YEAR_BOUNDS[year]
        exit_counts = dict(sorted(annual_exits[year].items()))
        annual[year] = {
            'calendar_days': stop - start,
            'person_calendar_days': sum(roster[start:stop]),
            'exits_by_cause': exit_counts,
            'exits_all_causes': sum(exit_counts.values()),
            'voluntary_exits': exit_counts.get('voluntary_exit', 0),
            'voluntary_exits_by_month': annual_voluntary_by_month[year],
            'active_members_on_monthly_12th': [roster[day] for day in MONTH_DAYS[year]],
        }
    return roster, full_exits, annual


def check_arm(archive, entries, frozen, world, arm):
    receipt_raw, receipt_digest = member_bytes(archive, entries['arm.json'])
    receipt = json.loads(receipt_raw)
    config = receipt['config']
    require(config['world'] == world and receipt['arm'] == arm and config['n'] == 10000, 'World or arm differs')
    require(receipt['days'] == DAYS and receipt['branch_day'] == BRANCH_DAY, 'Horizon or branch differs')
    require(config['longitudinal']['calendar_start'] == START.isoformat(), 'Calendar origin differs')
    require(receipt['parent_prehistory_sha256'] == frozen['prehistory_sha256'], 'Prehistory SHA-256 differs')
    require(receipt['source_sha256'] == frozen['scientific_source_sha256'], 'Execution source SHA-256 differs')
    require(all(receipt['validators'][k] == 'pass' for k in ('longitudinal', 'organization', 'summary')), 'Arm validation missing')
    if arm == 'dams-witness':
        require(config['longitudinal']['adoption_day'] == BRANCH_DAY, 'Witness adoption date differs')

    inputs = {}
    for filename, key in (('final_state.json', 'final_state_json'), ('summary.json', 'summary_json'), ('timeseries.csv', 'timeseries_csv')):
        expected = receipt['files'][key]
        require(isinstance(expected, str) and len(expected) == 64, f'Absent raw SHA-256 for {filename}')
        inputs[filename], _ = member_bytes(archive, entries[filename], expected)
    final = json.loads(inputs.pop('final_state.json'))
    require(final['source_sha256'] == receipt['source_sha256'], 'State execution source differs')
    require(final['state_semantic_sha256'] == receipt['state_semantic_sha256'], 'State semantic hash declaration differs')
    state = final['state']
    require(state['day'] == DAYS and state['config']['longitudinal']['calendar_start'] == START.isoformat(), 'Final state calendar differs')
    roster, full_exits, annual = check_people(state)
    check_daily(inputs['timeseries.csv'], roster)
    summary = json.loads(inputs['summary.json'])
    expected_totals = {
        'active_member_calendar_days': sum(roster),
        'entrants': len(state['people']) - config['n'],
        'retirements': full_exits['retirement'],
        'exits': sum(full_exits.values()) - full_exits['retirement'],
    }
    for key, expected in expected_totals.items():
        require(summary.get(key, 0) == state['metrics'].get(key, 0) == expected, f'{key} differs from summary or state metrics')
    require(receipt['ever_people'] == summary['ever_people'] == len(state['people']), 'Ever-person count differs')
    require(receipt['final_active_members'] == roster[-1] == len(state['people']) - sum(full_exits.values()), 'Final active count differs')
    return {
        'arm_receipt_sha256': receipt_digest,
        'final_state_raw_sha256': receipt['files']['final_state_json'],
        'summary_raw_sha256': receipt['files']['summary_json'],
        'daily_raw_sha256': receipt['files']['timeseries_csv'],
        'ever_people': len(state['people']),
        'final_active_members': roster[-1],
        'full_horizon_summary_counts': expected_totals,
        'full_horizon_exits_by_cause': dict(sorted(full_exits.items())),
        'annual': annual,
    }


def check_world(arguments):
    root, world, frozen = arguments
    path = root / f'dams-a12-n10000-w{world}.tar'
    with path.open('rb') as stream:
        digest = file_digest(stream, 'sha256').hexdigest()
    require(digest == frozen['archive_sha256'], f'Prior payback archive hash differs for {world}')
    targets = {'arm.json', 'final_state.json', 'summary.json', 'timeseries.csv'}
    entries = {arm: {} for arm in ARMS}
    with tarfile.open(path, 'r:') as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if member.isfile() and len(parts) >= 2 and parts[-2] in entries and parts[-1] in targets:
                require(parts[-1] not in entries[parts[-2]], f'Duplicate retained member {member.name}')
                entries[parts[-2]][parts[-1]] = member
        require(all(set(x) == targets for x in entries.values()), f'Incomplete retained roster for {world}')
        arms = {arm: check_arm(archive, entries[arm], frozen, world, arm) for arm in ARMS}
    for year in (2020, 2021):
        require(all(a['annual'][year] == arms[ARMS[0]]['annual'][year] for a in arms.values()), 'Shared prehistory aggregation differs')
    return world, {'archive_sha256': digest, 'prehistory_sha256': frozen['prehistory_sha256'], 'arms': arms}


def pool(records):
    exits = Counter()
    for row in records:
        exits.update(row['exits_by_cause'])
    quits = sum(row['voluntary_exits'] for row in records)
    exposure = sum(row['person_calendar_days'] for row in records)
    stock = sum(sum(row['active_members_on_monthly_12th']) for row in records)
    return {
        'voluntary_exits': quits,
        'exits_all_causes': sum(exits.values()),
        'exits_by_cause': dict(sorted(exits.items())),
        'person_calendar_days': exposure,
        'sum_monthly_12th_active_members': stock,
        'voluntary_exits_per_100_monthly_roster_observations': 100 * quits / stock,
    }


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('archive_root', type=Path)
    parser.add_argument('payback_provenance', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    proof = args.payback_provenance.read_bytes()
    prior = json.loads(proof)['worlds']
    require(set(prior) == set(map(str, WORLDS)), 'Frozen roster differs')
    with ProcessPoolExecutor(max_workers=args.workers) as pooler:
        worlds = dict(pooler.map(check_world, ((args.archive_root, w, prior[str(w)]) for w in WORLDS)))
    annual = {arm: {y: pool([worlds[w]['arms'][arm]['annual'][y] for w in WORLDS]) for y in YEARS} for arm in ARMS}
    selected = {arm: pool([worlds[w]['arms'][arm]['annual'][y] for w in WORLDS for y in SELECTED_YEARS]) for arm in ARMS}
    report = {
        'scope': 'Private independent extraction from final states and daily summaries for the ten 10,000-member worlds. No original event journals or simulation.',
        'source_commit': SOURCE_COMMIT,
        'lifecycle_source_sha256': LIFECYCLE_SOURCE_SHA256,
        'payback_provenance_sha256': sha256(proof).hexdigest(),
        'calendar': {'origin': START.isoformat(), 'branch_day': BRANCH_DAY, 'branch_date': DATES[BRANCH_DAY].isoformat(),
                     'final_day': DAYS - 1, 'final_date': DATES[-1].isoformat(), 'selected_post_adoption_years': SELECTED_YEARS},
        'convention': {'interval': '[entry day, exit day), using the horizon end for a person without exit',
                       'monthly_roster': 'Modeled active members on the 12th calendar day of each month, not employment over a payroll period',
                       'exit_count_reconciliation': 'Retirement is separate in source metrics. All other recorded causes reconcile to the combined exits count.',
                       'observed_voluntary_cause': 'voluntary_exit'},
        'checks': {'archives_sha256_matched': len(worlds), 'arm_final_state_summary_and_daily_hashes_matched': len(worlds) * len(ARMS),
                   'daily_roster_rows_matched': len(worlds) * len(ARMS) * DAYS,
                   'person_calendar_exposure_rows_matched': sum(a['ever_people'] for w in worlds.values() for a in w['arms'].values()),
                   'summary_exposure_and_exit_totals_matched': len(worlds) * len(ARMS), 'shared_prehistory_annual_aggregates_matched': True},
        'pooled_annual_by_arm': annual,
        'pooled_selected_years_by_arm': selected,
        'worlds': worlds,
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'checks': report['checks'], 'pooled_selected_years_by_arm': selected}, indent=2))


if __name__ == '__main__':
    main()
