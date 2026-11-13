"""Recompute the descriptive pilot contrasts with the Python standard library."""

import csv
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decimal_text(value, places):
    with localcontext() as context:
        context.prec = 40
        return format(Decimal(value.numerator) / Decimal(value.denominator), f'.{places}f')


def parse_daily(data, pairs, first, adoption, last):
    fields = ('calendar_date', 'revenue_resource_units_daily', 'operating_resource_units_daily',
              'active_member_calendar_days_daily')
    reader = csv.DictReader(data.decode('ascii').splitlines())
    require(reader.fieldnames == ['world', 'arm', *fields], 'Unexpected daily columns')
    daily = {pair: [] for pair in pairs}
    for row in reader:
        series = daily[row['world'], row['arm']]
        when = date.fromisoformat(row[fields[0]])
        require(when == first + timedelta(days=len(series)), 'Missing or out-of-order date')
        revenue, expense, exposure = (Fraction(row[field]) for field in fields[1:])
        require(exposure >= 0 and exposure.denominator == 1, 'Invalid exposure')
        series.append((revenue, expense, exposure))
    horizon = (last - first).days + 1
    shared = (adoption - first).days
    require(horizon == 2557 and shared == 731, 'Unexpected study dates')
    require(all(len(series) == horizon for series in daily.values()), 'Incomplete daily series')
    rates = {}
    for pair, series in daily.items():
        revenue, expense, exposure = map(sum, zip(*series[shared:], strict=True))
        require(exposure > 0, 'Missing post-adoption exposure')
        rates[pair] = Fraction('365.25') * (revenue - expense) / exposure
    return daily, rates, horizon, shared


def parse_confirmation(data, pairs, horizon):
    reader = csv.DictReader(data.decode('ascii').splitlines())
    require(reader.fieldnames == ['world', 'arm', 'days', 'confirmed_records'],
            'Unexpected histogram columns')
    buckets = {pair: {} for pair in pairs}
    for row in reader:
        arm = buckets[row['world'], row['arm']]
        delay, count = int(row['days']), int(row['confirmed_records'])
        require(0 <= delay < horizon and count > 0 and delay not in arm, 'Invalid histogram bin')
        arm[delay] = count
    means = {}
    for pair, arm in buckets.items():
        require(arm, 'Missing confirmation data')
        means[pair] = Fraction(sum(delay * n for delay, n in arm.items()), sum(arm.values()))
    return means


def verify(root):
    fixed = json.loads((root / 'manifest.json').read_text())
    worlds = [str(n) for n in fixed['worlds']]
    hierarchy, witness = fixed['arms']
    require(worlds == ['0', '1'] and (hierarchy, witness) == ('control-hierarchy', 'dams-witness'),
            'Unexpected study roster')
    require(fixed['initial_members'] == 50000, 'Unexpected initial population')
    inputs = {}
    for name in ('daily.csv', 'confirmation-histogram.csv'):
        content = (root / name).read_bytes()
        require(sha256(content).hexdigest() == fixed['inputs'][name], 'File hash differs: ' + name)
        inputs[name] = content
    pairs = [(world, arm) for world in worlds for arm in (hierarchy, witness)]
    dates = [date.fromisoformat(fixed[key]) for key in ('first_day', 'adoption_day', 'last_day')]
    daily, rates, horizon, shared = parse_daily(inputs['daily.csv'], pairs, *dates)
    means = parse_confirmation(inputs['confirmation-histogram.csv'], pairs, horizon)
    queues = {}
    for world, arm in pairs:
        pending = fixed['terminal_pending'][world + '/' + arm]
        require(set(pending) == {'review', 'commit'} and
                all(type(n) is int and n >= 0 for n in pending.values()), 'Invalid queue count')
        queues[world, arm] = sum(pending.values())

    result = {}
    for world in worlds:
        control, selected = (world, hierarchy), (world, witness)
        require(daily[control][:shared] == daily[selected][:shared], 'Shared history differs')
        result[world] = {
            'five_year_net_operating_units_per_member_year': decimal_text(rates[selected] - rates[control], 6),
            'seven_year_mean_confirmation_delay_days': decimal_text(means[selected] - means[control], 6),
            'terminal_review_or_commit_records': queues[selected] - queues[control],
        }
    return result


if __name__ == '__main__':
    print(json.dumps(verify(Path(__file__).resolve().parent), indent=2))
