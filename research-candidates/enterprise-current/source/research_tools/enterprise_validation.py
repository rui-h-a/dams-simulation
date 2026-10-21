"""Frozen external-data roles and multi-objective calibration for candidate ABMs.

Prediction packets must come from complete independently checked executions.
This module verifies packet provenance bindings, not their truth. Report parsing
currently supports one aggregate; no enterprise-level empirical fit is claimed.
Public targets exposed during development are retrospective diagnostics.
Retained R10 narrow engineering evidence has a separate observable packet and
cannot be promoted to calibration/validation prediction packets by this adapter.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import re

from .enterprise_observations import DataLabel, validate_split, canonical, parse_employee_total

SHA = re.compile(r'[0-9a-f]{64}')
ROLES = {'calibration':'fit', 'validation':'evaluate_frozen', 'diagnostic':'inspect',
         'training':'design', 'holdout_candidate':'evaluate_locked'}

V2_MODEL = 'longitudinal-enterprise-causal-2'
R7_MODEL = 'longitudinal-enterprise-integrated-7-networks-tasks-1'
R8_MODEL = 'longitudinal-enterprise-joint-integration-8'
R10_MODEL = 'longitudinal-enterprise-task-structure-10'
OPERATING_COSTS = ('operating_resource_units', 'recruitment_resource_units',
                   'migration_resource_units', 'infrastructure_setup_resource_units')


@dataclass(frozen=True)
class ObservableEvidenceFile:
    path: str
    sha256: str


@dataclass(frozen=True)
class R10EngineeringEvidence:
    """Retained native engineering metadata, never a complete raw/empirical gate.

    Caller-pinned original bytes are checked against one another. These pins
    do not authenticate the issuer or replace the independent raw validator.
    Outer joint execution is still R8; its schema-two constituents are R10.
    """
    arm: str
    profile: ObservableEvidenceFile
    scenario: ObservableEvidenceFile
    joint_result: ObservableEvidenceFile
    execution_receipt: ObservableEvidenceFile
    supervisor_terminal: ObservableEvidenceFile
    narrow_review_receipt: ObservableEvidenceFile
    mechanism_review: ObservableEvidenceFile


@dataclass(frozen=True)
class EngineeringObservablePacket:
    model_version: str
    outer_joint_model_version: str
    model_source_sha256: str
    arms: tuple[dict, ...]
    evidence: tuple[R10EngineeringEvidence, ...]
    evidence_class: str = field(default='existing-conditional-synthetic-engineering-observables',init=False)
    formal_worlds_added: int = field(default=0,init=False)
    full_raw_validation_performed: bool = field(default=False,init=False)
    complete_independent_model_validation: bool = field(default=False,init=False)
    empirical_calibration_completed: bool = field(default=False,init=False)
    historical_blindness_certified: bool = field(default=False,init=False)
    cross_enterprise_holdout_completed: bool = field(default=False,init=False)
    content_sha256: str = field(init=False)

    def __post_init__(self):
        require(self.model_version==R10_MODEL and self.outer_joint_model_version==R8_MODEL
                and _sha(self.model_source_sha256),'engineering packet model/source identity differs')
        object.__setattr__(self,'content_sha256','')
        body=asdict(self);body.pop('content_sha256')
        object.__setattr__(self,'content_sha256',digest(body))

    def to_dict(self):
        """Checked export; frozen dataclasses alone do not freeze nested maps."""
        return export_r10_engineering_packet(self)


@dataclass(frozen=True)
class EntityPredictionBinding:
    """Bindings supplied by a caller after its existing complete raw gate.

    Summary/config digests cover canonical mappings, not raw file bytes. The
    gate receipt pin references the caller's native evidence. This type does
    not execute that validator, authenticate a receipt or certify its truth.
    R10 is a separate engineering projection requiring actual referenced narrow
    receipts; its binding does not assert a complete independent raw gate.
    """
    company: str
    model_version: str
    model_source_sha256: str
    config_sha256: str
    summary_sha256: str
    gate_receipt_sha256: str
    seed: int
    world: int
    expected_days: int
    completed_days: int
    engineering_evidence: R10EngineeringEvidence | None = None


@dataclass(frozen=True)
class CompletedPredictionBinding:
    """Explicit complete-calendar and observation-scope provenance only.

    This intentionally has no caller-controlled PASS flag. Valid bindings are
    not independent raw verification, calibration or external validation.
    The joint world seed is the buyer config seed; the independently evolving
    target's seed/world/config remain separately bound in ``entities``.
    """
    model_version: str
    observation_scope: str
    model_source_sha256: str
    config_sha256: str
    summary_sha256: str
    gate_receipt_sha256: str
    world_seed: int
    expected_days: int
    entities: tuple[EntityPredictionBinding, ...]


@dataclass(frozen=True)
class ExtractedObservables:
    values: dict
    units: dict
    observation_scope: str
    complete_days: int
    full_raw_validation_performed: bool = False
    empirical_calibration_completed: bool = False
    historical_blindness_certified: bool = False
    cross_enterprise_holdout_completed: bool = False


def require(ok, message):
    if not ok: raise ValueError(message)


def digest(value): return hashlib.sha256(canonical(value)).hexdigest()


def seal(path, value):
    value=json.loads(canonical(value));value['sha256']=digest(value)
    with Path(path).open('xb') as f:
        f.write(canonical(value)+b'\n');f.flush()
        import os
        os.fsync(f.fileno())
    return value


def load(path, schema):
    value=json.loads(Path(path).read_bytes());sha=value.pop('sha256')
    require(SHA.fullmatch(sha or '') and digest(value)==sha,'frozen packet checksum drift')
    require(value.get('schema')==schema,'wrong packet schema')
    return {**value,'sha256':sha}


def finite(value): return type(value) in (int,float) and math.isfinite(value)

def checked_packet(value, schema):
    require(type(value) is dict and value.get('schema')==schema,'wrong frozen packet schema')
    require(SHA.fullmatch(value.get('sha256','')) and digest({k:v for k,v in value.items() if k!='sha256'})==value['sha256'],'frozen packet edited after sealing')
    return value


def freeze_protocol(path, *, labels, metrics, parameter_grid, world_seeds, model_source_sha256,
                    identification_tolerance=1e-8, score_tie_tolerance=1e-10):
    """Create once before any new target read; seeds/metrics/objectives never tuned.

    metric specs: {name: {unit, scale, weight}}. Parameter grids specify candidate
    values, not identified real coefficients. All values are checked predictions
    on the same fixed independent worlds; calibration excludes DAMS superiority.
    """
    labels=validate_split(labels)
    require(SHA.fullmatch(model_source_sha256 or ''),'model source SHA required')
    require(metrics and type(metrics) is dict,'predeclared metrics required')
    for name,m in metrics.items():
        require(type(name) is str and name and set(m)=={'unit','scale','weight'},'invalid metric schema')
        require(type(m['unit']) is str and m['unit'] and finite(m['scale']) and m['scale']>0 and finite(m['weight']) and m['weight']>0,'invalid metric scale/weight')
    require(parameter_grid and type(parameter_grid) is dict,'finite parameter grid required')
    for name,values in parameter_grid.items():
        require(type(name) is str and name and type(values) in (list,tuple) and len(values)>=2,'parameter grid needs at least two values per dimension')
        require(all(finite(v) for v in values) and len(set(values))==len(values),'nonfinite/duplicate parameter grid')
    require(type(world_seeds) in (list,tuple) and world_seeds and all(type(v) is int and v>=0 for v in world_seeds) and len(set(world_seeds))==len(world_seeds),'fixed unique world seeds required')
    require(finite(identification_tolerance) and identification_tolerance>0 and finite(score_tie_tolerance) and score_tie_tolerance>=0,'invalid fixed tolerances')
    names=sorted(parameter_grid)
    points=[dict(zip(names,v)) for v in itertools.product(*(parameter_grid[k] for k in names))]
    require(len(points)<=4096,'bounded candidate grid exceeded')
    return seal(path,{'schema':'enterprise-calibration-protocol-v1','frozen_utc':datetime.now(timezone.utc).isoformat(),
        'labels':[asdict(v) for v in labels],'metrics':metrics,'parameter_grid':parameter_grid,
        'world_seeds':list(world_seeds),'model_source_sha256':model_source_sha256,
        'identification_tolerance':identification_tolerance,'score_tie_tolerance':score_tie_tolerance,
        'parameter_points':points,'target_values_included':False,
        'objective':'weighted standardized squared prediction error on calibration records only',
        'holdout_access':'sealed; independent historical-exposure stewardship not implemented',
        'blindness_status':'metadata freeze does not establish historical blindness'})


def labels_of(protocol):
    checked_packet(protocol,'enterprise-calibration-protocol-v1')
    labels=validate_split(DataLabel(**{**v,'countries':tuple(v['countries'])}) for v in protocol['labels'])
    return {v.key:v for v in labels}


def authorize_target(protocol, key, purpose, *, frozen_prediction=None):
    labels=labels_of(protocol);require(key in labels,'target absent from frozen split')
    label=labels[key];require(ROLES[label.role]==purpose,'target purpose conflicts with frozen role')
    require(label.role!='holdout_candidate','holdout sealed pending independent stewardship')
    if label.role=='validation':
        require(frozen_prediction is not None,'freeze validation predictions before target access')
        checked_packet(frozen_prediction,'enterprise-frozen-validation-predictions-v1')
        require(frozen_prediction is not None and frozen_prediction['protocol_sha256']==protocol['sha256'] and frozen_prediction['model_source_sha256']==protocol['model_source_sha256'],'freeze checked validation predictions before target access')
    return label


def read_employee_target(raw_path, protocol, key, purpose, source_url, *, frozen_prediction=None):
    # This authorization executes BEFORE opening target bytes.
    label=authorize_target(protocol,key,purpose,frozen_prediction=frozen_prediction)
    require(label.organization=='Amazon','only the audited Amazon aggregate parser is available')
    pattern=rf'https://www\.sec\.gov/Archives/edgar/data/1018724/[0-9]+/amzn-{label.period.replace("-", "")}\.htm'
    require(re.fullmatch(pattern,source_url),'primary source URL/issuer/period mismatch')
    parsed=parse_employee_total(Path(raw_path).read_bytes(),label.period)
    name=parsed['metric'];require(name in protocol['metrics'] and protocol['metrics'][name]['unit']==parsed['unit'],'observed metric not predeclared with matching unit')
    return {'key':key,'purpose':purpose,'protocol_sha256':protocol['sha256'],'source_url':source_url,
            'source_sha256':parsed['source_sha256'],'evidence_class':parsed['evidence_class'],
            'declared_exposure':label.exposure,'values':{name:parsed['value']},'parser_evidence':parsed}


def _sha(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def _positive_int(value):
    return type(value) is int and value > 0


def _amount(value, name, *, nonnegative=True):
    require(finite(value) and (not nonnegative or value >= 0), 'invalid model amount: '+name)
    return value


def _total(values):
    try:
        total=math.fsum(values)
    except OverflowError:
        raise ValueError('model amount sum overflows') from None
    require(finite(total), 'nonfinite model amount total')
    return total


def _margin(revenue, cost):
    if not revenue:return None
    value=(revenue-cost)/revenue
    require(finite(value), 'undefined or nonfinite operating margin')
    return value


def _single_values(summary):
    require(type(summary) is dict and type(summary.get('enterprise_operating_state_final')) is dict,
            'actual operating summary required')
    people=summary.get('active_members_final')
    require(type(people) is int and people >= 0, 'actual integer employee count required')
    revenue=_amount(summary['enterprise_operating_state_final'].get('delivered_revenue'), 'delivered revenue')
    cost=_total(_amount(summary.get(k,0.),k) for k in OPERATING_COSTS)
    return {'reported_full_time_and_part_time_employees':people,
            'operating_margin_model_units':_margin(revenue,cost),
            'settled_revenue_model_resource_units':revenue}


def _evidence_json(ref):
    """Anchored no-follow read with byte pin and duplicate/nonfinite rejection."""
    import os
    import stat
    require(type(ref) is ObservableEvidenceFile and _sha(ref.sha256)
            and type(ref.path) is str and Path(ref.path).is_absolute(),'typed original evidence file required')
    path=Path(ref.path);before=path.lstat()
    def native(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=16*1024**2,
            'unsafe/unbounded original observable evidence')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        require(native(os.fstat(fd))==native(before),'evidence open identity drift')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(16*1024**2+1)
        require(native(os.fstat(fd))==native(before) and native(path.lstat())==native(before)
                and hashlib.sha256(raw).hexdigest()==ref.sha256,'original observable evidence bytes drift')
    finally:os.close(fd)
    def pairs(items):
        value={}
        for k,v in items:
            require(k not in value,'duplicate original observable JSON key');value[k]=v
        return value
    value=json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite original observable JSON')))
    require(type(value) is dict,'original observable evidence must be a mapping')
    return value


def _r10_evidence(evidence):
    require(type(evidence) is R10EngineeringEvidence and evidence.arm in ('hierarchy','dams'),
            'R10 requires retained typed engineering evidence; a hex-only raw-gate pin is insufficient')
    values={name:_evidence_json(getattr(evidence,name)) for name in
            ('profile','scenario','joint_result','execution_receipt','supervisor_terminal','narrow_review_receipt','mechanism_review')}
    profile=values['profile'];scenario=values['scenario'];joint=values['joint_result']
    root=values['execution_receipt'];narrow=values['narrow_review_receipt'];review=values['mechanism_review']
    terminal=values['supervisor_terminal']
    from dams_sim.organization_tasks import STRUCTURE_VERSION
    from dams_sim.storage import source_hash
    source=source_hash()
    require(STRUCTURE_VERSION==R10_MODEL and profile.get('schema')=='existing-r10-exact-preregistration-proposal-1'
            and profile.get('model_source',{}).get('runtime_core_33_sha256')==source,
            'R10 original source/profile differs')
    require(root.get('status')=='ONE_EXISTING_R10_FULL_PERIOD_ENGINEERING_PAIR_AND_NATIVE_RESTORE_COMPLETED'
            and root.get('source_sha256')==source and root.get('profile_sha256')==evidence.profile.sha256
            and type(root.get('formal_worlds_added')) is int and root['formal_worlds_added']==0
            and root.get('empirical_calibration_or_OOS') is False
            and root.get('independent_full_model_verification') is False,
            'original complete engineering execution receipt required')
    require(narrow.get('status')=='EXISTING_FULL_PERIOD_ENGINEERING_MECHANISM_SUBSET_REVIEW_PASS'
            and narrow.get('source_SHA')==source and narrow.get('profile_SHA')==evidence.profile.sha256
            and type(narrow.get('formal_worlds_added')) is int and narrow['formal_worlds_added']==0
            and narrow.get('empirical_calibration_or_OOS') is False
            and narrow.get('complete_independent_model_validation') is False,
            'original narrow review scope/source differs; no complete independent gate is claimed')
    require(review.get('schema')=='DAMS-existing-R10-full-period-independent-mechanism-review-1'
            and review.get('status')=='NARROW_EXISTING_MECHANISM_AND_RAW_ACCOUNTING_SUBSETS_PASS_WITH_EXPLICIT_UNEXERCISED_SCOPE'
            and review.get('source_core_sha256')==source and review.get('profile_sha256')==evidence.profile.sha256
            and review.get('Root_terminal_sha256')==evidence.supervisor_terminal.sha256
            and type(review.get('formal_worlds_added')) is int and review['formal_worlds_added']==0
            and review.get('empirical_calibration_or_OOS') is False,
            'original referenced mechanism review differs')
    require(type(terminal.get('exit_code')) is int and terminal['exit_code']==0
            and terminal.get('producer_reaped') is True and terminal.get('owned_pgid_absent') is True
            and terminal.get('result_present') is True and type(terminal.get('formal_worlds_added')) is int
            and terminal['formal_worlds_added']==0 and terminal.get('whole_goal_complete') is False,
            'original closed engineering supervisor terminal differs')
    links=narrow.get('evidence_files')
    require(type(links) is list and any(v.get('path')==Path(evidence.mechanism_review.path).name
            and v.get('sha256')==evidence.mechanism_review.sha256 for v in links),
            'narrow receipt does not bind the actual mechanism review bytes')
    arms=profile.get('arms');completed=root.get('arms')
    require(type(arms) is list and len(arms)==2 and {v.get('name') for v in arms}=={'hierarchy','dams'}
            and type(completed) is list and len(completed)==2 and {v.get('arm') for v in completed}=={'hierarchy','dams'},
            'original engineering two-arm roster differs')
    arm=next(v for v in arms if v['name']==evidence.arm)
    finished=next(v for v in completed if v['arm']==evidence.arm)
    config={'buyer':scenario.get('buyer_config'),'target':scenario.get('target_config'),'terms':scenario.get('terms')}
    expected={'buyer':arm.get('buyer_config'),'target':arm.get('target_config'),'terms':arm.get('joint_terms')}
    horizon=profile.get('calendar',{}).get('full_horizon_days')
    require(_positive_int(horizon) and canonical(config)==canonical(expected)
            and scenario.get('source_sha256')==source and scenario.get('engineering_only') is True
            and type(scenario.get('formal_worlds_added')) is int and scenario['formal_worlds_added']==0
            and scenario.get('calibration_OOS') is False,
            'predeclared engineering scenario/config differs')
    require(finished.get('status')=='ACTUAL_TWO_EVOLVED_FIRM_HISTORY_ENGINEERING_COMPLETED'
            and finished.get('complete_calendar_days')==horizon
            and finished.get('native_full_history_restore_and_joint_accounting_pass') is True
            and canonical(finished.get('summaries'))==canonical(joint.get('firm_summaries'))
            and root.get('full_calendar_days')==horizon
            and type(joint.get('formal_worlds_added')) is int and joint['formal_worlds_added']==0
            and joint.get('calibration_OOS') is False,
            'retained joint summaries/full-horizon/native-restore receipt differs')
    raw=review.get('full_original_artifact_pins')
    require(type(raw) is list and raw and len({v.get('path') for v in raw})==len(raw),
            'original raw metadata pin roster missing/duplicate')
    for name,ref in (('predeclared-scenario.json',evidence.scenario),('joint-result.json',evidence.joint_result)):
        matches=[v for v in raw if v.get('path')==evidence.arm+'/'+name]
        require(len(matches)==1 and matches[0].get('sha256')==ref.sha256,
                'original mechanism receipt does not bind projected raw bytes')
    return values,config,source,horizon


def _r10_state_shape(summary, config):
    from dams_sim.config import Config
    validated=Config.from_dict(config).validate()
    require(canonical(validated.to_dict())==canonical(config),'R10 actual full Config differs from canonical schema')
    tasks=config.get('organization_tasks');state=summary.get('organization_tasks_state_final')
    require(type(tasks) is dict and type(tasks.get('schema_version')) is int and tasks['schema_version']==2
            and type(state) is dict and set(state)=={'schema_version','completed_days','stocks','generated',
                'consumed','last_plan','review_remaining','structure'}
            and type(state['schema_version']) is int and state['schema_version']==2
            and type(state['completed_days']) is int and state['completed_days']==config['days'],
            'R10 requires exact complete schema-two task state/config')
    structure=state['structure'];count=summary.get('task_department_count_final')
    require(type(structure) is dict and _positive_int(count)
            and type(structure.get('department_count')) is int and structure['department_count']==count,
            'R10 final task structure shape differs')
    expected={str(v) for v in range(count)}
    for name in ('stocks','generated','consumed'):
        require(type(state[name]) is dict and set(state[name])==expected
                and all(finite(v) and v>=0 for v in state[name].values()),'R10 task stock map shape differs')
    # Dynamic final task departments can exceed initial departments. The
    # installed enterprise departments and task-layer departments are distinct.
    require(type(structure.get('pending')) is list and type(structure.get('history')) is list
            and type(state['review_remaining']) is dict and type(state['last_plan']) is dict,
            'R10 final task structure metadata missing')


def _entity(summary, config, binding, source, horizon, company):
    require(type(binding) is EntityPredictionBinding and binding.company==company,
            'typed entity scope binding required')
    require(all(_sha(getattr(binding,k)) for k in ('model_source_sha256','config_sha256',
            'summary_sha256','gate_receipt_sha256')), 'entity provenance pins required')
    require(type(config) is dict and type(summary) is dict, 'canonical actual entity mappings required')
    require(binding.model_source_sha256==source and digest(config)==binding.config_sha256
            and digest(summary)==binding.summary_sha256, 'entity source/config/summary drift')
    require(binding.model_version in (V2_MODEL,R7_MODEL,R8_MODEL,R10_MODEL)
            and summary.get('model_version')==binding.model_version, 'unsupported entity model version')
    tasks=config.get('organization_tasks')
    if tasks is not None:
        require(type(tasks) is dict and type(tasks.get('schema_version')) is int
                and tasks['schema_version'] in (1,2),'unknown organization task schema')
        if tasks['schema_version']==2:
            require(binding.model_version==R10_MODEL,'schema-two tasks cannot be relabeled as an older model')
        else:
            require(binding.model_version in (R7_MODEL,R8_MODEL),'schema-one task model version differs')
        task_state=summary.get('organization_tasks_state_final')
        require(type(task_state) is dict and type(task_state.get('schema_version')) is int
                and task_state['schema_version']==tasks['schema_version'], 'task state/config schema differs')
    if binding.model_version==R10_MODEL:
        values,actual,original_source,original_horizon=_r10_evidence(binding.engineering_evidence)
        require(company in ('buyer','target') and canonical(config)==canonical(actual[company])
                and canonical(summary)==canonical(values['joint_result']['firm_summaries'][company])
                and source==original_source and horizon==original_horizon
                and binding.gate_receipt_sha256==binding.engineering_evidence.narrow_review_receipt.sha256,
                'R10 entity summary/config/source/receipt does not bind its retained engineering evidence')
        _r10_state_shape(summary,config)
    require(_positive_int(horizon) and type(config.get('days')) is int and config['days']==horizon
            and _positive_int(binding.expected_days) and binding.expected_days==horizon
            and _positive_int(binding.completed_days) and binding.completed_days==horizon
            and type(summary.get('days_completed')) is int and summary['days_completed']==horizon,
            'incomplete entity calendar')
    require(type(config.get('seed')) is int and config['seed']>=0 and type(binding.seed) is int
            and binding.seed==config['seed'] and type(config.get('world')) is int and config['world']>=0
            and type(binding.world) is int and binding.world==config['world']
            and type(summary.get('world')) is int and summary['world']==config['world'], 'entity seed/world drift')
    # Native summaries do not currently expose seed. Bind the actual config and
    # native caller's seed instead; reject a conflicting seed when one is present.
    require('seed' not in summary or type(summary['seed']) is int and summary['seed']==config['seed'],
            'summary seed differs from actual config')
    require(_positive_int(config.get('n')) and type(summary.get('n')) is int
            and summary['n']==config['n'], 'summary population differs from actual config')
    longitudinal=config.get('longitudinal')
    require(type(longitudinal) is dict, 'longitudinal calendar required')
    try:
        start=date.fromisoformat(longitudinal['calendar_start'])
    except (KeyError,TypeError,ValueError):
        raise ValueError('invalid actual Gregorian calendar') from None
    require(summary.get('calendar_start')==start.isoformat()
            and summary.get('calendar_end_exclusive')==(start+timedelta(days=horizon)).isoformat(),
            'summary Gregorian calendar differs')
    for field in ('closure_day','suspension_day'):
        value=summary.get(field)
        require(value is None or type(value) is int and 0<=value<horizon, 'invalid '+field)
        if value is not None:require(summary.get('active_members_final')==0, 'closed/suspended firm must have zero active employees')
    return _single_values(summary)


def extract_supported_observables(summary, config, binding):
    """Pure extraction after a separate native complete raw gate.

    r7/v2 are single enterprises. r8 requires an explicit scope: the buyer's
    final corporate-group employee/cash STOCKS, or the two-company full-period
    SYSTEM flows under different names. Target pre-acquisition flows must not
    silently become consolidated buyer revenue. Seller/provider are accounts,
    never extra operating companies. Model resource units are never dollars.
    """
    require(type(binding) is CompletedPredictionBinding, 'typed complete prediction binding required')
    require(all(_sha(getattr(binding,k)) for k in ('model_source_sha256','config_sha256',
            'summary_sha256','gate_receipt_sha256')), 'complete prediction provenance pins required')
    require(type(summary) is dict and type(config) is dict
            and digest(summary)==binding.summary_sha256 and digest(config)==binding.config_sha256,
            'prediction summary/config drift')
    require(_positive_int(binding.expected_days) and type(binding.world_seed) is int
            and binding.world_seed>=0 and type(binding.entities) is tuple
            and all(type(e) is EntityPredictionBinding for e in binding.entities), 'invalid complete prediction binding')
    horizon=binding.expected_days;source=binding.model_source_sha256
    if binding.model_version in (V2_MODEL,R7_MODEL):
        require(binding.observation_scope=='single-enterprise' and len(binding.entities)==1,
                'single enterprise scope must contain exactly one entity')
        entity=binding.entities[0]
        require(entity.model_version==binding.model_version and entity.seed==binding.world_seed,
                'single prediction model/seed drift')
        values=_entity(summary,config,entity,source,horizon,'single')
        units={'reported_full_time_and_part_time_employees':'persons',
               'operating_margin_model_units':'fraction','settled_revenue_model_resource_units':'model_resource_units'}
    elif binding.model_version==R8_MODEL:
        require(binding.observation_scope in ('buyer-corporate-group','two-firm-system'),
                'explicit joint observation scope required')
        require(set(config)=={'buyer','target','terms'} and type(config['terms']) is dict,
                'native joint config/terms envelope required')
        require(len(binding.entities)==2 and {e.company for e in binding.entities}=={'buyer','target'},
                'exactly two unique operating-unit bindings required; seller is not a company')
        require(summary.get('status')=='ACTUAL_TWO_EVOLVED_FIRM_HISTORY_ENGINEERING_COMPLETED'
                and type(summary.get('day')) is int and summary['day']==horizon
                and summary.get('source_sha256')==source, 'native complete joint result/source required')
        terms=config['terms'];global_world=terms.get('global_world');offer_day=terms.get('offer_day')
        require(type(global_world) is int and type(summary.get('initial_two_firm_global_world')) is int
                and summary['initial_two_firm_global_world']==global_world
                and type(offer_day) is int and 1<=offer_day<horizon, 'joint world/offer calendar differs')
        firms=summary.get('firm_summaries');owners=summary.get('owners');mode=summary.get('integration_status')
        require(type(firms) is dict and set(firms)=={'buyer','target'} and type(owners) is dict
                and set(owners)=={'buyer','target'} and owners['buyer']=='buyer', 'joint physical-unit roster differs')
        require(mode in ('declined','integrating','integrated','failed-owned')
                and owners['target']==('seller' if mode=='declined' else 'buyer'), 'joint actual ownership differs')
        by_company={e.company:e for e in binding.entities};parts={}
        for company in ('buyer','target'):
            require(by_company[company].model_version in (R7_MODEL,R8_MODEL,R10_MODEL), 'unsupported joint entity version')
            parts[company]=_entity(firms[company],config[company],by_company[company],source,horizon,company)
        r10=[e for e in binding.entities if e.model_version==R10_MODEL]
        if r10:
            require(len(r10)==2 and r10[0].engineering_evidence==r10[1].engineering_evidence
                    and binding.gate_receipt_sha256==r10[0].engineering_evidence.narrow_review_receipt.sha256,
                    'R10 joint constituents require one exact retained engineering receipt')
            original,original_config,original_source,original_horizon=_r10_evidence(r10[0].engineering_evidence)
            require(canonical(summary)==canonical(original['joint_result'])
                    and canonical(config)==canonical(original_config)
                    and source==original_source and horizon==original_horizon,
                    'R10 outer joint result/terms must match retained original engineering evidence')
        require(config['buyer']['seed']==binding.world_seed, 'joint buyer world seed differs')
        require(config['buyer']['longitudinal']==config['target']['longitudinal']
                or (config['buyer']['longitudinal'].get('calendar_start')==config['target']['longitudinal'].get('calendar_start')
                    and config['buyer']['longitudinal'].get('holidays',[])==config['target']['longitudinal'].get('holidays',[])),
                'joint Gregorian calendars differ')
        require(tuple(config['buyer'].get(k) for k in ('seed','world'))!=tuple(config['target'].get(k) for k in ('seed','world'))
                or config['buyer']['longitudinal'].get('world_context')!=config['target']['longitudinal'].get('world_context'),
                'joint target lacks its separate initial identity')
        offer=summary.get('offer')
        require(type(offer) is dict and offer.get('day')==offer_day
                and offer.get('source_sha256')==source and offer.get('target_config_sha256')==digest(config['target'])
                and offer.get('target_seed')==config['target']['seed'] and offer.get('target_world')==config['target']['world'],
                'joint offer target provenance differs or offer not reached')
        provider=_total(_amount(firms[c].get('joint_provider_resource_units',0.),'joint provider') for c in firms)
        consideration=_total(_amount(firms[c].get('joint_consideration_resource_units',0.),'consideration') for c in firms)
        treasury=_total(_amount(firms[c].get('joint_treasury_net_resource_units',0.),'internal treasury',nonnegative=False) for c in firms)
        require(math.isclose(treasury,0.,rel_tol=0,abs_tol=1e-8)
                and math.isclose(provider,_amount(summary.get('provider_account'),'provider account'),rel_tol=1e-11,abs_tol=1e-8)
                and math.isclose(consideration,_amount(summary.get('seller_account'),'seller account'),rel_tol=1e-11,abs_tol=1e-8),
                'joint provider/seller/internal accounts differ')
        selected=('buyer','target') if owners['target']=='buyer' else ('buyer',)
        if binding.observation_scope=='buyer-corporate-group':
            values={'reported_full_time_and_part_time_employees':sum(parts[c]['reported_full_time_and_part_time_employees'] for c in selected),
                    'corporate_cash_model_resource_units':_total(_amount(firms[c].get('cash_resource_units'),'corporate cash',nonnegative=False) for c in selected)}
            units={'reported_full_time_and_part_time_employees':'persons','corporate_cash_model_resource_units':'model_resource_units'}
        else:
            revenue=_total(parts[c]['settled_revenue_model_resource_units'] for c in firms)
            cost=_total(_amount(firms[c].get(k,0.),k) for c in firms for k in OPERATING_COSTS)
            values={'two_firm_system_active_members':sum(parts[c]['reported_full_time_and_part_time_employees'] for c in firms),
                    'two_firm_system_cash_model_resource_units':_total(_amount(firms[c].get('cash_resource_units'),'system cash',nonnegative=False) for c in firms),
                    'two_firm_system_settled_revenue_model_resource_units':revenue,
                    'two_firm_system_operating_margin_model_units':_margin(revenue,cost),
                    'joint_transaction_and_integration_model_resource_units':provider,
                    'joint_consideration_model_resource_units':consideration}
            units={k:('persons' if k=='two_firm_system_active_members' else 'fraction' if k.endswith('margin_model_units') else 'model_resource_units') for k in values}
    else:
        raise ValueError('unsupported complete prediction model version')
    return ExtractedObservables(values,units,binding.observation_scope,horizon)


def checked_prediction(summary, *, parameters, world_seed, key, gate_receipt_sha256,
                       config_sha256, model_source_sha256, config=None, binding=None):
    """Normalize declared observables without claiming raw or empirical validity.

    Legacy v2 pin-only callers retain their original values/packet ABI. r7/r8
    callers must supply typed complete-calendar/config/scope bindings. Neither
    path authenticates native gate receipts or replaces the separate raw gate.
    """
    require(all(_sha(v) for v in (gate_receipt_sha256,config_sha256,model_source_sha256)), 'complete gate/config pins required')
    require(type(world_seed) is int and world_seed>=0 and type(key) is str and key,
            'actual world seed and target key required')
    require(type(parameters) is dict and all(type(k) is str and k and finite(v) for k,v in parameters.items()),
            'finite declared parameter mapping required')
    if binding is None:
        require(config is None and summary.get('model_version')==V2_MODEL,
                'new model predictions require typed complete binding and actual config')
        require(_positive_int(summary.get('days_completed')), 'incomplete legacy summary')
        values=_single_values(summary);complete_days=summary['days_completed']
    else:
        require(not any(e.model_version==R10_MODEL for e in getattr(binding,'entities',())),
                'R10 narrow engineering observables cannot enter calibration/validation prediction packets')
        require(type(binding) is CompletedPredictionBinding
                and (binding.gate_receipt_sha256,binding.config_sha256,binding.model_source_sha256,binding.world_seed)
                ==(gate_receipt_sha256,config_sha256,model_source_sha256,world_seed), 'prediction caller/binding provenance differs')
        extracted=extract_supported_observables(summary,config,binding)
        values=extracted.values;complete_days=extracted.complete_days
    return {'parameters':parameters,'world_seed':world_seed,'key':key,'config_sha256':config_sha256,
        'gate_receipt_sha256':gate_receipt_sha256,'model_source_sha256':model_source_sha256,
        'complete_days':complete_days,'values':values}


def project_r10_engineering_observables(evidence):
    """Pure retained-metadata projection, no Model/SQL/full raw gate or fitting.

    Exact file pins and receipt semantics establish which original engineering
    metadata is projected. They do not upgrade narrow accounting to complete
    independent validation. Outer joint version stays R8; firms stay R10.
    """
    require(type(evidence) is tuple and len(evidence)==2
            and {e.arm for e in evidence if type(e) is R10EngineeringEvidence}=={'hierarchy','dams'},
            'exact typed two-arm R10 engineering evidence required')
    outputs=[];sources=set()
    for item in evidence:
        values,config,source,horizon=_r10_evidence(item);sources.add(source)
        summary=values['joint_result'];entities=[];firms={}
        for company in ('buyer','target'):
            cfg=config[company];firm=summary['firm_summaries'][company]
            entity=EntityPredictionBinding(company,R10_MODEL,source,digest(cfg),digest(firm),
                item.narrow_review_receipt.sha256,cfg['seed'],cfg['world'],horizon,firm['days_completed'],item)
            extracted=_entity(firm,cfg,entity,source,horizon,company)
            firms[company]={'model_version':R10_MODEL,'config_sha256':digest(cfg),'summary_sha256':digest(firm),
                'seed':cfg['seed'],'world':cfg['world'],'initial_population':cfg['n'],'complete_days':horizon,
                'values':extracted,'units':{'reported_full_time_and_part_time_employees':'persons',
                    'operating_margin_model_units':'fraction','settled_revenue_model_resource_units':'model_resource_units'}}
            entities.append(entity)
        scopes={}
        for scope in ('buyer-corporate-group','two-firm-system'):
            binding=CompletedPredictionBinding(R8_MODEL,scope,source,digest(config),digest(summary),
                item.narrow_review_receipt.sha256,config['buyer']['seed'],horizon,tuple(entities))
            scopes[scope]=asdict(extract_supported_observables(summary,config,binding))
        outputs.append({'arm':item.arm,'firms':firms,'scopes':scopes,
            'gate_scope':'retained narrow mechanism/accounting subsets and Root native-restore receipt metadata'})
    require(len(sources)==1,'R10 engineering pair source mismatch')
    return EngineeringObservablePacket(R10_MODEL,R8_MODEL,sources.pop(),tuple(outputs),evidence)


def export_r10_engineering_packet(packet):
    """Verify content integrity and reproject original refs before exporting.

    Checksums are not issuer authentication. Reprojection is metadata-only and
    never asserts that the separately referenced native raw gate is complete.
    """
    require(type(packet) is EngineeringObservablePacket,'typed R10 engineering packet required')
    body=asdict(packet);checksum=body.pop('content_sha256')
    require(_sha(checksum) and digest(body)==checksum,'engineering packet nested content changed after projection')
    fresh=asdict(project_r10_engineering_observables(packet.evidence));fresh.pop('content_sha256')
    require(canonical(body)==canonical(fresh),'engineering packet differs from original retained evidence projection')
    return {**body,'content_sha256':checksum}


def validate_predictions(protocol, predictions, *, role, parameter_points=None):
    labels=labels_of(protocol);keys=sorted(k for k,v in labels.items() if v.role==role)
    require(keys,'no records declared for prediction role')
    points=parameter_points or protocol['parameter_points'];expected={(digest(p),s,k) for p in points for s in protocol['world_seeds'] for k in keys}
    found={}
    for row in predictions:
        require(set(row)=={'parameters','world_seed','key','config_sha256','gate_receipt_sha256','model_source_sha256','complete_days','values'},'prediction packet fields differ')
        ident=(digest(row['parameters']),row['world_seed'],row['key'])
        require(ident in expected and ident not in found,'duplicate/unplanned candidate, seed or role')
        require(type(row['world_seed']) is int and type(row['complete_days']) is int and row['complete_days']>0,'incomplete run/seed')
        require(row['model_source_sha256']==protocol['model_source_sha256'] and all(SHA.fullmatch(row[n] or '') for n in ('config_sha256','gate_receipt_sha256')),'prediction provenance mismatch')
        require(set(row['values'])==set(protocol['metrics']) and all(finite(v) for v in row['values'].values()),'missing/nonfinite predictive metric')
        found[ident]=row
    require(set(found)==expected,'every fixed candidate/seed/record must be present')
    return found,keys


def target_values(protocol, targets, role):
    labels=labels_of(protocol);out={};expected={k for k,v in labels.items() if v.role==role}
    for row in targets:
        key=row['key'];require(key in expected and key not in out,'target overlap or wrong fitting/evaluation role')
        require(row['protocol_sha256']==protocol['sha256'] and row['purpose']==ROLES[role],'target provenance/role mismatch')
        require(SHA.fullmatch(row['source_sha256'] or '') and row['evidence_class'] in ('observed_reported_aggregate','synthetic_control'),'target lacks declared evidence')
        require(set(row['values'])==set(protocol['metrics']) and all(finite(v) for v in row['values'].values()),'all predeclared objectives need observed values')
        out[key]=row['values']
    require(set(out)==expected,'incomplete target objectives or records')
    return out


def rank(matrix,tol):
    a=[list(v) for v in matrix];r=0
    if not a:return 0
    for column in range(len(a[0])):
        pivot=max(range(r,len(a)),key=lambda k:abs(a[k][column]),default=None)
        if pivot is None or abs(a[pivot][column])<=tol:continue
        a[r],a[pivot]=a[pivot],a[r];p=a[r][column];a[r]=[v/p for v in a[r]]
        for k in range(r+1,len(a)):
            q=a[k][column];a[k]=[v-q*w for v,w in zip(a[k],a[r])]
        r+=1
        if r==len(a):break
    return r


def fit_calibration(path, protocol, predictions, targets):
    rows,keys=validate_predictions(protocol,predictions,role='calibration');observed=target_values(protocol,targets,'calibration')
    means={};scores=[]
    for p in protocol['parameter_points']:
        values={(k,m):math.fsum(rows[digest(p),s,k]['values'][m] for s in protocol['world_seeds'])/len(protocol['world_seeds']) for k in keys for m in protocol['metrics']}
        means[digest(p)]=values
        loss=math.fsum(spec['weight']*((values[k,m]-observed[k][m])/spec['scale'])**2 for k in keys for m,spec in protocol['metrics'].items())
        scores.append({'parameters':p,'score':loss})
    minimum=min(v['score'] for v in scores);best=[v for v in scores if v['score']<=minimum+protocol['score_tie_tolerance']]
    if len(best)!=1:
        seal(path,{'schema':'enterprise-calibration-fit-rejection-v1','protocol_sha256':protocol['sha256'],'status':'ambiguous_grid_solution','all_grid_scores':scores,'prediction_packet_sha256':digest(predictions),'target_packet_sha256':digest(targets)})
        raise ValueError('parameter recovery ambiguous: all scores preserved, no arbitrary selection')
    p=best[0]['parameters'];columns=[]
    # Local finite differences around the selected point, across all objectives.
    for name,grid in sorted(protocol['parameter_grid'].items()):
        alternatives=[v for v in grid if v!=p[name]];q={**p,name:min(alternatives,key=lambda v:abs(v-p[name]))}
        columns.append([(means[digest(q)][k,m]-means[digest(p)][k,m])/(q[name]-p[name])/protocol['metrics'][m]['scale'] for k in keys for m in protocol['metrics']])
    sensitivity=list(map(list,zip(*columns)));identified_rank=rank(sensitivity,protocol['identification_tolerance'])
    if identified_rank!=len(columns):
        seal(path,{'schema':'enterprise-calibration-fit-rejection-v1','protocol_sha256':protocol['sha256'],'status':'not_locally_identifiable','all_grid_scores':scores,'local_standardized_sensitivity':sensitivity,'sensitivity_rank':identified_rank,'parameter_count':len(columns),'prediction_packet_sha256':digest(predictions),'target_packet_sha256':digest(targets)})
        raise ValueError('parameters not locally identifiable from declared observables')
    return seal(path,{'schema':'enterprise-calibration-fit-v1','protocol_sha256':protocol['sha256'],
        'model_source_sha256':protocol['model_source_sha256'],'selected_parameters':p,'all_grid_scores':scores,
        'sensitivity_rank':identified_rank,'parameter_count':len(columns),'local_standardized_sensitivity':sensitivity,
        'prediction_packet_sha256':digest(predictions),'target_packet_sha256':digest(targets),
        'claims':'grid recovery and local sensitivity only; not global identifiability or empirical validity'})


def freeze_validation(path, protocol, fit, predictions):
    checked_packet(fit,'enterprise-calibration-fit-v1')
    require(fit['protocol_sha256']==protocol['sha256'] and fit['model_source_sha256']==protocol['model_source_sha256'],'fit/model drift')
    validate_predictions(protocol,predictions,role='validation',parameter_points=[fit['selected_parameters']])
    return seal(path,{'schema':'enterprise-frozen-validation-predictions-v1','protocol_sha256':protocol['sha256'],
        'model_source_sha256':protocol['model_source_sha256'],'fit_sha256':fit['sha256'],
        'selected_parameters':fit['selected_parameters'],'predictions':predictions,'target_values_included':False,
        'frozen_utc':datetime.now(timezone.utc).isoformat()})


def evaluate_validation(protocol, fit, frozen, targets):
    checked_packet(fit,'enterprise-calibration-fit-v1');checked_packet(frozen,'enterprise-frozen-validation-predictions-v1')
    require(frozen['protocol_sha256']==protocol['sha256'] and frozen['model_source_sha256']==protocol['model_source_sha256'] and frozen['fit_sha256']==fit['sha256'] and frozen['selected_parameters']==fit['selected_parameters'],'postfit evaluation drift')
    rows,keys=validate_predictions(protocol,frozen['predictions'],role='validation',parameter_points=[fit['selected_parameters']]);observed=target_values(protocol,targets,'validation')
    diagnostics=[];p=digest(fit['selected_parameters']);loss=0.
    for k in keys:
        for m,spec in protocol['metrics'].items():
            values=sorted(rows[p,s,k]['values'][m] for s in protocol['world_seeds']);mean=math.fsum(values)/len(values)
            error=(mean-observed[k][m])/spec['scale'];loss+=spec['weight']*error**2
            diagnostics.append({'key':k,'metric':m,'observed':observed[k][m],'predicted_mean':mean,
                'standardized_error':error,'predictive_min':values[0],'predictive_max':values[-1],
                'inside_fixed_world_range':values[0]<=observed[k][m]<=values[-1]})
    return {'schema':'enterprise-validation-score-v1','protocol_sha256':protocol['sha256'],'fit_sha256':fit['sha256'],
        'frozen_prediction_sha256':frozen['sha256'],'weighted_standardized_squared_error':loss,
        'diagnostics':diagnostics,'claims':'held-out temporal predictive diagnostics; fixed-world range is not a confidence interval',
        'historical_blindness_certified':False,'cross_enterprise_holdout_completed':False}
