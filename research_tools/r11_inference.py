"""Confirmatory inference for an R11 study, following docs/r11-formal-protocol.md.

For each comparison arm against the reference arm and each primary outcome:
paired differences over independent worlds, mean, paired Student-t 95 %
interval, two-sided p-value, Holm adjustment across the three primary outcomes
within the comparison, and the achieved half-width against the declared
threshold delta (2 % of the reference-arm mean of the pilot, passed in).
Secondary outcomes get the same interval, labelled unadjusted. A complete
bound plan supplies the roster; missing/failed/invalid worlds remain visible.
Full original prehistory and arm evidence is verified before outcomes are read.
This is raw-data validation, not external calibration or independent transitions.
Degenerate differences have no Student-t interval/test or precision claim.

Usage: python research_tools/r11_inference.py STUDY_ROOT [--first-world 1000]
           [--delta-json PILOT_WORLD_COUNT.json] [--inventory-only]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_outcomes import OUTCOMES, outcomes  # noqa: E402
from research_tools.r11_world_count import PRIMARY  # noqa: E402


def _betacf(a, b, x):
    """Continued fraction for the regularized incomplete beta (Lentz)."""
    tiny = 1e-300
    c, d = 1., 1.-(a+b)*x/(a+1)
    d = 1/(d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2*m
        aa = m*(b-m)*x/((a+m2-1)*(a+m2))
        d = 1+aa*d; d = 1/(d if abs(d) > tiny else tiny)
        c = 1+aa/c if abs(1+aa/c) > tiny else tiny
        h *= d*c
        aa = -(a+m)*(a+b+m)*x/((a+m2)*(a+m2+1))
        d = 1+aa*d; d = 1/(d if abs(d) > tiny else tiny)
        c = 1+aa/c if abs(1+aa/c) > tiny else tiny
        delta = d*c; h *= delta
        if abs(delta-1) < 1e-15:
            return h
    raise ArithmeticError('incomplete beta did not converge')


def _ibeta(a, b, x):
    if x <= 0:
        return 0.
    if x >= 1:
        return 1.
    front = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    if x < (a+1)/(a+b+2):
        return front*_betacf(a, b, x)/a
    return 1-front*_betacf(b, a, 1-x)/b


def t_two_sided_p(t, df):
    return _ibeta(df/2, .5, df/(df+t*t))


def t_quantile(p, df):
    """Upper quantile: P(T <= q) = p, for 0.5 < p < 1, by bisection on the CDF."""
    lo, hi = 0., 1e3
    for _ in range(200):
        mid = (lo+hi)/2
        if 1-t_two_sided_p(mid, df)/2 < p:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


def holm(pvalues):
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [0.]*len(pvalues), 0.
    for rank, i in enumerate(order):
        running = max(running, min(1., (len(pvalues)-rank)*pvalues[i]))
        adjusted[i] = running
    return adjusted


def _planned_roster(root, first_world, reference):
    """Enumerate assigned worlds, never infer a roster from surviving results."""
    plan = json.loads((root/'plan.json').read_text())
    worlds, arms = plan.get('worlds'), plan.get('arms')
    if (not isinstance(worlds, list) or not worlds
            or any(type(w) is not int or w < 0 for w in worlds)
            or len(set(worlds)) != len(worlds)):
        raise ValueError('plan requires unique nonnegative integer world identities')
    from research_tools.r11_design import ARMS, CONTEXTS, GROWTH_MODES
    if (not isinstance(arms, list) or len(set(arms)) != len(arms)
            or reference not in arms or any(a not in ARMS for a in arms)):
        raise ValueError('plan arm roster differs or reference is unassigned')
    if plan.get('context') not in CONTEXTS or plan.get('growth') not in GROWTH_MODES:
        raise ValueError('unknown plan context or growth')
    for key in ('scale', 'pre_years', 'post_years', 'seed'):
        if type(plan.get(key)) is not int or plan[key] < (1 if key in ('scale', 'post_years') else 0):
            raise ValueError('invalid plan '+key)
    paths = {w: root/f"n{plan['scale']}-{plan['context']}-{plan['growth']}-pre{plan['pre_years']}-post{plan['post_years']}-w{w}"
             for w in worlds if w >= first_world}
    return plan, paths


def _bound_files(paths):
    from dams_sim.storage import file_digest
    result = {}
    for path in paths:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents):
            raise ValueError('evidence must be a regular file under non-symlink directories')
        result[str(path)] = file_digest(path)
    return result


def _recheck_files(expected):
    if _bound_files(expected) != expected:
        raise ValueError('verified evidence changed during inference')


def _snapshot_bound(database, envelope):
    """A post-verifier file hash must still match the original snapshot pin."""
    bound = _bound_files([database])
    if (bound[str(database)] != envelope['ledger']['sha256']
            or Path(database).stat().st_size != envelope['ledger']['bytes']):
        raise ValueError('snapshot ledger changed after verification')
    return bound


def _verify_prehistory(path, plan, world):
    from dams_sim.config import Config
    from dams_sim.storage import file_digest, canonical, digest
    from dams_sim.longitudinal_model import verify_snapshot
    from research_tools.r11_design import day_of_year_offset, base_config
    pre = path/'prehistory'
    receipt_path = pre/'prehistory.json'
    initial = _bound_files([receipt_path])
    receipt = json.loads(receipt_path.read_text())
    config = Config.from_dict(receipt['config'])
    branch = day_of_year_offset(plan['pre_years'])
    if (receipt.get('kind') != 'r11-prehistory'
            or receipt.get('source_sha256') != plan['source_sha256']
            or receipt.get('branch_day') != branch
            or (config.n, config.seed, config.world, config.days) !=
               (plan['scale'], plan['seed'], world,
                day_of_year_offset(plan['pre_years']+plan['post_years']))):
        raise ValueError('prehistory source/config/calendar differs from assigned world')
    if plan['growth'] in ('acquisition', 'organic+acquisition'):
        # Regenerating this recipe would execute another target simulation.
        # It needs a pre-frozen full world configuration, never a self-declared
        # receipt hash or a silently regenerated acquisition target.
        frozen = plan.get('frozen_world_config_sha256', {}).get(str(world))
        if frozen != digest(canonical(config.to_dict())):
            raise ValueError('acquisition world lacks pre-frozen full configuration binding; target simulation not rerun')
    else:
        expected, _ = base_config(scale=plan['scale'], context=plan['context'], growth=plan['growth'],
                                  world=world, seed=plan['seed'], pre_years=plan['pre_years'],
                                  post_years=plan['post_years'], variant=plan.get('variant', 'baseline'))
        if config.to_dict() != Config.from_dict(expected).to_dict():
            raise ValueError('complete prehistory configuration differs from plan recipe')
    name = receipt.get('checkpoint')
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('invalid prehistory checkpoint name')
    checkpoint = pre/name
    initial.update(_bound_files([checkpoint]))
    if initial[str(checkpoint)] != receipt.get('checkpoint_sha256'):
        raise ValueError('prehistory checkpoint bytes differ')
    env, database = verify_snapshot(checkpoint, expected_config=config)
    if env['state']['day'] != branch:
        raise ValueError('prehistory checkpoint does not reach branch boundary')
    _recheck_files(initial)
    initial.update(_snapshot_bound(database, env))
    return receipt, {'receipt_sha256': initial[str(receipt_path)], 'files': initial,
                     'origin': {'parent_source_sha256': plan['source_sha256'],
                                'parent_config_sha256': env['config_sha256'],
                                'parent_day': branch,
                                'parent_state_semantic_sha256': env['state_semantic_sha256']}}


def _verified_outcomes(path, arm, plan, prehistory, prehistory_proof):
    """Check original full evidence before reading any summary into a sample."""
    from dams_sim.config import Config
    from dams_sim.storage import file_digest
    from dams_sim.longitudinal_model import verify_snapshot
    from research_tools.r11_design import arm_config, ARMS
    from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence
    directory = path/arm
    receipt_path = directory/'arm.json'
    initial_receipt = _bound_files([receipt_path])
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('kind') != 'r11-arm' or receipt.get('arm') != arm
            or receipt.get('arm_spec') != ARMS[arm]
            or receipt.get('source_sha256') != plan['source_sha256']
            or receipt.get('parent_prehistory_sha256') != prehistory_proof['receipt_sha256']
            or receipt.get('branch_day') != prehistory['branch_day']
            or receipt.get('retention') != 'journal'):
        raise ValueError('arm identity/source/parent differs or original journal is missing')
    files = receipt.get('files', {})
    for name, key in (('final_state.json','final_state_json'),
                      ('timeseries.csv','timeseries_csv'), ('summary.json','summary_json')):
        if file_digest(directory/name) != files.get(key):
            raise ValueError('arm input bytes differ: '+name)
    parent = Config.from_dict(prehistory['config'])
    child = Config.from_dict(arm_config(prehistory['config'], prehistory['branch_day'], arm))
    if Config.from_dict(receipt['config']).to_dict() != child.to_dict() or receipt.get('days') != child.days:
        raise ValueError('arm configuration differs from prescribed counterfactual')
    final = directory/'final_state.json'
    inputs = {**initial_receipt, **_bound_files([final, directory/'timeseries.csv', directory/'summary.json'])}
    env, database = verify_snapshot(final, expected_config=child)
    if env['state'].get('branch_origin') != prehistory_proof['origin']:
        raise ValueError('arm did not branch from verified complete parent state')
    if env['state']['day'] != child.days:
        raise ValueError('arm has no complete terminal horizon')
    original = files.get('original_ledger', {})
    if (original.get('file') != Path(database).name
            or original.get('sha256') != file_digest(database)
            or original.get('bytes') != Path(database).stat().st_size
            or receipt.get('ledger_semantic_sha256') != env['ledger']['semantic_sha256']
            or receipt.get('state_semantic_sha256') != env['state_semantic_sha256']):
        raise ValueError('full original ledger/state binding differs')
    inputs.update(_snapshot_bound(database, env))
    summary = json.loads((directory/'summary.json').read_text())
    verify_daily_evidence(directory/'timeseries.csv', child, env['state'], database,
                          config_history=((prehistory['branch_day'], parent),))
    verify_summary_evidence(summary, child, env['state'], database)
    if child.enterprise_acquisition is not None:
        from research_tools.validate_acquisition_accounts import verify_acquisition_accounts
        verify_acquisition_accounts(child, env['state'], database)
    if child.organization_tasks is not None:
        from research_tools.validate_organization_tasks import verify_organization_accounts
        verify_organization_accounts(child, env['state'], database)
    # Verify producer summaries *and* recalculate outcome denominators from the
    # authenticated files. Rehash after use so a concurrent producer cannot pass.
    result = outcomes(directory)
    for name, key in (('final_state.json','final_state_json'),
                      ('timeseries.csv','timeseries_csv'), ('summary.json','summary_json')):
        if file_digest(directory/name) != files[key]:
            raise ValueError('arm input changed during inference: '+name)
    if json.loads(receipt_path.read_text()) != receipt:
        raise ValueError('arm receipt changed during inference')
    _recheck_files(inputs)
    return result, inputs


def _difference_summary(d, key, deltas):
    row = {'unit': OUTCOMES[key][0], 'worlds': len(d),
           'role': 'primary' if key in PRIMARY else 'secondary (unadjusted)'}
    if len(d) < 2:
        return {**row, 'status': 'fewer than two complete pairs'}
    n, m, s = len(d), statistics.fmean(d), statistics.stdev(d)
    row.update(mean_difference=m, sd_difference=s)
    if s == 0:
        # Neither zero nor nonzero constant samples identify a Student-t
        # statistic. Do not convert undefined 0/0 to infinite significance or
        # certify a zero-width interval (especially for zero closure events).
        row.update(status='degenerate paired differences; Student-t inference undefined',
                   ci95=None, half_width=None, p_two_sided=None)
    else:
        se = s/math.sqrt(n)
        q = t_quantile(.975, n-1)
        row.update(status='conditional complete-pair Student-t summary',
                   ci95=[m-q*se, m+q*se], half_width=q*se,
                   p_two_sided=t_two_sided_p(m/se, n-1))
    if deltas is not None and key in deltas:
        delta = deltas[key]
        if type(delta) not in (int, float) or not math.isfinite(delta) or delta <= 0:
            raise ValueError('precision delta must be positive and finite')
        row['delta'] = delta
        row['half_width_within_delta_over_2'] = (row['half_width'] is not None
                                               and row['half_width'] <= delta/2)
    return row


def infer(root, *, first_world=1000, reference='control-hierarchy', deltas=None, inventory_only=False):
    from dams_sim.storage import file_digest, source_hash
    from research_tools.r11_retention import science_manifest, RETENTION_POLICY
    root = Path(root).resolve()
    if type(first_world) is not int or first_world < 0:
        raise ValueError('first_world must be a nonnegative integer')
    initial_plan = _bound_files([root/'plan.json'])
    plan, worlds = _planned_roster(root, first_world, reference)
    initial_plan_sha = initial_plan[str(root/'plan.json')]
    report = {'schema': 'r11-roster-and-raw-inference-v2', 'study_root': str(root),
              'reference': reference, 'first_world': first_world,
              'assigned_worlds': list(worlds),
              'excluded_before_first_world': [w for w in plan['worlds'] if w < first_world],
              'failures': sorted(str(f.relative_to(root)) for f in root.rglob('failure*.json')),
              'admission_errors': [], 'world_inventory': [], 'comparisons': {},
              'measurement_warnings': ['The legacy primary governance_cost_per_member_year mixes member-hours and resource units. Separate components are reported; the legacy index is not a one-unit cost, and a revised formal endpoint requires prospective freezing.'],
              'complete_preservation_accepted': False, 'scientific_completion': False,
              'scope': 'raw integrity and conditional model inference; not external validation, independent transitions, recovery or off-GCP preservation'}
    if plan.get('source_sha256') != source_hash():
        report['admission_errors'].append('plan engine source differs; use its frozen source')
    dependencies = science_manifest(Path(__file__).resolve().parent.parent)
    if plan.get('scientific_dependencies') != dependencies:
        report['admission_errors'].append('plan has no matching complete science/execution binding; original plan not changed')
    if plan.get('retention_policy') != RETENTION_POLICY:
        report['admission_errors'].append('plan does not bind complete original journal retention')
    admitted = not inventory_only and not report['admission_errors']
    cache, world_proofs, arm_proofs = {}, {}, {}
    for world, path in worlds.items():
        row = {'world': world, 'path': path.name, 'arms': {}}
        prehistory, pre_sha = None, None
        if admitted:
            try:
                prehistory, pre_sha = _verify_prehistory(path, plan, world)
                world_proofs[world] = pre_sha['files']
            except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError) as error:
                row['prehistory_error'] = type(error).__name__+': '+str(error)
        for arm in plan['arms']:
            entry = {'status': 'missing arm receipt'}
            if (path/arm/'arm.json').exists():
                entry['status'] = 'present; not admitted for inference'
                if admitted and prehistory is not None:
                    try:
                        values, proof = _verified_outcomes(path, arm, plan, prehistory, pre_sha)
                        cache[world, arm] = values
                        arm_proofs[world, arm] = proof
                        entry['status'] = 'raw verified'
                    except (OSError, ValueError, KeyError, TypeError, ArithmeticError, sqlite3.DatabaseError) as error:
                        entry = {'status': 'invalid raw evidence', 'reason': type(error).__name__+': '+str(error)}
            row['arms'][arm] = entry
        report['world_inventory'].append(row)
    # Recheck all consumed originals after reading the entire roster. A deleted
    # or changed SQLite/parent invalidates the affected sample, not the world ID.
    for row in report['world_inventory']:
        world = row['world']
        if world in world_proofs:
            try:
                _recheck_files(world_proofs[world])
            except (OSError, ValueError) as error:
                row['prehistory_error'] = type(error).__name__+': '+str(error)
                for arm in plan['arms']:
                    cache.pop((world, arm), None)
                    row['arms'][arm] = {'status': 'invalid prehistory on final recheck'}
        for arm in plan['arms']:
            key = world, arm
            if key in cache:
                try:
                    _recheck_files(arm_proofs[key])
                except (OSError, ValueError) as error:
                    cache.pop(key)
                    row['arms'][arm] = {'status': 'invalid raw evidence on final recheck',
                                         'reason': type(error).__name__+': '+str(error)}
    report['worlds_with_reference'] = sum((w, reference) in cache for w in worlds)
    if admitted:
        for arm in (a for a in plan['arms'] if a != reference):
            rows = {}
            pairs = [w for w in worlds if (w, arm) in cache and (w, reference) in cache]
            for key in OUTCOMES:
                differences, missing = [], []
                for world in worlds:
                    if world not in pairs:
                        missing.append({'world': world, 'reason': 'missing or invalid paired raw evidence'})
                        continue
                    values = cache[world, arm].get(key), cache[world, reference].get(key)
                    if any(v is None or type(v) not in (int, float) or not math.isfinite(v) for v in values):
                        missing.append({'world': world, 'reason': 'undefined or nonfinite outcome'})
                        continue
                    differences.append(values[0]-values[1])
                row = _difference_summary(differences, key, deltas)
                row.update(assigned_worlds=len(worlds), missing_worlds=missing,
                           conditional_on_completion=bool(missing))
                rows[key] = row
            # The predeclared primary family (Amendment 8: four endpoints) cannot shrink when a test is
            # unavailable. A placeholder 1 reserves its multiplicity slot only;
            # its unavailable p-value remains null in the output.
            adjusted = holm([rows[k].get('p_two_sided') if rows[k].get('p_two_sided') is not None else 1.
                             for k in PRIMARY])
            for key, adj in zip(PRIMARY, adjusted):
                rows[key]['p_holm'] = adj if rows[key].get('p_two_sided') is not None else None
                rows[key]['holm_family_size'] = len(PRIMARY)
            report['comparisons'][arm] = rows
    if (file_digest(root/'plan.json') != initial_plan_sha
            or science_manifest(Path(__file__).resolve().parent.parent) != dependencies):
        raise ValueError('plan/source changed during inference; report refused')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--first-world', type=int, default=1000)
    ap.add_argument('--delta-json', help='output of r11_world_count.py on the pilot')
    ap.add_argument('--inventory-only', action='store_true', help='list original assigned/missing worlds without inference')
    ns = ap.parse_args()
    deltas = None
    if ns.delta_json:
        wc = json.loads(Path(ns.delta_json).read_text())
        deltas = {k: v['delta'] for k, v in wc['outcomes'].items() if 'delta' in v}
    print(json.dumps(infer(ns.root, first_world=ns.first_world, deltas=deltas, inventory_only=ns.inventory_only), indent=1, allow_nan=False))
