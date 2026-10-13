"""Isolated normative candidate: one bound growth request, no Model integration.

All money, reserve, thresholds and delays come from the pinned original growth
API. A stored transition is applied by the future owner of the daily transaction.
Legacy synthetic proposal ballots cannot be relabelled as operating authority.
"""
from __future__ import annotations
import copy
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import types

BACKEND_SHA='1c833ad245eee387a6fa46a00ddbfe1ad68b9718f06417525420f22f04f89cfb'
PROPOSAL='R98-observed-growth-request-1'
BALLOT='R98-bound-growth-ballot-1'
OUTCOME='R98-growth-request-outcome-1'
TRANSITION='R98-growth-boundary-transition-1'
REQUEST_KINDS={'infrastructure_requested','hiring_requested'}

class Refused(ValueError):pass
class MissingOperatingProposal(Refused):pass

def require(ok,reason):
    if not ok:raise Refused(reason)
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def exact(v,names):require(type(v) is dict and set(v)==set(names),'exact schema differs')
def integer(v):require(type(v) is int and v>=0,'nonnegative integer required');return v
def number(v):require(type(v) in (int,float) and math.isfinite(v) and v>=0,'finite nonnegative resource required');return v
def identifier(v):require(type(v) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',v),'bounded identifier required');return v

def load_growth_api(path):
    """Execute only exact already-reviewed stdlib state API, never Model."""
    path=Path(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);raw=os.read(fd,131073);after=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before==after and len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==BACKEND_SHA,'growth backend bytes differ')
    finally:os.close(fd)
    name='r98_pinned_growth_'+BACKEND_SHA[:12]
    if name not in sys.modules:
        module=types.ModuleType(name);module.__file__=str(path);sys.modules[name]=module
        exec(compile(raw,str(path),'exec'),module.__dict__)
    return sys.modules[name]

def _config(growth):
    module=sys.modules.get('r98_pinned_growth_'+BACKEND_SHA[:12])
    require(module is not None and type(growth) is module.EnterpriseGrowthState,'pinned growth state API required')
    return dataclasses.asdict(growth.c)
def _state(growth):return growth.to_dict()
def _request(events,paid):
    items=[(kind,value) for kind,value in events if kind in REQUEST_KINDS]
    require(len(items)<=1,'backend returned unrelated multiple requests')
    if not items:return None
    kind,value=items[0];target=value['target'];before=value['target_before']
    # cash is rechecked at consumption. All other quoted action/cost/deadline
    # details must match; accepted does not authorize a different heuristic.
    details={k:v for k,v in value.items() if k!='cash_before'}
    return {'kind':kind,'target':target,'quantity':target-before,'cost':paid,
            'reserve':value['reserve'],'ready_day':value['ready_day'],'details':details}

def _validate_proposal(p):
    exact(p,('schema','proposal_id','request_key','scope','guild','observation_day','effective_day','expires_day',
             'backend_sha256','config_sha256','observations','request'))
    require(p['schema']==PROPOSAL and p['backend_sha256']==BACKEND_SHA,'proposal source/schema differs')
    identifier(p['scope']);integer(p['guild']);integer(p['observation_day'])
    integer(p['effective_day']);integer(p['expires_day'])
    require(type(p['config_sha256']) is str and re.fullmatch('[0-9a-f]{64}',p['config_sha256']),'configuration SHA required')
    require(p['effective_day']==p['observation_day']+1 and p['expires_day']==p['effective_day'],'only next boundary admitted')
    body={k:v for k,v in p.items() if k!='proposal_id'}
    require(p['proposal_id']=='r98-'+digest(body),'proposal content identity differs')
    expected=digest({'scope':p['scope'],'guild':p['guild'],'effective_day':p['effective_day'],
                     'config':p['config_sha256'],'request':p['request']})
    require(p['request_key']==expected,'bound request key differs')
    exact(p['observations'],('growth','cash','active','payroll_per_member','operating','expansion_allowed'))
    number(p['observations']['cash']);integer(p['observations']['active']);number(p['observations']['payroll_per_member'])
    require(p['observations']['operating'] is True and p['observations']['expansion_allowed'] is True,'observed eligibility required')
    exact(p['request'],('kind','target','quantity','cost','reserve','ready_day','details'))
    require(p['request']['kind'] in REQUEST_KINDS,'only original growth request admitted')
    for key in ('target','quantity','ready_day'):integer(p['request'][key])
    for key in ('cost','reserve'):number(p['request'][key])
    require(p['request']['quantity']>0 and type(p['request']['details']) is dict,'positive concrete request required')

def quote_observed_request(growth,*,scope,guild,observation_day,cash,active,payroll_per_member,operating=True,expansion_allowed=True):
    """Before voting: derive a concrete proposal solely from observed inputs."""
    _config(growth);identifier(scope);integer(guild);integer(observation_day);number(cash);integer(active);number(payroll_per_member)
    require(type(operating) is bool and type(expansion_allowed) is bool,'eligibility booleans required')
    require(operating and expansion_allowed,'operating request is ineligible')
    require(growth.last_observed_day is not None and growth.last_observed_day<=observation_day,'current/past observed cash required')
    require(growth.pending_construction is None and growth.pending_hiring is None,'existing obligations exclude new proposal')
    trial=copy.deepcopy(growth)
    paid,events=trial.begin_day(observation_day+1,cash,active=active,operating=True,
                              payroll_per_member=payroll_per_member,expansion_allowed=True)
    request=_request(events,paid)
    require(request is not None and request['quantity']>0,'original backend refused proposal quality/funding/calendar')
    observations={'growth':_state(growth),'cash':cash,'active':active,'payroll_per_member':payroll_per_member,
                  'operating':operating,'expansion_allowed':expansion_allowed}
    body={'schema':PROPOSAL,'scope':scope,'guild':guild,'observation_day':observation_day,
          'effective_day':observation_day+1,'expires_day':observation_day+1,
          'backend_sha256':BACKEND_SHA,'config_sha256':digest(_config(growth)),
          'observations':observations,'request':request}
    body['request_key']=digest({'scope':scope,'guild':guild,'effective_day':observation_day+1,
                               'config':body['config_sha256'],'request':request})
    return {**body,'proposal_id':'r98-'+digest(body)}

def outcome_from_bound_ballot(proposal,ballot):
    _validate_proposal(proposal)
    exact(ballot,('schema','proposal_id','decision_day','guild','eligible_person_ids','votes'))
    require(ballot['schema']==BALLOT and ballot['proposal_id']==proposal['proposal_id']
            and ballot['decision_day']==proposal['observation_day'] and ballot['guild']==proposal['guild'],
            'ballot does not bind this observed request')
    integer(ballot['decision_day']);integer(ballot['guild'])
    eligible=ballot['eligible_person_ids'];require(type(eligible) is list and eligible==sorted(set(eligible)),'eligible roster differs')
    for i in eligible:integer(i)
    require(type(ballot['votes']) is list,'votes must be a list')
    seen=set();weights=[];products=[]
    for vote in ballot['votes']:
        exact(vote,('person','guild','observed_signal','participates','formal_share','effective_weight','vote'))
        i=integer(vote['person']);integer(vote['guild']);require(i not in seen and i in eligible and vote['guild']==proposal['guild'],'duplicate/foreign vote');seen.add(i)
        require(type(vote['participates']) is bool,'participation boolean required')
        signal=vote['observed_signal'];require(type(signal) in (int,float) and math.isfinite(signal),'observed signal required')
        share=number(vote['formal_share']);require(share<=1,'formal share exceeds unity')
        number(vote['effective_weight']);require(vote['effective_weight']==(share if vote['participates'] else 0.),'original effective-weight rule differs')
        require(not vote['participates'] or i in eligible,'ineligible person participated')
        require(type(vote['vote']) in (int,float) and vote['vote']==float(signal>=0),'original vote/signal rule differs')
        weights.append(vote['effective_weight']);products.append(vote['effective_weight']*vote['vote'])
    mass=math.fsum(weights)
    # This .5 majority is the exact R10 ballot rule, not a new success parameter.
    state='unresolved' if not mass else 'accepted' if math.fsum(products)/mass>=.5 else 'rejected'
    return {'schema':OUTCOME,'proposal_id':proposal['proposal_id'],'decision_day':proposal['observation_day'],
            'state':state,'evidence_sha256':digest(ballot),'reason':None}

def failed_outcome(proposal,*,reason):
    _validate_proposal(proposal);identifier(reason)
    return {'schema':OUTCOME,'proposal_id':proposal['proposal_id'],'decision_day':proposal['observation_day'],
            'state':'failed','evidence_sha256':digest({'proposal':proposal['proposal_id'],'failure':reason}),'reason':reason}

def map_existing_record(decision,votes,*,operating_proposal=None,eligible_person_ids=None):
    """Audit the real caller gap. Legacy theta/loss ballots have no action link."""
    if decision.get('schema')!=BALLOT:
        raise MissingOperatingProposal('legacy decision lacks bound proposal_id, executable target/quantity/cost/deadline/request_key, observed cutoff and eligibility roster; theta/loss cannot supply them')
    require(operating_proposal is not None,'bound operating proposal required')
    require(decision['votes']==votes and decision['eligible_person_ids']==eligible_person_ids,'caller ballot projection differs')
    return outcome_from_bound_ballot(operating_proposal,decision)

class RequestBook:
    """Durable immutable IDs and transition receipts; no Model state is mutated."""
    def __init__(self,path):
        self.db=sqlite3.connect(path,isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY,raw BLOB NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outcomes (id TEXT PRIMARY KEY,raw BLOB NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS consumed (id TEXT PRIMARY KEY,scope TEXT NOT NULL,day INTEGER NOT NULL,raw BLOB NOT NULL,UNIQUE(scope,day))')
    def close(self):self.db.close()
    def _put(self,table,key,value):
        raw=canonical(value);row=self.db.execute('SELECT raw FROM '+table+' WHERE id=?',(key,)).fetchone()
        if row:require(row[0]==raw,'immutable record conflict');return
        self.db.execute('INSERT INTO '+table+'(id,raw) VALUES(?,?)',(key,raw))
    def propose(self,proposal):_validate_proposal(proposal);self._put('proposals',proposal['proposal_id'],proposal)
    def decide(self,outcome,*,ballot=None):
        exact(outcome,('schema','proposal_id','decision_day','state','evidence_sha256','reason'))
        row=self.db.execute('SELECT raw FROM proposals WHERE id=?',(outcome['proposal_id'],)).fetchone();require(row is not None,'proposal not persisted before outcome')
        p=json.loads(row[0]);require(outcome['schema']==OUTCOME and outcome['decision_day']==p['observation_day']
                                     and outcome['state'] in ('accepted','rejected','unresolved','failed'),'outcome shape/time differs')
        expected=failed_outcome(p,reason=outcome['reason']) if outcome['state']=='failed' else outcome_from_bound_ballot(p,ballot)
        require(outcome==expected,'outcome differs from full bound evidence')
        self._put('outcomes',outcome['proposal_id'],{'outcome':outcome,'ballot':ballot})
    def consume(self,proposal_id,growth,*,day,cash,active,payroll_per_member,operating,expansion_allowed):
        integer(day);number(cash);integer(active);number(payroll_per_member)
        require(type(operating) is bool and type(expansion_allowed) is bool,'eligibility booleans required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT raw FROM consumed WHERE id=?',(proposal_id,)).fetchone()
            if prior:
                result=json.loads(prior[0]);require(day==result['day'],'consumption day changed')
                require((_state(growth),cash) in ((result['before_growth'],result['cash_before']),(result['after_growth'],result['cash_after'])),'replay requires exact before or after resource pair')
                self.db.execute('COMMIT');return {'receipt':result,'replayed':True,'paid_now':0.}
            row=self.db.execute('SELECT raw FROM proposals WHERE id=?',(proposal_id,)).fetchone();require(row is not None,'unknown proposal')
            p=json.loads(row[0]);_validate_proposal(p)
            require(day>=p['effective_day'],'cannot consume before next boundary')
            latest=self.db.execute('SELECT MAX(day) FROM consumed WHERE scope=?',(p['scope'],)).fetchone()[0]
            require(latest is None or day>latest,'boundary already consumed or rolled back')
            row=self.db.execute('SELECT raw FROM outcomes WHERE id=?',(proposal_id,)).fetchone();outcome=json.loads(row[0])['outcome'] if row else None
            status='expired' if day>p['expires_day'] else outcome['state'] if outcome else 'missing_outcome'
            observed=p['observations']
            current=(digest(_config(growth))==p['config_sha256'] and _state(growth)==observed['growth']
                     and active==observed['active'] and payroll_per_member==observed['payroll_per_member']
                     and operating and expansion_allowed)
            if status=='accepted' and not current:status='stale_or_ineligible'
            trial=copy.deepcopy(growth)
            paid,events=trial.begin_day(day,cash,active=active,operating=operating,
                                       payroll_per_member=payroll_per_member,expansion_allowed=status=='accepted')
            requested=_request(events,paid)
            if status=='accepted' and requested is not None and requested!=p['request']:
                # Roll back the tentative unrelated action; still honor existing
                # completion/contraction obligations through the original API.
                status='request_mismatch';trial=copy.deepcopy(growth)
                paid,events=trial.begin_day(day,cash,active=active,operating=operating,
                                           payroll_per_member=payroll_per_member,expansion_allowed=False)
                requested=_request(events,paid)
            if status=='accepted' and requested is None:status='original_backend_refused'
            funded=status=='accepted' and requested==p['request']
            require(not requested or funded,'unrelated operating request escaped admission')
            result={'schema':TRANSITION,'proposal_id':proposal_id,'request_key':p['request_key'],'scope':p['scope'],
                    'day':day,'status':status,'new_request_funded':funded,'before_growth':_state(growth),
                    'after_growth':_state(trial),'config_sha256':digest(_config(growth)),'proposal_config_sha256':p['config_sha256'],'cash_before':cash,'cash_after':cash-paid,
                    'paid':paid,'events':events,'outcome_sha256':digest(outcome) if outcome else None,
                    'Model_integration_complete':False,'normative_candidate':True}
            self.db.execute('INSERT INTO consumed(id,scope,day,raw) VALUES(?,?,?,?)',(proposal_id,p['scope'],day,canonical(result)))
            self.db.execute('COMMIT');return {'receipt':result,'replayed':False,'paid_now':paid}
        except BaseException:
            self.db.execute('ROLLBACK');raise

def apply_transition(growth,cash,result):
    """Return one exact growth/cash pair; future daily owner applies atomically."""
    receipt=result['receipt'];require(digest(_config(growth))==receipt['config_sha256'],'growth configuration changed')
    if _state(growth)==receipt['after_growth'] and cash==receipt['cash_after']:return copy.deepcopy(growth),cash
    require(_state(growth)==receipt['before_growth'] and cash==receipt['cash_before'],'mixed/stale application resource pair')
    clone=copy.deepcopy(growth)
    for key in list(clone.__dict__):
        if key!='c':del clone.__dict__[key]
    clone.__dict__.update(copy.deepcopy(receipt['after_growth']))
    return clone,receipt['cash_after']
