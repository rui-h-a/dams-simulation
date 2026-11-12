from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from statistics import mean, stdev

WORLDS = list(range(2000, 2010))
EFFECTS = ('0', '0.005', '0.01', '0.05', '0.1', '0.2')
ENDPOINTS = ('net_operating_result_per_member_year', 'decision_regret_per_available_member_hour',
             'operating_margin_ratio', 'output_per_available_member_hour')


def interval(values):
    center = mean(values)
    width = 2.2621571627409915 * stdev(values) / len(values)**0.5
    return [center, center-width, center+width]


def main():
    raw = Path(__file__).with_name('world-pairs.json').read_bytes()
    if sha256(raw).hexdigest() != 'f897b26ce301e49ebae31280c94bd280230139886724bf612905f68ea8b4695a':
        raise SystemExit('world-pairs.json failed SHA-256 verification')
    inputs = json.loads(raw)
    assert tuple(inputs) == EFFECTS
    results, differences = {}, {}
    for effect, block in inputs.items():
        assert tuple(block['outcomes']) == ENDPOINTS
        paired = {}
        for name, values in block['outcomes'].items():
            assert [row[0] for row in values] == WORLDS
            assert all(isfinite(dams) and isfinite(hierarchy) for _, dams, hierarchy in values)
            paired[name] = [dams-hierarchy for _, dams, hierarchy in values]
        differences[effect] = paired
        results[effect] = {'basis': block['basis'], 'paired_estimates': {name: interval(paired[name]) for name in ENDPOINTS}}
        if effect != '0':
            results[effect]['within_world_change_from_zero_unadjusted'] = {
                name: interval([a-b for a,b in zip(paired[name], differences['0'][name], strict=True)])
                for name in ENDPOINTS
            }
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
