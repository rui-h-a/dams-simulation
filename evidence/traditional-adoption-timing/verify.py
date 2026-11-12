from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, stdev

def estimate(rows, staged, triggered):
    differences = [r[staged] - r[triggered] for r in rows]
    center = mean(differences)
    width = 1.9977296543176926 * stdev(differences) / len(differences)**0.5
    return [center, center - width, center + width]

def main():
    raw = Path(__file__).with_name('paired-worlds.json').read_bytes()
    if sha256(raw).hexdigest() != '80f3482de576bf8ba888544cef8ea6a27c50dd46b2ea15dbda66e424c4adc7cc':
        raise SystemExit('paired-worlds.json failed SHA-256 verification')
    rows = json.loads(raw)
    assert [row['world'] for row in rows] == list(range(1000, 1065))
    for row in rows:
        assert row['triggered_adoption'] == row['fixed_adoption'] == {'0':731, '1':731}
        assert row['staged_adoption'] == {'0':731, '1':913}
    result = dict(worlds=len(rows), first_eligible_day=731, staged_second_guild_day=913,
                  staged_minus_triggered_net=estimate(rows,'staged_net','triggered_net'),
                  staged_minus_triggered_delay=estimate(rows,'staged_delay','triggered_delay'))
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
