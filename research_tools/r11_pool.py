"""Pool admitted inference reports of one study run in batches of worlds.

A large complete-retention study is run in consecutive batches of worlds so that
each batch's complete raw evidence can be verified by r11_inference.infer() on
the machine, uploaded and removed from the local disk before the next batch.
Each batch report must be fully admitted (no admission errors) and carry the
same study identity (source commit and study arguments other than the world
list); worlds must not repeat. The pooled summary applies the same paired
Student-t summary and Holm family to the union of admitted world-level values,
which is what infer() would report on the union of the batches.

Usage: python research_tools/r11_pool.py BATCH_INFERENCE.json [...] > pooled.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_inference import _difference_summary, holm  # noqa: E402
from research_tools.r11_world_count import PRIMARY  # noqa: E402


def pool(reports, deltas=None):
    if not reports:
        raise ValueError('no batch reports')
    identity = reports[0].get('study_identity')
    for r in reports:
        if r.get('schema') != 'r11-roster-and-raw-inference-v2' or r.get('admission_errors') != []:
            raise ValueError('every batch must be a fully admitted inference report')
        if r.get('study_identity') != identity or identity is None:
            raise ValueError('batches belong to different studies or lack a study identity')
    seen = set()
    for r in reports:
        worlds = {w for rows in r['comparisons'].values() for row in rows.values() for w, *_ in row.get('world_values', [])}
        if worlds & seen:
            raise ValueError('a world appears in more than one batch')
        seen |= worlds
    out = {'schema': 'r11-pooled-batches-v1', 'study_identity': identity, 'batches': len(reports),
           'reference': reports[0]['reference'], 'comparisons': {}}
    for arm in reports[0]['comparisons']:
        rows = {}
        for key in reports[0]['comparisons'][arm]:
            values, missing, assigned = [], [], 0
            for r in reports:
                row = r['comparisons'][arm][key]
                values += row.get('world_values', [])
                missing += row.get('missing_worlds', [])
                assigned += row.get('assigned_worlds', 0)
            values.sort()
            summary = _difference_summary([a-b for _, a, b in values], key, deltas)
            summary.update(assigned_worlds=assigned, missing_worlds=missing,
                           conditional_on_completion=bool(missing), world_values=values)
            rows[key] = summary
        family = [k for k in PRIMARY if k in rows]
        adjusted = holm([rows[k].get('p_two_sided') if rows[k].get('p_two_sided') is not None else 1. for k in family])
        for k, adj in zip(family, adjusted):
            rows[k]['p_holm'] = adj if rows[k].get('p_two_sided') is not None else None
            rows[k]['holm_family_size'] = len(family)
        out['comparisons'][arm] = rows
    return out


if __name__ == '__main__':
    print(json.dumps(pool([json.loads(Path(p).read_text()) for p in sys.argv[1:]]), indent=1, allow_nan=False))
