"""Candidate full-history two-firm interventions, not an old-study migration.

The original joint terms stay immutable. A separate intervention names only
one of three enumerated actions at the original, completed offer boundary.
No source bypass, target regeneration, employee capsule or history rewrite.
Engineering verification does not imply calibration or formal study acceptance.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import shutil

ARMS = ('no-acquisition', 'preserve-autonomy', 'centralize')
PLAN_SCHEMA = 'joint-full-history-counterfactual-plan-1'
CHECKPOINT_SCHEMA = 'dams-two-firm-joint-counterfactual-checkpoint-1'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_plan(payload):
    """Pure schema/immutable-history check; never read or instantiate a Model."""
    required = {'schema', 'source_sha256', 'parent_checkpoint', 'parent_day', 'history_terms',
                'history_terms_sha256', 'firm_config_sha256', 'firm_state_sha256',
                'parent_records_sha256', 'parent_files', 'arms'}
    if not isinstance(payload, dict) or set(payload) != required or payload['schema'] != PLAN_SCHEMA:
        raise ValueError('counterfactual plan schema differs')
    if payload['arms'] != list(ARMS) or type(payload['parent_day']) is not int or payload['parent_day'] < 1:
        raise ValueError('counterfactual actions/calendar differ')
    if not isinstance(payload['history_terms'], dict) or _digest(payload['history_terms']) != payload['history_terms_sha256']:
        raise ValueError('original history terms digest differs')
    if payload['history_terms'].get('offer_day') != payload['parent_day']:
        raise ValueError('counterfactual is not the original offer boundary')
    for key in ('source_sha256', 'history_terms_sha256', 'parent_records_sha256'):
        if not _sha(payload[key]):raise ValueError('invalid counterfactual digest')
    for key in ('firm_config_sha256', 'firm_state_sha256'):
        if not isinstance(payload[key], dict) or set(payload[key]) != {'buyer', 'target'} or not all(_sha(v) for v in payload[key].values()):
            raise ValueError('complete two-firm binding required')
    files = payload['parent_files']
    if not isinstance(files, dict) or not files or payload['parent_checkpoint'] not in files:
        raise ValueError('complete original parent group required')
    for name, pin in files.items():
        if (not isinstance(name, str) or Path(name).name != name or name in ('.', '..')
                or not isinstance(pin, dict) or set(pin) != {'bytes', 'sha256'}
                or type(pin['bytes']) is not int or pin['bytes'] <= 0 or not _sha(pin['sha256'])):
            raise ValueError('unsafe/incomplete original parent file pin')
    return copy.deepcopy(payload)


@dataclass(frozen=True)
class CounterfactualPlan:
    # A canonical immutable string avoids a frozen dataclass holding mutable maps.
    payload_json: str

    def __post_init__(self):
        value = validate_plan(json.loads(self.payload_json))
        if _canonical(value).decode() != self.payload_json:
            raise ValueError('counterfactual plan must use canonical bytes')

    @property
    def payload(self):return json.loads(self.payload_json)

    @property
    def sha256(self):return hashlib.sha256(self.payload_json.encode()).hexdigest()

    @classmethod
    def from_checkpoint(cls, path):
        """Inspect the complete original pair; no Model construction/advance."""
        from .storage import file_digest, source_hash
        from .longitudinal_model import verify_snapshot
        from .joint_firm import JointTerms, VERSION, verify_cash_records, validate_joint_configs
        from .config import Config
        path = Path(path)
        value = json.loads(path.read_bytes())
        if (value.get('schema') != 'dams-two-firm-joint-checkpoint-1'
                or value.get('model_version') != VERSION or value.get('source_sha256') != source_hash()
                or value.get('status') != 'unoffered' or value.get('offer') is not None
                or value.get('owners') != {'buyer': 'buyer', 'target': 'seller'}
                or set(value.get('firms', {})) != {'buyer', 'target'}):
            raise ValueError('shared parent must be original-source, unoffered and unowned')
        terms = JointTerms(**value['terms'])
        configs={name:Config.from_dict(pin['config']) for name,pin in value['firms'].items()}
        validate_joint_configs(configs['buyer'],configs['target'],terms)
        if value['day'] != terms.offer_day or value['terms_sha256'] != _digest(value['terms']):
            raise ValueError('cannot move or rewrite the original offer boundary/terms')
        if len(value['records']) != value['day']:raise ValueError('shared parent history is incomplete')
        verify_cash_records(value['records'])
        paths = {path.name: path}
        configs, states = {}, {}
        for company, pin in value['firms'].items():
            if Path(pin['file']).name != pin['file'] or pin['day'] != value['day']:
                raise ValueError('shared company snapshot path/calendar differs')
            original = path.parent/pin['file']
            if file_digest(original) != pin['sha256']:raise ValueError('shared company snapshot pin differs')
            envelope, database = verify_snapshot(original, expected_source_sha256=value['source_sha256'])
            if (envelope['state'].get('joint_control') is not None or envelope['state']['day'] != value['day']
                    or envelope['state_semantic_sha256'] != pin['state_semantic_sha256']
                    or _canonical(envelope['state']['config']) != _canonical(pin['config'])):
                raise ValueError('target was already controlled or parent state/config differs')
            paths[original.name] = original; paths[database.name] = database
            configs[company] = _digest(pin['config']); states[company] = pin['state_semantic_sha256']
        files = {name: {'bytes': p.stat().st_size, 'sha256': file_digest(p)} for name, p in paths.items()}
        payload = {'schema': PLAN_SCHEMA, 'source_sha256': value['source_sha256'],
                   'parent_checkpoint': path.name, 'parent_day': value['day'],
                   'history_terms': value['terms'], 'history_terms_sha256': value['terms_sha256'],
                   'firm_config_sha256': configs, 'firm_state_sha256': states,
                   'parent_records_sha256': _digest(value['records']), 'parent_files': files, 'arms': list(ARMS)}
        return cls(_canonical(payload).decode())


def _copy_group(source, destination, files):
    from .storage import file_digest
    destination.mkdir(parents=True, exist_ok=False)
    for name, pin in files.items():
        original, target = source/name, destination/name
        if original.is_symlink() or original.stat().st_size != pin['bytes'] or file_digest(original) != pin['sha256']:
            raise ValueError('original lineage file changed before copy')
        with original.open('rb') as src, target.open('xb') as dst:shutil.copyfileobj(src, dst, 1048576)
        if file_digest(target) != pin['sha256']:raise ValueError('lineage copy differs')


def fork_counterfactual(path, root, *, plan, arm):
    """Restore the same complete pair, then bind one separately named action."""
    from .joint_firm import JointRun
    if type(plan) is not CounterfactualPlan or arm not in ARMS:
        raise ValueError('typed fixed plan and enumerated intervention required')
    if CounterfactualPlan.from_checkpoint(path) != plan:
        raise ValueError('original parent/source/terms differ from the fixed plan')
    run = JointRun.restore(path, root)  # exact original terms; never pass replacement terms
    try:
        _copy_group(Path(path).parent, run.root/'lineage-parent', plan.payload['parent_files'])
        run.counterfactual = {'schema': PLAN_SCHEMA, 'arm': arm, 'plan': plan.payload,
                              'plan_sha256': plan.sha256, 'governance_transition': None}
        run._counterfactual_expected = (plan.sha256, arm)
        validate_lineage(run)
        return run
    except BaseException:
        run.close();raise


def integration_mode(run):
    metadata = getattr(run, 'counterfactual', None)
    return metadata['arm'] if metadata is not None and metadata['arm'] != 'no-acquisition' else run.terms.integration_mode


def no_acquisition(run):
    return getattr(run, 'counterfactual', {}).get('arm') == 'no-acquisition'


def check_intervention_binding(run):
    """Cheap pre-step authority check; never re-read a full journal per day."""
    from dataclasses import asdict
    meta = getattr(run, 'counterfactual', None)
    if meta is None:return
    if (getattr(run, '_counterfactual_expected', None) != (meta['plan_sha256'], meta['arm'])
            or _digest(meta['plan']) != meta['plan_sha256']
            or _digest(asdict(run.terms)) != meta['plan']['history_terms_sha256']):
        raise ValueError('counterfactual intervention/history binding changed before step')


def prepare_centralization(run):
    """Use the existing paid adoption path, never a free policy override.

    Only an unadopted target can use the existing Model.fork contract. Re-adoption
    of an already adopted firm to a different policy is explicitly unsupported,
    rather than erased or made free. Identical actual policies need no conversion.
    """
    from .storage import file_digest
    target, buyer = run.units['target'], run.units['buyer']
    desired = {buyer.policy(g) for g in buyer.guild_alias}
    if len(desired) != 1:raise ValueError('centralization requires one declared parent policy')
    desired = next(iter(desired))
    metadata = run.counterfactual
    if metadata['governance_transition'] is not None:return
    if all(target.policy(g) == desired for g in target.guild_alias):return
    if any(day is not None for day in target.adoption.values()):
        raise ValueError('different-policy re-adoption mechanism is not implemented; original state retained')
    old = target.config
    if min(old.longitudinal.migration_resource_units_per_member,
           old.longitudinal.training_hours_per_workday,
           old.longitudinal.dual_run_resource_units_per_member_workday) <= 0:
        raise ValueError('new centralization requires explicit nonzero migration/training/dual-run costs')
    child = replace(old, regime=desired[0], backend=desired[1],
                    longitudinal=replace(old.longitudinal, adoption_mode='fixed', adoption_day=run.day))
    checkpoint = run.root/'governance-parent.json'
    target.write_checkpoint(checkpoint)
    state_digest = target.semantic_digest()
    forked = target.fork(child, storage_dir=run.root/f'target-governance-day-{run.day:06d}')
    try:
        if forked.ledger.semantic_digest() != target.ledger.semantic_digest():
            raise ValueError('centralization fork changed original ledger')
        transition = {'requested_day': run.day, 'before_config': old.to_dict(), 'after_config': child.to_dict(),
                      'parent_state_semantic_sha256': state_digest,
                      'parent_checkpoint': checkpoint.name, 'parent_checkpoint_sha256': file_digest(checkpoint),
                      'policy': list(desired), 'cost_path': 'existing _adopt / training / dual-run',
                      'branch_origin': copy.deepcopy(forked.branch_origin)}
        run.units['target'] = forked
        metadata['governance_transition'] = transition
    except BaseException:
        forked.ledger.close();raise
    target.ledger.close()  # retain its original closed files; never delete them


def validate_lineage(run):
    """Bind immutable prehistory, independent intervention and original files."""
    from dataclasses import asdict
    from .storage import file_digest, source_hash
    meta = getattr(run, 'counterfactual', None)
    if meta is None:return
    check_intervention_binding(run)
    if (set(meta) != {'schema', 'arm', 'plan', 'plan_sha256', 'governance_transition'}
            or meta['schema'] != PLAN_SCHEMA or meta['arm'] not in ARMS):
        raise ValueError('counterfactual lineage schema/action differs')
    plan = CounterfactualPlan(_canonical(meta['plan']).decode()); payload = plan.payload
    if (plan.sha256 != meta['plan_sha256'] or payload['source_sha256'] != source_hash()
            or _canonical(asdict(run.terms)) != _canonical(payload['history_terms'])
            or run.day < payload['parent_day']):
        raise ValueError('counterfactual source/history terms/calendar changed')
    for name, pin in payload['parent_files'].items():
        p = run.root/'lineage-parent'/name
        if p.is_symlink() or p.stat().st_size != pin['bytes'] or file_digest(p) != pin['sha256']:
            raise ValueError('original complete two-firm lineage file differs')
    parent = json.loads((run.root/'lineage-parent'/payload['parent_checkpoint']).read_bytes())
    if _canonical(run.records[:payload['parent_day']]) != _canonical(parent['records']):
        raise ValueError('counterfactual rewrites shared original history')
    if no_acquisition(run) and run.owners != {'buyer': 'buyer', 'target': 'seller'}:
        raise ValueError('no-acquisition intervention cannot acquire/control target')
    transition = meta['governance_transition']
    for company, model in run._all().items():
        expected = transition['after_config'] if company == 'target' and transition else parent['firms'][company]['config']
        if _canonical(model.config.to_dict()) != _canonical(expected):
            raise ValueError('counterfactual changed a company history/config field')
        if run.day == payload['parent_day'] and model.semantic_digest() != payload['firm_state_sha256'][company]:
            raise ValueError('counterfactual does not start from the exact complete parent state')
    if transition is not None:
        if meta['arm'] != 'centralize' or not payload['parent_day'] <= transition['requested_day'] <= run.day:
            raise ValueError('governance transition outside authorized intervention')
        original = run.root/transition['parent_checkpoint']
        if file_digest(original) != transition['parent_checkpoint_sha256']:
            raise ValueError('original governance transition checkpoint differs')
        from .longitudinal_model import verify_snapshot
        envelope, _ = verify_snapshot(original, expected_source_sha256=payload['source_sha256'])
        expected_origin = {'parent_source_sha256': payload['source_sha256'],
                           'parent_config_sha256': _digest(transition['before_config']),
                           'parent_day': transition['requested_day'],
                           'parent_state_semantic_sha256': envelope['state_semantic_sha256']}
        if (envelope['state']['day'] != transition['requested_day']
                or _canonical(envelope['state']['config']) != _canonical(transition['before_config'])
                or envelope['state_semantic_sha256'] != transition['parent_state_semantic_sha256']
                or transition['branch_origin'] != expected_origin):
            raise ValueError('governance transition is disconnected from its full original state')
        if run.units['target'].branch_origin != transition['branch_origin']:
            raise ValueError('governance fork parent provenance differs')
        if _canonical(transition['before_config']) != _canonical(parent['firms']['target']['config']):
            raise ValueError('governance conversion rewrote original target assumptions')
        if (transition['after_config']['longitudinal']['adoption_mode'] != 'fixed'
                or transition['after_config']['longitudinal']['adoption_day'] != transition['requested_day']
                or transition['policy'] != [transition['after_config']['regime'], transition['after_config']['backend']]):
            raise ValueError('governance conversion policy/calendar differs')
        after = copy.deepcopy(transition['after_config']); before = transition['before_config']
        after['regime'], after['backend'] = before['regime'], before['backend']
        after['longitudinal']['adoption_mode'] = before['longitudinal']['adoption_mode']
        after['longitudinal']['adoption_day'] = before['longitudinal']['adoption_day']
        if _canonical(after) != _canonical(before):
            raise ValueError('governance conversion altered a non-intervention assumption')
    # Both original logical journal prefixes must survive, including compressed
    # chunks. Histories and metadata flags alone cannot establish this property.
    import sqlite3
    from .longitudinal_storage import ExactLedger
    for company, model in run._all().items():
        original = run.root/'lineage-parent'/parent['firms'][company]['file']
        envelope = json.loads(original.read_bytes()); name = envelope['ledger']['file']
        if Path(name).name != name:raise ValueError('unsafe original ledger reference')
        connection = sqlite3.connect((original.parent/name).resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
        ledger = ExactLedger.__new__(ExactLedger); ledger.db = connection
        earlier, current = ledger.rows('journal'), model.ledger.rows('journal')
        try:
            for row in earlier:
                if next(current, None) != row:raise ValueError('original company journal prefix changed')
        finally:
            earlier.close(); current.close(); connection.close()


def snapshot_metadata(run, path):
    meta = getattr(run, 'counterfactual', None)
    if meta is None:return None
    # New branch groups are deliberately self-contained. Do not silently emit a
    # detached JSON in another directory without its immutable lineage sidecars.
    if Path(path).parent.resolve() != run.root.resolve():
        raise ValueError('counterfactual checkpoint must remain with complete lineage group')
    validate_lineage(run)
    return copy.deepcopy(meta)


def restore_metadata(run, value, path):
    meta = value.get('counterfactual')
    if meta is None:
        if value['schema'] == CHECKPOINT_SCHEMA:raise ValueError('counterfactual checkpoint lacks lineage')
        return
    if value['schema'] != CHECKPOINT_SCHEMA:raise ValueError('legacy checkpoint cannot acquire new intervention authority')
    payload = validate_plan(meta['plan'])
    _copy_group(Path(path).parent/'lineage-parent', run.root/'lineage-parent', payload['parent_files'])
    transition = meta['governance_transition']
    if transition is not None:
        name = transition['parent_checkpoint']
        if Path(name).name != name:raise ValueError('unsafe governance lineage path')
        original = Path(path).parent/name
        envelope = json.loads(original.read_bytes())
        name = envelope['ledger']['file']
        if Path(name).name != name:raise ValueError('unsafe governance ledger path')
        from .storage import file_digest
        files = {p.name: {'bytes': p.stat().st_size, 'sha256': file_digest(p)} for p in
                 (original, original.parent/name)}
        for name, pin in files.items():
            if Path(name).name != name:raise ValueError('unsafe governance lineage reference')
            shutil.copyfile(Path(path).parent/name, run.root/name)
    run.counterfactual = copy.deepcopy(meta)
    validate_lineage(run)


def configuration_history(run, company):
    """Original per-firm config segment for the independent daily raw validator.

    The requested conversion boundary is not a claim that cash was sufficient:
    actual per-guild adoption dates and payments remain original journal events.
    """
    if company not in ('buyer', 'target'):raise ValueError('unknown company namespace')
    transition = getattr(run, 'counterfactual', {}).get('governance_transition')
    if company != 'target' or transition is None:return ()
    from .config import Config
    return ((transition['requested_day'], Config.from_dict(transition['before_config'])),)


def ownership_scoped_totals(records, *, since_day=0):
    """Pure additive flows: corporate ownership mask versus fixed two-firm system.

    Ratios/means/concentration are not added; denominators remain separate raw
    exposure quantities. Consideration is a seller transfer, separately named.
    """
    if type(since_day) is not int or since_day < 0 or since_day > len(records):
        raise ValueError('flow interval must be an observed completed-day boundary')
    keys = ('revenue_resource_units', 'operating_resource_units', 'migration_resource_units',
            'joint_provider_resource_units', 'joint_consideration_resource_units',
            'active_member_calendar_days', 'available_member_work_hours', 'produced_work_units')
    out = {scope: {key: 0. for key in keys} for scope in ('buyer-corporate-group', 'two-firm-system')}
    previous={}
    for day, row in enumerate(records):
        if (row['day'] != day or set(row['firms']) != {'buyer', 'target'}
                or set(row['owners']) != {'buyer', 'target'} or row['owners']['buyer'] != 'buyer'
                or row['owners']['target'] not in ('buyer', 'seller')):
            raise ValueError('scoped flows require complete synchronized daily roster')
        for company, firm in row['firms'].items():
            for key in keys:
                after, before = firm['metrics_after'].get(key, 0.), firm['metrics_before'].get(key, 0.)
                if any(type(v) not in (int, float) or not math.isfinite(v) or v<0 for v in (before, after)) or after < before:
                    raise ValueError('scoped flow requires nonnegative finite cumulative counters')
                if day==0 and before!=0:raise ValueError('scoped flow initial cumulative counters must be zero')
                if day and before!=previous[company][key]:raise ValueError('scoped flow previous cumulative counter differs')
                if day < since_day:continue
                amount = after-before
                out['two-firm-system'][key] += amount
                if row['owners'][company] == 'buyer':out['buyer-corporate-group'][key] += amount
        previous={company:{key:firm['metrics_after'].get(key,0.) for key in keys} for company,firm in row['firms'].items()}
    return out
