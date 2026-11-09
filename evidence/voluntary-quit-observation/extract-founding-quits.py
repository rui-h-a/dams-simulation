"""Extract aggregate quit counts and payroll proxies from retained founding arms."""
from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import csv
import io
import json
import sys

study = Path(sys.argv[1])
plan_bytes = (study / 'plan.json').read_bytes()
plan = json.loads(plan_bytes)
assert plan['worlds'] == list(range(1000, 1057)) and len(plan['arms']) == 4
assert plan['scale'] == 300 and plan['pre_years'] == 0 and plan['post_years'] == 5
start = date(2020, 1, 1)
end = date(2025, 1, 1)
days = (end - start).days
month_days = [(date(y, m, 12) - start).days for y in range(2020, 2025) for m in range(1, 13)]
month_keys = [(y, m) for y in range(2020, 2025) for m in range(1, 13)]
receipts = []
out = csv.writer(sys.stdout, lineterminator='\n')
out.writerow(['initial_members', 'world', 'arm', 'year', 'month', 'voluntary_quits',
              'all_exits', 'active_on_twelfth', 'person_calendar_days'])
for world in plan['worlds']:
    for arm in plan['arms']:
        directory = study / f'n300-startup-organic-pre0-post5-w{world}' / arm
        receipt_bytes = (directory / 'arm.json').read_bytes()
        receipt = json.loads(receipt_bytes)
        assert receipt['arm'] == arm and receipt['days'] == days
        assert receipt['source_sha256'] == plan['source_sha256'] and receipt['branch_day'] == 0
        verified = {}
        for filename, key in [('final_state.json', 'final_state_json'), ('summary.json', 'summary_json'),
                              ('timeseries.csv', 'timeseries_csv')]:
            data = (directory / filename).read_bytes()
            digest = sha256(data).hexdigest()
            assert receipt['files'][key] == digest, (world, arm, filename)
            verified[key] = data
        state = json.loads(verified['final_state_json'])['state']
        summary = json.loads(verified['summary_json'])
        assert state['day'] == days and int(summary['world']) == world
        assert summary['calendar_start'] == str(start) and summary['calendar_end_exclusive'] == str(end)
        assert state['execution_source_sha256'] == plan['source_sha256']
        people = state['people']
        assert len({p['id'] for p in people}) == len(people) == int(summary['ever_people'])
        quit_counts, exit_counts = Counter(), Counter()
        delta = [0] * (days + 1)
        for person in people:
            entry = person['entered_day']
            exit_day = person['exited_day']
            upper = days if exit_day is None else exit_day
            assert 0 <= entry <= upper <= days
            assert upper - entry == person['active_calendar_days'], (world, arm, person['id'])
            delta[entry] += 1
            delta[upper] -= 1
            if exit_day is not None:
                when = start + timedelta(days=exit_day)
                key = when.year, when.month
                exit_counts[key] += 1
                if person['exit_reason'] == 'voluntary_exit':
                    quit_counts[key] += 1
        assert sum(exit_counts.values()) == summary['exits']
        headcount = 0
        census = []
        monthly_exposure = Counter()
        for i, row in enumerate(csv.DictReader(io.StringIO(verified['timeseries_csv'].decode()))):
            when = start + timedelta(days=i)
            assert row['calendar_date'] == str(when) and int(row['day']) == i
            headcount += delta[i]
            assert headcount == int(row['active_members']), (world, arm, i)
            census.append(headcount)
            monthly_exposure[when.year, when.month] += headcount
        assert len(census) == days and sum(monthly_exposure.values()) == summary['active_member_calendar_days']
        for j, (year, month) in zip(month_days, month_keys, strict=True):
            key = year, month
            out.writerow([300, world, arm, year, month, quit_counts[key], exit_counts[key],
                          census[j], monthly_exposure[key]])
        receipts.append({
            'world': world, 'arm': arm, 'arm_receipt_sha256': sha256(receipt_bytes).hexdigest(),
            'retained_files': {name: sha256(data).hexdigest() for name, data in verified.items()},
        })
print(json.dumps({'plan_sha256': sha256(plan_bytes).hexdigest(), 'source_sha256': plan['source_sha256'],
                  'arm_count': len(receipts), 'receipts': receipts}, indent=2), file=sys.stderr)
