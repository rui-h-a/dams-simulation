"""Independent, read-only reconstruction of spec-v2 recovery calculations.

This validates retained raw summaries and traces, not empirical calibration or
unique identification. It starts no Model and does not alter a study.
"""
from __future__ import annotations

import csv
import json
import math
import statistics

from dams_sim.storage import canonical
from research_tools.recovery import FLOORS, PATTERNS, RULES, distance, moments


def _check_csv(study, path, rows):
    """Every row and field must be the producer's exact serialized raw value."""
    study.verify_file(path)
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        actual = list(reader)
    expected_columns = set().union(*(set(row) for row in rows))
    if columns is None or len(columns) != len(set(columns)) or set(columns) != expected_columns:
        raise ValueError('recovery CSV columns differ: ' + path.name)
    if len(actual) != len(rows):
        raise ValueError('recovery CSV row count differs: ' + path.name)
    for observed, expected in zip(actual, rows):
        if None in observed:
            raise ValueError('recovery CSV has an extra cell: ' + path.name)
        for column in columns:
            value = expected.get(column)
            serialized = '' if value is None else json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            if observed[column] != serialized:
                raise ValueError('recovery CSV differs from raw/calculation: ' + path.name + '/' + column)


def _patterns(study, stage):
    patterns = []
    for row in study.stages[stage]:
        # CheckedStudy already binds this row to its exact planned Config,
        # complete output hashes, summary, and the complete raw state identity.
        fields = ('kind', 'candidate', 'case', 'behavior_rule', 'autonomy_response',
                  'review_capacity_per_member_day')
        tags = {key: row[key] for key in fields if key in row}
        if row['kind'] == 'null':
            tags = {'kind': 'null', 'regime': row['regime']}
        trace = study.trace(row)
        if not trace:
            raise ValueError('recovery raw trace is empty')
        pattern = {**tags, 'world': row['world'],
                   'work_per_member_day': row['produced_work_units'] / (study.spec.n * study.spec.days),
                   'regret_per_guild_day': row['decision_regret_units'] / (study.spec.guilds * study.spec.days),
                   'unfinished_per_member': row['unfinished_records'] / study.spec.n,
                   'mean_trust': trace[-1]['mean_trust']}
        if any(not math.isfinite(pattern[key]) for key in PATTERNS):
            raise ValueError('recovery pattern is not finite')
        patterns.append(pattern)
    return patterns


def validate_recovery(study) -> None:
    """Recompute selection on training worlds and scoring on fresh holdouts.

    Uses the declared historical numeric moments/distance functions, but
    independently reconstructs every input from checked raw summaries/traces.
    Null records are checked against their raw paired cases; their observed
    equivalence is not treated as an empirical identification result.
    """
    path = study.root / 'recovery'
    manifest = study.load_json(path / 'manifest.json')
    required = {'training_patterns.csv', 'training_distances.csv',
                'heldout_patterns.csv', 'recovery_results.json'}
    if set(manifest.get('output_sha256', {})) != required:
        raise ValueError('recovery output roster differs')
    if canonical(manifest.get('source_configuration')) != canonical(study.base.to_dict()):
        raise ValueError('recovery source configuration differs')
    study.verify_outputs(path, manifest)
    training = _patterns(study, 'recovery-training')
    heldout = _patterns(study, 'recovery-holdout')
    _check_csv(study, path / 'training_patterns.csv', training)
    _check_csv(study, path / 'heldout_patterns.csv', heldout)
    grid = [dict(behavior_rule=rule, autonomy_response=response,
                 review_capacity_per_member_day=capacity)
            for rule in RULES for response in (0., .25, .5) for capacity in (.6, .9, 1.2)]
    conditions = [dict(behavior_rule=rule, autonomy_response=response,
                       review_capacity_per_member_day=.9)
                  for rule in RULES for response in (0., .25)]
    expected_results, distances = [], []
    for case, true in enumerate(conditions):
        observed_train = [row for row in training if row['kind'] == 'observation' and row['case'] == case and row['world'] < 7008]
        observed_holdout = [row for row in training if row['kind'] == 'observation' and row['case'] == case and row['world'] >= 7008]
        if [r['world'] for r in observed_train] != list(range(7000, 7000 + study.spec.recovery_train_worlds)) or [r['world'] for r in observed_holdout] != list(range(7008, 7008 + study.spec.recovery_holdout_worlds)):
            raise ValueError('recovery observation world partition differs')
        scales = {key: max(FLOORS[i], statistics.stdev(row[key] for row in observed_train)) for i, key in enumerate(PATTERNS)}
        scores = []
        for candidate in range(len(grid)):
            predictions = [row for row in training if row['kind'] == 'candidate' and row['candidate'] == candidate]
            if [r['world'] for r in predictions] != list(range(7100, 7100 + study.spec.recovery_train_worlds)):
                raise ValueError('recovery candidate training worlds differ')
            scores.append({'case': case, 'candidate': candidate,
                           'distance': distance(observed_train, predictions, scales)})
        scores.sort(key=lambda row: (row['distance'], row['candidate']))
        distances.extend(scores)
        best = scores[0]
        chosen = grid[best['candidate']]
        predictions = [row for row in heldout if row['kind'] == 'holdout' and row['case'] == case]
        if [r['world'] for r in predictions] != list(range(7108, 7108 + study.spec.recovery_holdout_worlds)) or any(any(row[key] != value for key, value in chosen.items()) for row in predictions):
            raise ValueError('heldout prediction does not match training-only argmin')
        expected_results.append(dict(case=case, true=true, chosen=chosen,
            training_distance=best['distance'], scales=scales,
            feasible_candidates=[row['candidate'] for row in scores if row['distance'] <= best['distance'] + 1],
            observed_train=moments(observed_train), observed_holdout=moments(observed_holdout),
            predicted_holdout=moments(predictions),
            heldout_distance=distance(observed_holdout, predictions, scales),
            exact_grid_parameter_recovery=true == chosen,
            behavior_rule_recovered=true['behavior_rule'] == chosen['behavior_rule']))
    null = [row for row in heldout if row['kind'] == 'null']
    if [(r['world'], r['regime']) for r in null] != [(world, regime) for world in range(7200, 7200 + study.spec.recovery_train_worlds) for regime in ('linear', 'sublinear')]:
        raise ValueError('recovery null paired-world inventory differs')
    _check_csv(study, path / 'training_distances.csv', distances)
    actual_results = study.load_json(path / 'recovery_results.json')
    if manifest.get('case_count') != len(conditions) or canonical(actual_results) != canonical(expected_results):
        raise ValueError('recovery selection/scales/moments/holdout loss differs from raw training-only calculation')
    study.guard()
