"""Frozen external-data roles and multi-objective calibration for candidate ABMs.

Prediction packets must come from complete independently checked executions.
This module verifies packet provenance bindings, not their truth. Report parsing
currently supports one aggregate; no enterprise-level empirical fit is claimed.
Public targets exposed during development are retrospective diagnostics.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
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
OPERATING_COSTS = ('operating_resource_units', 'recruitment_resource_units',
                   'migration_resource_units', 'infrastructure_setup_resource_units')


@dataclass(frozen=True)
class EntityPredictionBinding:
    """Bindings supplied by a caller after its existing complete raw gate.

    Summary/config digests cover canonical mappings, not raw file bytes. The
    gate receipt pin references the caller's native evidence. This type does
    not execute that validator, authenticate a receipt or certify its truth.
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


def _entity(summary, config, binding, source, horizon, company):
    require(type(binding) is EntityPredictionBinding and binding.company==company,
            'typed entity scope binding required')
    require(all(_sha(getattr(binding,k)) for k in ('model_source_sha256','config_sha256',
            'summary_sha256','gate_receipt_sha256')), 'entity provenance pins required')
    require(type(config) is dict and type(summary) is dict, 'canonical actual entity mappings required')
    require(binding.model_source_sha256==source and digest(config)==binding.config_sha256
            and digest(summary)==binding.summary_sha256, 'entity source/config/summary drift')
    require(binding.model_version in (V2_MODEL,R7_MODEL,R8_MODEL)
            and summary.get('model_version')==binding.model_version, 'unsupported entity model version')
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
            require(by_company[company].model_version in (R7_MODEL,R8_MODEL), 'unsupported joint entity version')
            parts[company]=_entity(firms[company],config[company],by_company[company],source,horizon,company)
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
        require(type(binding) is CompletedPredictionBinding
                and (binding.gate_receipt_sha256,binding.config_sha256,binding.model_source_sha256,binding.world_seed)
                ==(gate_receipt_sha256,config_sha256,model_source_sha256,world_seed), 'prediction caller/binding provenance differs')
        extracted=extract_supported_observables(summary,config,binding)
        values=extracted.values;complete_days=extracted.complete_days
    return {'parameters':parameters,'world_seed':world_seed,'key':key,'config_sha256':config_sha256,
        'gate_receipt_sha256':gate_receipt_sha256,'model_source_sha256':model_source_sha256,
        'complete_days':complete_days,'values':values}


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
