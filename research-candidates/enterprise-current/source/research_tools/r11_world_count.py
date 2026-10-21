"""Apply the pre-registered world-count rule (docs/r11-formal-protocol.md) to pilot output.

R = max(10, ceil((2.262 * s / (delta / 2))**2)) with delta = 2 % of the reference-arm
pilot mean, using the largest requirement among the three primary outcomes for the
main comparison (dams-witness vs control-hierarchy).
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_outcomes import outcomes  # noqa: E402

PRIMARY = ('net_operating_result_per_member_year', 'decision_regret_per_available_member_hour',
           'governance_cost_per_member_year')


def world_count(root, arm='dams-witness', reference='control-hierarchy', t=2.262, minimum=10):
    worlds = sorted(p for p in Path(root).iterdir() if (p/reference/'arm.json').exists() and (p/arm/'arm.json').exists())
    rows = {}
    for k in PRIMARY:
        ref = [outcomes(w/reference)[k] for w in worlds]
        dif = [outcomes(w/arm)[k]-outcomes(w/reference)[k] for w in worlds]
        if len(dif) < 2 or any(v is None for v in ref+dif):
            rows[k] = {'worlds': len(dif), 'status': 'insufficient pilot pairs'}
            continue
        delta = .02*abs(statistics.fmean(ref))
        s = statistics.stdev(dif)
        need = max(minimum, math.ceil((t*s/(delta/2))**2)) if delta > 0 else None
        rows[k] = {'worlds': len(dif), 'reference_mean': statistics.fmean(ref), 'delta': delta,
                   'sd_paired_difference': s, 'mean_paired_difference_pilot': statistics.fmean(dif),
                   'required_worlds': need}
    req = [r['required_worlds'] for r in rows.values() if r.get('required_worlds')]
    return {'rule': 'max(10, ceil((2.262*s/(delta/2))^2)), delta=2% of reference mean',
            'pilot_worlds': len(worlds), 'outcomes': rows, 'R': max(req) if req else None}


if __name__ == '__main__':
    print(json.dumps(world_count(sys.argv[1]), indent=1))
