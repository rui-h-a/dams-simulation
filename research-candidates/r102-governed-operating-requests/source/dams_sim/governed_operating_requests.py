"""R102 opt-in normative operating requests, exact helper + daily journal owner.

No empirical coefficients are added. The designated charter guild replaces its
legacy theta ballot with an end-of-day observed operating ballot; original
noise/participation/majority/cost rules remain explicit synthetic assumptions.
"""
from __future__ import annotations
import copy
import dataclasses
import json
import math
from pathlib import Path
from . import governed_request_normative_r98 as normative

VERSION='longitudinal-R102-governed-operating-requests-candidate-1'
STATE_SCHEMA='R102-journal-governed-request-state-1'
GROWTH_API=normative.load_growth_api(Path(__file__).with_name('enterprise_growth.py'))

@dataclasses.dataclass(frozen=True)
class GovernedOperatingConfig:
    schema_version:int=1
    governance_guild:int=0
    @classmethod
    def from_dict(cls,value):
        if type(value) is not dict or set(value)-{'schema_version','governance_guild'}:raise ValueError('unknown governed operating configuration')
        return cls(**value)
    def validate(self,outer):
        if type(self.schema_version) is not int or self.schema_version!=1 or type(self.governance_guild) is not int or not 0<=self.governance_guild<outer.guilds:
            raise ValueError('governed request schema/charter guild differs')
        if outer.enterprise_growth is None or outer.enterprise_operations is None or outer.organization_tasks is None or outer.organization_tasks.schema_version!=2:
            raise ValueError('R102 governed requests require original R10 operating/growth/structure paths')
        return self

def scope_for(config):
    return 'world:'+str(config.world)+':firm:'+normative.digest(config.longitudinal.world_context)[:32]

def model_version(config,legacy):
    return VERSION if config.governed_operating_requests is not None else legacy

class _Result:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value

class _JournalSQL:
    """Map exact R98 RequestBook SQL onto the existing journal + bounded cache.

    The outer daily transaction owns commit/rollback and checkpoint sidecars.
    R98 BEGIN/COMMIT is a savepoint; no new database/tables/schema are introduced.
    Full older history stays in journal, current3 records in the hashed header.
    """
    def __init__(self,owner):self.owner=owner;self.backup=None
    def execute(self,sql,args=()):
        o=self.owner;state=o.state
        normative.require(o.ledger.db.in_transaction,'governed request requires outer daily transaction')
        if sql=='BEGIN IMMEDIATE':
            normative.require(self.backup is None,'request savepoint already active')
            self.backup=copy.deepcopy(state);o.ledger.db.execute('SAVEPOINT governed_request_r102');return _Result(None)
        if sql=='COMMIT':
            normative.require(self.backup is not None,'request savepoint missing')
            o.ledger.db.execute('RELEASE governed_request_r102');self.backup=None;return _Result(None)
        if sql=='ROLLBACK':
            normative.require(self.backup is not None,'request savepoint missing')
            o.ledger.db.execute('ROLLBACK TO governed_request_r102');o.ledger.db.execute('RELEASE governed_request_r102')
            o.state.clear();o.state.update(self.backup);self.backup=None;return _Result(None)
        if sql.startswith('SELECT raw FROM '):
            table=sql.split()[3];normative.require(table in ('proposals','outcomes','consumed'),'unrelated RequestBook table')
            record=state[table];return _Result((normative.canonical(record['value']),) if record and record['id']==args[0] else None)
        if sql=='SELECT MAX(day) FROM consumed WHERE scope=?':
            normative.require(args[0]==state['scope'],'request consumption scope differs')
            return _Result((state['last_consumed_day'],))
        if sql.startswith('INSERT INTO '):
            table=sql.split()[2].split('(')[0];normative.require(table in ('proposals','outcomes','consumed'),'unrelated RequestBook insert')
            key=args[0];raw=args[-1];value=json.loads(raw)
            normative.require(state[table] is None,'bounded current request record conflict')
            if table=='consumed':
                normative.require(args[1]==state['scope'] and args[2]==o.day,'request boundary differs')
                normative.require(o._consume_context is not None and state['consumption_context'] is None,'actual consume context missing or repeated')
                validate_transition(value,state['proposals']['value'],state['outcomes']['value'] if state['outcomes'] else None,context=o._consume_context,growth_config=o.growth_config)
                o.ledger.add_journal(o.day,'governed_consumption_context','governed:consume-context:'+key,o._consume_context)
                state['consumption_context']=copy.deepcopy(o._consume_context)
                state['last_consumed_day']=o.day
            o.ledger.add_journal(o.day,'governed_'+table,'governed:'+table+':'+key,value)
            state[table]={'id':key,'value':value};return _Result(None)
        raise ValueError('unsupported exact normative RequestBook SQL')

TRANSITION_KEYS=('schema','proposal_id','request_key','scope','day','status','new_request_funded',
    'before_growth','after_growth','config_sha256','proposal_config_sha256','cash_before','cash_after',
    'paid','events','outcome_sha256','Model_integration_complete','normative_candidate')
TRANSITION_STATUS={'accepted','rejected','unresolved','failed','expired','missing_outcome',
    'stale_or_ineligible','request_mismatch','original_backend_refused'}

def validate_charter(c):
    normative.exact(c,('day','guild','members','settled'))
    normative.integer(c['day']);normative.integer(c['guild'])
    normative.require(type(c['settled']) is bool and type(c['members']) is list,'charter settlement/roster type differs')
    ids=[m['person'] for m in c['members']]
    normative.require(ids==sorted(set(ids)),'charter roster differs')
    for m in c['members']:
        normative.exact(m,('person','share','reserved_hours'));normative.integer(m['person']);normative.number(m['share']);normative.number(m['reserved_hours'])
        normative.require(m['share']<=1 and m['reserved_hours'] in (0.,.05),'charter budget/share differs')

def validate_charter_ballot(c,p,b):
    normative.require(c and c['day']==p['observation_day'] and c['guild']==p['guild'],'actual morning charter differs')
    ids=[m['person'] for m in c['members']]
    normative.require(type(b) is dict and b['eligible_person_ids']==ids and sorted(v['person'] for v in b['votes'])==ids,'actual charter eligibility/full ballot roster differs')
    by_id={m['person']:m for m in c['members']}
    for v in b['votes']:
        m=by_id[v['person']]
        normative.require(v['formal_share']==m['share'] and (not v['participates'] or m['reserved_hours']==.05),'actual reserved participation/share differs')

def validate_outcome(p,entry,charter=None):
    normative.exact(entry,('outcome','ballot'));out=entry['outcome'];b=entry['ballot']
    expected=normative.failed_outcome(p,reason=out['reason']) if out['state']=='failed' else normative.outcome_from_bound_ballot(p,b)
    normative.require(normative.canonical(out)==normative.canonical(expected),'cached ballot/outcome differs')
    if charter is not None and b is not None:validate_charter_ballot(charter,p,b)
    return out

def _typed_growth(state,reference,day):
    keys={'capacity','guilds','departments','sites','target','pending_construction','pending_hiring','revenue_signal','cost_signal','observed_workdays','last_observed_day'}
    if 'pending_revenue' in reference:keys|={'pending_revenue','pending_operating_cost','last_cash_observed_day'}
    normative.exact(state,keys)
    for k in ('capacity','guilds','departments','sites','target','observed_workdays'):normative.integer(state[k])
    normative.require(state['target']<=state['capacity'] and state['observed_workdays']<=day,'growth inventory/exposure differs')
    normative.require((state['last_observed_day'] is None)==(state['observed_workdays']==0),'growth observed exposure differs')
    for k in ('revenue_signal','cost_signal','pending_revenue','pending_operating_cost'):
        if k in state:normative.number(state[k])
    for k in ('last_observed_day','last_cash_observed_day'):
        if k in state and state[k] is not None:normative.integer(state[k]);normative.require(state[k]<day,'transition observes future information')
    for k,fields in (('pending_hiring',('target','ready_day')),('pending_construction',('capacity','guilds','departments','sites','target','requested_day','ready_day'))):
        if state[k] is not None:
            normative.exact(state[k],fields)
            for field in fields:normative.integer(state[k][field])
            if k=='pending_construction':normative.require(state[k]['ready_day']>=state[k]['requested_day'],'construction date order differs')

def validate_proposal(p):
    normative._validate_proposal(p)
    observed=p['observations']['growth'];request=p['request'];details=request['details']
    _typed_growth(observed,observed,p['effective_day'])
    normative.require(observed['last_observed_day'] is not None and observed['pending_construction'] is None and observed['pending_hiring'] is None,'proposal lacks available observed growth or bypasses pending obligations')
    normative.require(request['ready_day']>=p['effective_day'] and request['quantity']==request['target']-observed['target'],'proposal target/quantity/deadline differs')
    for key in ('target_before','previous_observed_day','requested_target','target','ready_day'):normative.integer(details[key])
    normative.require(details['target_before']==observed['target'] and details['previous_observed_day']==observed['last_observed_day'] and details['requested_target']==details['target']==request['target'] and details['ready_day']==request['ready_day'],'proposal concrete original request details differ')
    normative.require(details['reserve']==request['reserve'] and details['setup_cost']==request['cost'] and p['observations']['cash']>=request['cost']+request['reserve'],'proposal original cash/cost/reserve differs')
    margin=(observed['revenue_signal']-observed['cost_signal'])/max(observed['revenue_signal'],observed['cost_signal'],1e-12)
    normative.require(type(details['margin']) in (int,float) and details['margin']==margin,'proposal public observed margin differs')

def _validate_transition_shape(r,p,outcome_entry,*,checkpoint_day=None):
    normative.exact(r,TRANSITION_KEYS);validate_proposal(p)
    normative.require(r['schema']==normative.TRANSITION and r['status'] in TRANSITION_STATUS,'transition schema/status differs')
    normative.integer(r['day']);normative.require(r['day']>=p['effective_day'],'transition precedes effective boundary')
    if checkpoint_day is not None:normative.require(r['day']<checkpoint_day,'transition exceeds committed checkpoint boundary')
    normative.require((r['proposal_id'],r['request_key'],r['scope'])==(p['proposal_id'],p['request_key'],p['scope']),'transition proposal/scope/request differs')
    normative.require(r['proposal_config_sha256']==p['config_sha256'] and r['config_sha256']==p['config_sha256'],'frozen transition configuration/source differs')
    normative.require(type(r['new_request_funded']) is bool and r['Model_integration_complete'] is False and r['normative_candidate'] is True,'transition typed flags differ')
    for k in ('cash_before','cash_after','paid'):normative.number(r[k])
    normative.require(r['cash_after']==r['cash_before']-r['paid'],'transition cash/payment does not conserve')
    normative.require(type(r['events']) is list,'transition event list differs')
    for event in r['events']:
        normative.require(type(event) in (list,tuple) and len(event)==2 and type(event[0]) is str and type(event[1]) is dict,'transition event type differs')
        normative.require(event[0] in ('infrastructure_complete','hiring_target_ready','workforce_contraction','infrastructure_requested','hiring_requested','growth_unfunded'),'foreign original growth event')
        normative.canonical(event)
    before=r['before_growth'];after=r['after_growth'];_typed_growth(before,p['observations']['growth'],r['day']);_typed_growth(after,before,r['day'])
    for k in ('revenue_signal','cost_signal','observed_workdays','last_observed_day','pending_revenue','pending_operating_cost','last_cash_observed_day'):
        if k in before:normative.require(after[k]==before[k],'boundary changed observed information')
    outcome=validate_outcome(p,outcome_entry) if outcome_entry is not None else None
    normative.require(r['outcome_sha256']==(normative.digest(outcome) if outcome is not None else None),'transition outcome digest differs')
    status=r['status']
    if r['day']>p['expires_day']:normative.require(status=='expired','expired transition status differs')
    elif outcome is None:normative.require(status=='missing_outcome','missing outcome transition differs')
    elif outcome['state']!='accepted':normative.require(status==outcome['state'],'nonaccepted outcome transition differs')
    else:normative.require(status in ('accepted','stale_or_ineligible','request_mismatch','original_backend_refused'),'accepted outcome transition status differs')
    request=normative._request(r['events'],r['paid'])
    normative.require(r['new_request_funded']==(status=='accepted'),'funded flag does not follow exact accepted transition')
    if r['new_request_funded']:
        normative.require(outcome is not None and outcome['state']=='accepted' and r['day']==p['effective_day'],'funded request lacks timely accepted outcome')
        normative.require(before==p['observations']['growth'] and request==p['request'] and r['paid']==p['request']['cost'],'funded before/action/payment differs from exact quoted request')
        # Original accepted request changes only installation/hiring pending
        # inventory; verify its concrete after-state rather than a free flag.
        expected=copy.deepcopy(before);details=p['request']['details']
        if request['kind']=='infrastructure_requested':expected['pending_construction']={k:details[k] for k in ('capacity','guilds','departments','sites','target','requested_day','ready_day')}
        else:expected['pending_hiring']={k:details[k] for k in ('target','ready_day')}
        normative.require(after==expected,'funded after-growth differs from original request')
        normative.require(r['cash_before']>=r['paid']+p['request']['reserve'],'funded request violates original cash reserve')
    else:normative.require(request is None and r['paid']==0.,'unfunded transition emitted unrelated request/payment')

CONTEXT_SCHEMA='R102-original-growth-consumption-context-1'
HELPER_SHA='b6d76f3f96a23f8c87a47889c104961f820d9efca93d45258ec1b05c8c711e26'
CONTEXT_KEYS=('schema','proposal_id','day','backend_sha256','normative_helper_sha256','config_body','config_sha256','before_growth_sha256','cash_before','active','payroll_per_member','operating','expansion_allowed')

def config_body(config):
    normative.require(config is not None,'actual complete growth configuration required')
    body=dataclasses.asdict(config) if dataclasses.is_dataclass(config) else copy.deepcopy(config)
    typed=GROWTH_API.EnterpriseGrowthConfig.from_dict(body)
    normative.require(dataclasses.asdict(typed)==body,'actual growth config body must be complete')
    return body

def validate_proposal_backend(proposal,growth_config):
    validate_proposal(proposal);body=config_body(growth_config)
    normative.require(normative.digest(body)==proposal['config_sha256'],'proposal differs from actual complete growth configuration')
    observed=proposal['observations'];state=GROWTH_API.EnterpriseGrowthState.__new__(GROWTH_API.EnterpriseGrowthState)
    state.c=GROWTH_API.EnterpriseGrowthConfig.from_dict(body)
    for key,value in observed['growth'].items():setattr(state,key,copy.deepcopy(value))
    exact=normative.quote_observed_request(state,scope=proposal['scope'],guild=proposal['guild'],observation_day=proposal['observation_day'],cash=observed['cash'],active=observed['active'],payroll_per_member=observed['payroll_per_member'],operating=observed['operating'],expansion_allowed=observed['expansion_allowed'])
    normative.require(normative.canonical(exact)==normative.canonical(proposal),'proposal does not match exact original backend quote')

def consumption_context(growth,proposal_id,*,day,cash,active,payroll_per_member,operating,expansion_allowed):
    body=normative._config(growth)
    return {'schema':CONTEXT_SCHEMA,'proposal_id':proposal_id,'day':day,'backend_sha256':normative.BACKEND_SHA,
        'normative_helper_sha256':HELPER_SHA,'config_body':body,'config_sha256':normative.digest(body),
        'before_growth_sha256':normative.digest(growth.to_dict()),'cash_before':cash,'active':active,
        'payroll_per_member':payroll_per_member,'operating':operating,'expansion_allowed':expansion_allowed}

def validate_transition(r,p,outcome_entry,*,context,growth_config,checkpoint_day=None):
    """Exact original backend replay, never infer config or real inputs from SHA."""
    _validate_transition_shape(r,p,outcome_entry,checkpoint_day=checkpoint_day)
    normative.exact(context,CONTEXT_KEYS);body=config_body(growth_config)
    normative.require(context['schema']==CONTEXT_SCHEMA and context['backend_sha256']==normative.BACKEND_SHA and context['normative_helper_sha256']==HELPER_SHA,'consume context source/schema differs')
    normative.require(context['config_body']==body and context['config_sha256']==normative.digest(body)==p['config_sha256'],'consume context differs from actual frozen full configuration')
    normative.require(context['proposal_id']==p['proposal_id'] and context['day']==r['day'] and type(context['day']) is int,'consume context identity/day differs')
    for key in ('cash_before','payroll_per_member'):normative.number(context[key])
    normative.integer(context['active'])
    normative.require(context['payroll_per_member']==p['observations']['payroll_per_member'],'actual frozen payroll context differs')
    normative.require(type(context['operating']) is bool and type(context['expansion_allowed']) is bool,'consume context eligibility type differs')
    normative.require(context['cash_before']==r['cash_before'] and context['before_growth_sha256']==normative.digest(r['before_growth']),'consume context before resource/observation binding differs')
    # This constructs only a pinned business-state value, not a Model/world.
    before=GROWTH_API.EnterpriseGrowthState.__new__(GROWTH_API.EnterpriseGrowthState)
    before.c=GROWTH_API.EnterpriseGrowthConfig.from_dict(body)
    c=before.c;state=r['before_growth']
    normative.require(c.initial_organization_capacity<=state['capacity']<=c.maximum_organization_capacity and c.minimum_target_members<=state['target']<=state['capacity'],'before growth exceeds actual configuration inventory')
    normative.require(state['capacity']<=state['guilds']*c.members_per_guild and state['capacity']<=state['sites']*c.members_per_site and state['guilds']<=state['departments']*c.guilds_per_department,'before growth lacks actual installed support')
    for key,value in r['before_growth'].items():setattr(before,key,copy.deepcopy(value))
    outcome=validate_outcome(p,outcome_entry) if outcome_entry is not None else None
    status='expired' if r['day']>p['expires_day'] else outcome['state'] if outcome is not None else 'missing_outcome'
    observed=p['observations']
    current=(before.to_dict()==observed['growth'] and context['active']==observed['active']
             and context['payroll_per_member']==observed['payroll_per_member'] and context['operating'] and context['expansion_allowed'])
    if status=='accepted' and not current:status='stale_or_ineligible'
    def original(allowed):
        trial=copy.deepcopy(before)
        paid,events=trial.begin_day(r['day'],r['cash_before'],active=context['active'],operating=context['operating'],
            payroll_per_member=context['payroll_per_member'],expansion_allowed=allowed)
        return trial,paid,events,normative._request(events,paid)
    trial,paid,events,request=original(status=='accepted')
    if status=='accepted' and request is not None and request!=p['request']:
        status='request_mismatch';trial,paid,events,request=original(False)
    if status=='accepted' and request is None:status='original_backend_refused'
    funded=status=='accepted' and request==p['request']
    normative.require(not request or funded,'original backend returned unrelated request')
    normative.require(r['status']==status and r['new_request_funded']==funded,'transition status/funding does not match actual original backend inputs')
    normative.require(r['after_growth']==trial.to_dict() and r['paid']==paid and r['cash_after']==r['cash_before']-paid
        and normative.canonical(r['events'])==normative.canonical(events),'transition after/events/cash/payment does not match exact original backend')

def settlement_record(day,charter,proposal=None,outcome_entry=None):
    validate_charter(charter);normative.require(charter['day']==day,'settlement day differs')
    today=proposal is not None and proposal['observation_day']==day
    if today:
        normative.require(outcome_entry is not None and outcome_entry['ballot'] is not None,'today proposal lacks persisted full ballot')
        b=outcome_entry['ballot'];validate_outcome(proposal,outcome_entry,charter)
    else:b=None
    participants=sorted(v['person'] for v in b['votes'] if v['participates']) if b is not None else []
    reserved=math.fsum(m['reserved_hours'] for m in charter['members']);used=math.fsum(m['reserved_hours'] for m in charter['members'] if m['person'] in participants)
    return {'day':day,'reserved_hours':reserved,'used_hours':used,'released_idle_hours':reserved-used,
        'participating_person_ids':participants,'proposal_id':b['proposal_id'] if b is not None else None,'late_release_creates_no_output':True}

class JournalRequestBook:
    def __init__(self,ledger,scope,state=None):
        self.ledger=ledger;self.day=None;self.growth_config=None;self._consume_context=None
        self.state=copy.deepcopy(state) if state is not None else {'schema':STATE_SCHEMA,'scope':scope,
            'proposals':None,'outcomes':None,'consumed':None,'pending_proposal_id':None,'last_consumed_day':None,'charter':None,'consumption_context':None}
        normative.require(self.state['schema']==STATE_SCHEMA and self.state['scope']==scope,'request state scope/schema differs')
        self.norm=normative.RequestBook.__new__(normative.RequestBook);self.norm.db=_JournalSQL(self)
        self.validate()
    def to_dict(self):return copy.deepcopy(self.state)
    def validate(self):
        normative.exact(self.state,('schema','scope','proposals','outcomes','consumed','pending_proposal_id','last_consumed_day','charter','consumption_context'))
        normative.require(self.state['schema']==STATE_SCHEMA,'request state schema differs');normative.identifier(self.state['scope'])
        c=self.state['charter'];p=self.state['proposals'];out=self.state['outcomes'];consumed=self.state['consumed']
        if c is not None:validate_charter(c)
        if self.state['last_consumed_day'] is not None:normative.integer(self.state['last_consumed_day'])
        if p is not None:
            normative.exact(p,('id','value'));validate_proposal(p['value'])
            normative.require(p['id']==p['value']['proposal_id'] and p['value']['scope']==self.state['scope'],'proposal cache identity differs')
        if out is not None:
            normative.exact(out,('id','value'));normative.require(p is not None and out['id']==p['id'],'outcome cache lacks proposal')
            validate_outcome(p['value'],out['value'],c if c is not None and c['day']==p['value']['observation_day'] else None)
        if consumed is not None:
            normative.exact(consumed,('id','value'));normative.require(p is not None and consumed['id']==p['id'],'transition cache identity differs')
            _validate_transition_shape(consumed['value'],p['value'],out['value'] if out else None)
            normative.require(self.state['consumption_context'] is not None,'consumed cache lacks actual input context')
            if self.growth_config is not None:validate_transition(consumed['value'],p['value'],out['value'] if out else None,context=self.state['consumption_context'],growth_config=self.growth_config)
            normative.require(consumed['value']['day']==self.state['last_consumed_day'] and self.state['pending_proposal_id'] is None,'consumed cache boundary differs')
        if consumed is None:normative.require(self.state['consumption_context'] is None,'orphan consumed context cache')
        if self.state['pending_proposal_id'] is not None:normative.require(p is not None and self.state['pending_proposal_id']==p['id'] and consumed is None,'pending request cache differs')
    def propose(self,proposal,day):
        validate_proposal(proposal)
        if self.growth_config is not None:validate_proposal_backend(proposal,self.growth_config)
        normative.require(self.state['charter'] is not None and self.state['charter']['day']==day and not self.state['charter']['settled'],'proposal requires current unsettled charter')
        if self.state['pending_proposal_id']==proposal['proposal_id']:
            self.norm.propose(proposal);return
        normative.require(self.state['pending_proposal_id'] is None,'prior boundary request not consumed')
        normative.require(self.state['last_consumed_day'] is None or day>=self.state['last_consumed_day'],'proposal observed cutoff rolled back')
        normative.require(proposal['observation_day']==day and proposal['scope']==self.state['scope'],'proposal observed scope/cutoff differs')
        self.day=day
        self.state.update(proposals=None,outcomes=None,consumed=None,consumption_context=None)
        self.norm.propose(proposal);self.state['pending_proposal_id']=proposal['proposal_id']
    def reserve(self,day,guild,members):
        normative.require(self.ledger.db.in_transaction,'charter requires outer daily transaction')
        normative.integer(day);normative.integer(guild);previous=self.state['charter']
        normative.require(previous is None or day>previous['day'],'charter already reserved or time rolled back')
        normative.require(self.state['last_consumed_day'] is None or day>=self.state['last_consumed_day'],'charter precedes consumed boundary')
        normative.require(self.state['pending_proposal_id'] is None,'old request must be consumed before new charter')
        proposed={'day':day,'guild':guild,'members':copy.deepcopy(members),'settled':False};validate_charter(proposed)
        self.ledger.add_journal(day,'governed_charter_reservation',f'governed:charter:{day}',proposed)
        self.day=day;self.state['charter']=proposed
    def _validate_charter_ballot(self,proposal,ballot):validate_charter_ballot(self.state['charter'],proposal,ballot)
    def decide(self,proposal,ballot):
        normative.require(self.state['charter'] is not None and not self.state['charter']['settled'],'ballot after charter settled')
        self._validate_charter_ballot(proposal,ballot)
        outcome=normative.outcome_from_bound_ballot(proposal,ballot);self.norm.decide(outcome,ballot=ballot);return outcome
    def settle(self,day,ballot=None):
        normative.require(self.ledger.db.in_transaction,'settlement requires outer daily transaction')
        normative.integer(day);c=self.state['charter'];normative.require(c and c['day']==day and not c['settled'],'charter already settled or foreign day')
        p=self.state['proposals'];out=self.state['outcomes'];today=p is not None and p['value']['observation_day']==day
        if today:
            normative.require(out is not None and out['value']['ballot'] is not None and normative.canonical(ballot)==normative.canonical(out['value']['ballot']),'settlement differs from persisted full ballot')
        else:normative.require(ballot is None,'settlement ballot has no current observed proposal')
        record=settlement_record(day,c,p['value'] if p else None,out['value'] if out else None)
        self.ledger.add_journal(day,'governed_charter_settlement',f'governed:settlement:{day}',record)
        c['settled']=True;return record
    def consume(self,growth,*,day,cash,active,payroll_per_member,operating,expansion_allowed):
        self.day=day;self.growth_config=normative._config(growth);key=self.state['pending_proposal_id']
        prior=self.state['consumed']
        if key is None and prior and prior['value']['day']==day:key=prior['id']
        if key is None:
            # Original completion, cancellation/contraction obligations survive;
            # no heuristic new expansion bypasses a missing operating ballot.
            trial=copy.deepcopy(growth);paid,events=trial.begin_day(day,cash,active=active,operating=operating,
                payroll_per_member=payroll_per_member,expansion_allowed=False)
            return trial,cash-paid,paid,events,None
        self._consume_context=consumption_context(growth,key,day=day,cash=cash,active=active,payroll_per_member=payroll_per_member,operating=operating,expansion_allowed=expansion_allowed)
        if prior is not None and prior['id']==key:
            original=self.state['consumption_context'];normative.require(original is not None,'replay context missing')
            for field in ('config_body','config_sha256','active','payroll_per_member','operating','expansion_allowed'):normative.require(self._consume_context[field]==original[field],'replay actual input context changed')
        result=self.norm.consume(key,growth,day=day,cash=cash,active=active,payroll_per_member=payroll_per_member,
                                 operating=operating,expansion_allowed=expansion_allowed)
        updated,cash_after=normative.apply_transition(growth,cash,result)
        self.state['pending_proposal_id']=None
        return updated,cash_after,result['paid_now'],result['receipt']['events'],result['receipt']
    def verify_committed_header(self,day,*,growth_config=None,full_model=False):
        normative.require(type(full_model) is bool,'full Model verification scope type differs')
        self.growth_config=config_body(growth_config) if growth_config is not None else self.growth_config
        if full_model:normative.require(self.growth_config is not None,'full Model actual growth configuration missing')
        self.validate();normative.integer(day)
        normative.require(not self.ledger.db.in_transaction,'checkpoint requires committed journal boundary')
        expected={'schema':STATE_SCHEMA,'scope':self.state['scope'],'proposals':None,'outcomes':None,'consumed':None,'pending_proposal_id':None,'last_consumed_day':None,'charter':None,'consumption_context':None}
        endings=0;latest=None;actual_votes=[];previous_growth=None;pending_context=None;market_begin=None;eligible=set()
        totals={k:0. for k in ('operating_proposals','operating_requests_consumed','operating_requests_funded','governed_request_paid_resource_units','operating_eligible_vote_rows','operating_participating_vote_rows','operating_effective_vote_mass','operating_governance_reserved_hours','operating_governance_used_hours','operating_governance_released_idle_hours','operating_ballots_accepted','operating_ballots_rejected','operating_ballots_unresolved','operating_ballots_failed')}
        # Decoded full original journal API supports flat, compressed and native.
        # Only current records are retained in memory; old cache copies cannot
        # authenticate themselves merely by equality to a modified day_end.
        for seq,when,kind,event,payload in self.ledger.rows('journal'):
            latest=(seq,when,kind,event,payload)
            if full_model and kind in ('entry','exit','retirement','layoff'):
                member=json.loads(payload);person=normative.integer(member['person'])
                if kind=='entry':
                    normative.require(person not in eligible,'actual entry identity repeated');eligible.add(person)
                else:normative.require(person in eligible,'actual exit identity unknown');eligible.remove(person)
            if kind=='enterprise_market_begin':
                market_begin=json.loads(payload)
            if kind=='operating_vote':
                value=json.loads(payload);normative.exact(value,('proposal_id','person','guild','observed_signal','participates','formal_share','effective_weight','vote'))
                normative.require(when==endings,'operating vote journal date/order differs');actual_votes.append(value)
            elif kind.startswith('governed_'):
                normative.require(type(when) is int and 0<=when<day,'governed journal outside checkpoint')
                value=json.loads(payload,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('nonfinite governed journal')))
                c=expected['charter'];p=expected['proposals'];out=expected['outcomes']
                if kind=='governed_charter_reservation':
                    validate_charter(value);normative.require(value['day']==when and not value['settled'] and event==f'governed:charter:{when}','reservation raw identity differs')
                    normative.require(when==endings and (c is None or c['settled'] and when>c['day']) and expected['pending_proposal_id'] is None,'reservation journal repeats, rolls back or bypasses outstanding request')
                    expected['charter']=value
                elif kind=='governed_proposals':
                    validate_proposal(value)
                    if self.growth_config is not None:validate_proposal_backend(value,self.growth_config)
                    normative.require(c is not None and c['day']==when and not c['settled'] and value['observation_day']==when and value['guild']==c['guild'] and value['scope']==expected['scope'],'proposal raw observed charter differs')
                    normative.require(expected['pending_proposal_id'] is None and event=='governed:proposals:'+value['proposal_id'],'proposal raw duplicate/pending identity differs')
                    expected.update(proposals={'id':value['proposal_id'],'value':value},outcomes=None,consumed=None,pending_proposal_id=value['proposal_id'],consumption_context=None)
                elif kind=='governed_outcomes':
                    normative.require(p is not None and c is not None and not c['settled'] and c['day']==when and p['value']['observation_day']==when and out is None and event=='governed:outcomes:'+p['id'],'outcome raw identity/order differs')
                    outcome=validate_outcome(p['value'],value,c);expected['outcomes']={'id':p['id'],'value':value}
                    totals['operating_proposals']+=1;totals['operating_ballots_'+outcome['state']]+=1
                    votes=value['ballot']['votes'] if value['ballot'] is not None else []
                    totals['operating_eligible_vote_rows']+=len(votes);totals['operating_participating_vote_rows']+=sum(v['participates'] for v in votes);totals['operating_effective_vote_mass']+=math.fsum(v['effective_weight'] for v in votes)
                elif kind=='governed_consumption_context':
                    normative.exact(value,CONTEXT_KEYS)
                    normative.require(p is not None and expected['pending_proposal_id']==p['id'] and pending_context is None and when==endings and value['day']==when and event=='governed:consume-context:'+p['id'],'consume context raw identity/order differs')
                    pending_context=value
                elif kind=='governed_consumed':
                    normative.require(p is not None and expected['pending_proposal_id']==p['id'] and expected['consumed'] is None and event=='governed:consumed:'+p['id'],'consumed raw identity/order differs')
                    normative.require(pending_context is not None,'consumed raw missing actual input context')
                    validate_transition(value,p['value'],out['value'] if out else None,context=pending_context,growth_config=self.growth_config,checkpoint_day=day)
                    if full_model:
                        normative.require(previous_growth is not None and value['before_growth']==previous_growth,'consume before-growth differs from actual previous Model day')
                        normative.require(market_begin is not None and market_begin['day']==when and pending_context['cash_before']==market_begin['cash_before']+(market_begin['received']+market_begin['borrowed']) and pending_context['operating']==market_begin['operating'],'consume actual market/cash/day-bound operating context differs')
                        normative.require(pending_context['active']==len(eligible),'consume actual pre-lifecycle roster count differs')
                    normative.require(when==endings and value['day']==when and (expected['last_consumed_day'] is None or when>expected['last_consumed_day']),'consumed raw day repeats or rolls back')
                    expected.update(consumed={'id':p['id'],'value':value},pending_proposal_id=None,last_consumed_day=when,consumption_context=pending_context)
                    pending_context=None
                    totals['operating_requests_consumed']+=1;totals['operating_requests_funded']+=int(value['new_request_funded']);totals['governed_request_paid_resource_units']+=value['paid']
                elif kind=='governed_charter_settlement':
                    normative.require(c is not None and c['day']==when and not c['settled'] and event==f'governed:settlement:{when}','settlement raw identity/order differs')
                    wanted=settlement_record(when,c,p['value'] if p else None,out['value'] if out else None)
                    normative.require(normative.canonical(value)==normative.canonical(wanted),'raw settlement differs from persisted participation/resource budget')
                    expected['charter']['settled']=True
                    for key in ('reserved_hours','used_hours','released_idle_hours'):totals['operating_governance_'+key]+=wanted[key]
                else:raise ValueError('unknown governed journal record')
            elif kind=='day_end':
                value=json.loads(payload);c=expected['charter']
                normative.require(when==endings and c is not None and c['day']==when and c['settled'],'governed committed day missing charter/settlement')
                normative.require(normative.canonical(value.get('governed_operating_request_state'))==normative.canonical(expected),'governed cache differs from original proposal/outcome/consumed/charter journal')
                normative.require(pending_context is None,'unconsumed input context at complete day')
                proposal=expected['proposals'];outcome=expected['outcomes']
                today=proposal is not None and proposal['value']['observation_day']==when
                if full_model or actual_votes:
                    wanted=[{'proposal_id':proposal['id'],**vote} for vote in outcome['value']['ballot']['votes']] if today and outcome is not None and outcome['value']['ballot'] is not None else []
                    normative.require(normative.canonical(actual_votes)==normative.canonical(wanted),'actual operating_vote rows differ from persisted full ballot')
                if full_model:
                    normative.require(all(key in value for key in ('enterprise_growth_state','metrics_cumulative','cash')),'full Model raw day fields missing')
                    normative.require(market_begin is not None and market_begin['day']==when,'full Model day missing actual operating context')
                    if today:normative.require(proposal['value']['observations']['growth']==value['enterprise_growth_state'] and proposal['value']['observations']['active']==len(eligible),'proposal growth/roster differs from actual Model day settlement')
                    normative.require(set(m['person'] for m in expected['charter']['members']).issubset(eligible),'actual charter contains foreign identities')
                    previous_growth=value['enterprise_growth_state']
                actual_votes=[];market_begin=None
                if 'metrics_cumulative' in value:
                    for key,total in totals.items():
                        actual=value['metrics_cumulative'].get(key,0.);normative.number(actual)
                        normative.require(actual==total,'governed metric differs from original journal: '+key)
                endings+=1
        normative.require(endings==day and (day==0 or latest is not None and latest[1]==day-1 and latest[2]=='day_end'),'governed checkpoint lacks complete journal/day_end')
        normative.require(normative.canonical(expected)==normative.canonical(self.state),'governed header differs from last original journal records')

def quote_request(growth,*,scope,guild,day,cash,active,payroll_per_member,operating,expansion_allowed):
    if not operating or not expansion_allowed or growth.last_observed_day is None or growth.pending_construction is not None or growth.pending_hiring is not None:return None
    try:return normative.quote_observed_request(growth,scope=scope,guild=guild,observation_day=day,cash=cash,active=active,
        payroll_per_member=payroll_per_member,operating=operating,expansion_allowed=expansion_allowed)
    except normative.Refused as error:
        if str(error)=='original backend refused proposal quality/funding/calendar':return None
        raise
