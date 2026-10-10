"""Frozen external-data roles and multi-objective calibration for candidate ABMs.

Prediction packets must come from complete independently checked executions.
This module verifies packet provenance bindings, not their truth. Report parsing
currently supports one aggregate; no enterprise-level empirical fit is claimed.
Public targets exposed during development are retrospective diagnostics.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
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


def checked_prediction(summary, *, parameters, world_seed, key, gate_receipt_sha256, config_sha256, model_source_sha256):
    """Normalize real summary observables, retaining their independent-gate pins.

    Receipt SHA alone is provenance binding; caller must independently admit its
    complete typed fullraw gate, source, seed and config before generating packet.
    No hidden conversion of organizational model units to real currency is made.
    """
    require(all(SHA.fullmatch(v or '') for v in (gate_receipt_sha256,config_sha256,model_source_sha256)),'complete gate/config pins required')
    require(summary.get('model_version')=='longitudinal-enterprise-causal-2','wrong operating model summary')
    ops=summary['enterprise_operating_state_final']
    revenue=ops['delivered_revenue'];cost=math.fsum(summary.get(k,0.) for k in ('operating_resource_units','recruitment_resource_units','migration_resource_units','infrastructure_setup_resource_units'))
    return {'parameters':parameters,'world_seed':world_seed,'key':key,'config_sha256':config_sha256,
        'gate_receipt_sha256':gate_receipt_sha256,'model_source_sha256':model_source_sha256,
        'complete_days':summary['days_completed'],'values':{
            'reported_full_time_and_part_time_employees':summary['active_members_final'],
            'operating_margin_model_units':(revenue-cost)/revenue if revenue else None,
            'settled_revenue_model_resource_units':revenue}}


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
