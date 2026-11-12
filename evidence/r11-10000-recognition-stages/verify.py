"""Recompute review and settlement stocks for the 10,000-member study."""
import csv
from hashlib import sha256
from itertools import product
import json
from math import sqrt
from pathlib import Path
import re
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parent
WORLDS = range(1000, 1010)
ARMS = ('control-hierarchy', 'control-process', 'dams-witness', 'linear-witness')
COUNTS = (
    'work_events', 'confirmed_records', 'reviews_rejected', 'appeals_processed', 'appeals_denied',
    'unfinished_records', 'pre_review_count', 'post_review_count', 'terminal_waiting_review',
    'terminal_waiting_settlement', 'terminal_waiting_appeal',
)
HASHES = ('summary_sha256', 'timeseries_sha256', 'final_state_sha256', 'arm_receipt_sha256', 'prehistory_sha256')
OUTCOMES = ('post_review_count', 'terminal_waiting_review', 'terminal_waiting_settlement', 'terminal_waiting_appeal')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_rows(path):
    rows = {}
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == ['world', 'arm', *COUNTS, *HASHES], 'Unexpected columns')
        for row in reader:
            require(None not in row and all(value is not None for value in row.values()), 'Incomplete row')
            require(re.fullmatch(r'[0-9]+', row['world']), 'Invalid world number')
            key = (int(row['world']), row['arm'])
            require(key not in rows, f'Duplicate arm {key}')
            require(all(re.fullmatch(r'[0-9]+', row[k]) for k in COUNTS), f'Invalid count in {key}')
            require(all(re.fullmatch(r'[0-9a-f]{64}', row[k]) for k in HASHES), f'Invalid digest in {key}')
            n = {k: int(row[k]) for k in COUNTS}
            reviews = n['pre_review_count'] + n['post_review_count']
            returns = n['appeals_processed'] - n['appeals_denied']
            require(returns >= 0 and reviews >= n['reviews_rejected'], f'Inconsistent exit counts in {key}')
            require(n['work_events'] - reviews == n['terminal_waiting_review'], f'First-review stock differs in {key}')
            require(reviews - n['reviews_rejected'] + returns - n['confirmed_records'] == n['terminal_waiting_settlement'], f'Settlement stock differs in {key}')
            require(n['terminal_waiting_review'] + n['terminal_waiting_settlement'] == n['unfinished_records'], f'Total stock differs in {key}')
            rows[key] = {**n, 'prehistory_sha256': row['prehistory_sha256']}
    require(set(rows) == set(product(WORLDS, ARMS)), 'Arm roster differs')
    for w in WORLDS:
        require(len({(rows[w,a]['pre_review_count'], rows[w,a]['prehistory_sha256']) for a in ARMS}) == 1, f'Shared prehistory differs in {w}')
    return rows


def pair(values):
    mean = fmean(values)
    half_width = 2.2621571628540993 * stdev(values) / sqrt(len(values))
    return {'mean': mean, 'paired_t95': [mean - half_width, mean + half_width]}


def main():
    path = ROOT / 'arm-stages.csv'
    rows = load_rows(path)
    arm_means = {a: {k: fmean(rows[w,a][k] for w in WORLDS) for k in OUTCOMES} for a in ARMS}
    comparisons = {
        a: {k: pair([rows[w,'dams-witness'][k] - rows[w,a][k] for w in WORLDS]) for k in OUTCOMES}
        for a in ARMS if a != 'dams-witness'
    }
    result = {'input_sha256': sha256(path.read_bytes()).hexdigest(), 'arm_count': len(rows),
              'world_count': len(WORLDS), 'arm_means': arm_means, 'comparisons': comparisons}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
