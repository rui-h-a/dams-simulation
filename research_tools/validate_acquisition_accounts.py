"""Independent journal reconciliation of the candidate's finite M&A accounts.

No Model, acquisition transition, or growth controller is constructed or called.
This complementary check verifies contractual stock conservation and paid
integration from source terms and original events. It is not an independent
implementation of the whole behavioural model or a claim of external validity.
"""
from __future__ import annotations
from dataclasses import asdict
from itertools import groupby
import json
import math
from pathlib import Path
import sqlite3

from dams_sim.longitudinal import GregorianClock
from research_tools.validate_longitudinal import iter_journal_rows


def close(a,b,label):
    if type(a) not in (int,float) or not math.isfinite(a) or not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-8):
        raise ValueError('independent acquisition account differs: '+label)


def verify_acquisition_accounts(config,state,database):
    c=config.enterprise_acquisition
    if c is None:raise ValueError('independent acquisition accounts require explicit M&A configuration')
    terms={t.key:t for t in c.targets};clock=GregorianClock(config.longitudinal)
    stocks={key:{'owner':t.seller,'status':'available','closed_day':None,'ready_day':None,
        'transferred_cash':0.,'assumed_debt':0.,'consideration_paid':0.,'financing':0.,
        'transaction_paid':0.,'integration_paid':0.,'evidence_paid':0.,
        'target_cash_remaining':t.cash_resource_units,'seller_consideration_received':0.,
        'integration_workdays':0,'unfunded_workdays':0,'evidence_workdays':0,
        'employee_status':{p.key:'target' for p in t.employees},'employee_ids':{},
        'retained_evidence':list(asdict(t)['evidence']),
        'pending_evidence':list(asdict(t)['pending_evidence']),'resolved_evidence':[]} for key,t in terms.items()}
    employee_ids=set();previous=None;daily_count=0;closed=integrated=failed=0
    funded_days={key:[] for key in terms};parent_cash_delta=0.;lender_cash_outflow=0.;provider_cash_received=0.
    chronological_cash=[]
    connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    journal=iter_journal_rows(connection)
    try:
        for day,group in groupby(journal,key=lambda row:row[0]):
            if day!=daily_count:raise ValueError('independent acquisition raw day coverage differs')
            daily_count+=1;records=[(kind,ident,json.loads(payload)) for _,kind,ident,payload in group]
            ends=[v for kind,_,v in records if kind=='day_end']
            if len(ends)!=1 or records[-1][0]!='day_end':raise ValueError('independent acquisition committed day boundary differs')
            end=ends[0];working=clock.is_workday(day)
            operating=end['closed_day'] is None and end['suspended_day'] is None
            events=[(kind,v) for kind,_,v in records if kind.startswith('acquisition_')]
            allowed={'acquisition_close','acquisition_integration_paid','acquisition_integration_stalled',
                'acquisition_integration_failed','acquisition_integration_complete',
                'acquisition_integration_departure','acquisition_entry','acquisition_evidence_quarantined',
                'acquisition_inventory_refused'}
            if any(kind not in allowed or v.get('target') not in terms for kind,v in events):
                raise ValueError('independent acquisition event type/target differs')
            # Financial events are replayed in their original journal sequence,
            # across all targets. Yesterday's cash is only the opening balance;
            # an earlier same-day review or integration payment is not spendable
            # again by a later buyer. No producer transition is called here.
            cash_now=previous['cash'] if previous is not None else config.n*config.longitudinal.initial_cash_per_member
            debt_now=previous['enterprise_operating_state']['debt'] if previous is not None else 0.
            financing_at_close={}
            for kind,v in events:
                t=terms[v['target']];s=stocks[t.key];cash_before=cash_now
                if kind=='acquisition_close':
                    if previous is None:raise ValueError('independent acquisition lacks prior realized history')
                    old=previous['enterprise_growth_state']
                    reserve=max(old['cost_signal'],old['target'])*c.reserve_workdays+len(t.employees)*c.integration_workdays*(c.integration_resource_units_per_employee_workday+c.integration_payroll_resource_units_per_employee_workday)
                    finance=max(0.,t.consideration_resource_units+c.transaction_resource_units+reserve-cash_now)
                    close(v['financing'],finance,'chronological lender advance')
                    if finance>c.maximum_acquisition_financing_resource_units or debt_now+t.financial_debt_resource_units+finance>c.maximum_consolidated_debt_resource_units:
                        raise ValueError('independent acquisition chronological lender/debt bound differs')
                    financing_at_close[t.key]=finance
                    cash_now+=finance+t.cash_resource_units-t.consideration_resource_units-c.transaction_resource_units
                    debt_now+=finance+t.financial_debt_resource_units
                elif kind=='acquisition_integration_paid':
                    retained=sum(value=='target' for value in s['employee_status'].values())
                    due=len(t.employees)*c.integration_resource_units_per_employee_workday+retained*c.integration_payroll_resource_units_per_employee_workday
                    close(v['paid'],due,'integration systems cost plus retained payroll')
                    cash_now-=due
                elif kind=='acquisition_evidence_quarantined':
                    if not s['pending_evidence']:raise ValueError('independent acquisition review has no pending source evidence')
                    due=len(s['pending_evidence'])*c.evidence_review_resource_units_per_record
                    close(v['paid'],due,'chronological evidence review')
                    cash_now-=due
                else:continue
                if not math.isfinite(cash_now) or cash_now < -1e-8:
                    raise ValueError('independent acquisition chronological cash overdraft')
                chronological_cash.append({'day':day,'event':kind,'target':t.key,'cash_before':cash_before,
                    'cash_after':cash_now,**({'lender_advance':financing_at_close[t.key]} if kind=='acquisition_close' else {})})
            for key,t in terms.items():
                s=stocks[key];mine=[(k,v) for k,v in events if v['target']==key]
                if s['status']=='available' and day>=t.expires_day:s['status']='expired'
                bykind={kind:[v for k,v in mine if k==kind] for kind in allowed}
                for kind in allowed-{'acquisition_entry','acquisition_integration_departure'}:
                    if len(bykind[kind])>1:raise ValueError('independent acquisition event replayed')
                if bykind['acquisition_inventory_refused']:
                    v=bykind['acquisition_inventory_refused'][0];g=config.enterprise_growth
                    old=previous['enterprise_growth_state'] if previous is not None else {'capacity':g.initial_organization_capacity,'guilds':config.guilds,'departments':g.initial_departments,'sites':config.sites,'target':config.n}
                    proposed={'capacity':old['capacity']+t.installed_seats,'guilds':old['guilds']+t.guilds,'departments':old['departments']+t.departments,'sites':old['sites']+t.sites,'target':old['target']+len(t.employees)}
                    invalid=(proposed['capacity']>g.maximum_organization_capacity or proposed['target']>proposed['capacity'] or proposed['guilds']>max(config.guilds,math.ceil(g.maximum_organization_capacity/g.members_per_guild)) or proposed['sites']>max(config.sites,math.ceil(g.maximum_organization_capacity/g.members_per_site)) or proposed['departments']>max(g.initial_departments,math.ceil(proposed['guilds']/g.guilds_per_department)) or proposed['capacity']>proposed['guilds']*g.members_per_guild or proposed['capacity']>proposed['sites']*g.members_per_site or proposed['guilds']>proposed['departments']*g.guilds_per_department)
                    if s['status']!='available' or not invalid or v['proposed_installed_inventory']!=proposed or v['money_paid']!=0. or v['ownership_changed'] is not False:
                        raise ValueError('independent acquisition refused inventory differs')
                if bykind['acquisition_close']:
                    v=bykind['acquisition_close'][0]
                    if s['status']!='available' or not t.available_day<=day<t.expires_day or not working or previous is None:
                        raise ValueError('independent acquisition ownership/date differs')
                    if not operating or not previous['enterprise_growth_state']['observed_workdays']:
                        raise ValueError('independent acquisition buys without realized operating history')
                    old=previous['enterprise_growth_state'];margin=(old['revenue_signal']-old['cost_signal'])/max(old['revenue_signal'],old['cost_signal'],1e-12)
                    close(v['past_margin'],margin,'past margin');close(v['cash_received'],t.cash_resource_units,'target treasury')
                    close(v['consideration'],t.consideration_resource_units,'seller consideration');close(v['debt_assumed'],t.financial_debt_resource_units,'assumed liability')
                    close(v['transaction_cost'],c.transaction_resource_units,'transaction fee')
                    reserve=max(old['cost_signal'],old['target'])*c.reserve_workdays+len(t.employees)*c.integration_workdays*(c.integration_resource_units_per_employee_workday+c.integration_payroll_resource_units_per_employee_workday)
                    close(v['reserve'],reserve,'full paid integration reserve')
                    finance=financing_at_close[key]
                    if margin<c.minimum_past_margin:
                        raise ValueError('independent acquisition affordability/debt bound differs')
                    if v['seller']!=t.seller or v['owner_after']!='parent' or v['evidence_unit_conversion'] is not False or v['employees']!=len(t.employees):
                        raise ValueError('independent acquisition contract identity differs')
                    assets={'seats':t.installed_seats,'guilds':t.guilds,'departments':t.departments,'sites':t.sites}
                    if v['installed_assets']!=assets:raise ValueError('independent acquisition owned asset inventory differs')
                    s.update(owner='parent',status='integrating',closed_day=day,
                        transferred_cash=t.cash_resource_units,assumed_debt=t.financial_debt_resource_units,
                        consideration_paid=t.consideration_resource_units,transaction_paid=c.transaction_resource_units,
                        financing=finance,target_cash_remaining=0.,seller_consideration_received=t.consideration_resource_units)
                    parent_cash_delta+=finance+t.cash_resource_units-t.consideration_resource_units-c.transaction_resource_units
                    lender_cash_outflow+=finance;provider_cash_received+=c.transaction_resource_units;closed+=1
                if bykind['acquisition_integration_complete']:
                    v=bykind['acquisition_integration_complete'][0]
                    if s['status']!='integrating' or s['integration_workdays']!=c.integration_workdays or day<=max(funded_days[key]) or not operating:
                        raise ValueError('independent acquisition installs without earlier paid integration')
                    if v['assets']!={'seats':t.installed_seats,'guilds':t.guilds,'departments':t.departments,'sites':t.sites} or v['mode']!=c.integration_mode or v['evidence_unit_conversion'] is not False:
                        raise ValueError('independent acquisition usable assets/mode differs')
                    retained=sum(value=='target' for value in s['employee_status'].values())
                    if v['employees_transferred']!=retained or len(bykind['acquisition_entry'])!=retained:
                        raise ValueError('independent acquisition workforce transfer conservation differs')
                    s.update(status='integrated',ready_day=day);integrated+=1
                if bykind['acquisition_integration_paid']:
                    v=bykind['acquisition_integration_paid'][0]
                    if s['status']!='integrating' or not working or not operating:raise ValueError('independent acquisition payment/work calendar differs')
                    employees=sum(value=='target' for value in s['employee_status'].values())
                    due=len(t.employees)*c.integration_resource_units_per_employee_workday+employees*c.integration_payroll_resource_units_per_employee_workday
                    close(v['paid'],due,'integration systems cost plus retained payroll')
                    s['integration_paid']+=due;s['integration_workdays']+=1;s['unfunded_workdays']=0
                    if v['workdays']!=s['integration_workdays'] or s['integration_workdays']>c.integration_workdays:
                        raise ValueError('independent acquisition paid integration duration differs')
                    parent_cash_delta-=due;provider_cash_received+=due;funded_days[key].append(day)
                for v in bykind['acquisition_integration_departure']:
                    if not bykind['acquisition_integration_paid'] or s['employee_status'].get(v['person_key'])!='target':
                        raise ValueError('independent acquisition employee departure replayed')
                    s['employee_status'][v['person_key']]='departed'
                if bykind['acquisition_integration_stalled']:
                    v=bykind['acquisition_integration_stalled'][0]
                    if s['status']!='integrating' or not working or bykind['acquisition_integration_paid']:
                        raise ValueError('independent acquisition stalled day differs')
                    due=len(t.employees)*c.integration_resource_units_per_employee_workday+sum(value=='target' for value in s['employee_status'].values())*c.integration_payroll_resource_units_per_employee_workday
                    close(v['due'],due,'unfunded integration obligation');s['unfunded_workdays']+=1
                    if v['unfunded_workdays']!=s['unfunded_workdays']:raise ValueError('independent acquisition unfunded counter differs')
                if bykind['acquisition_integration_failed']:
                    v=bykind['acquisition_integration_failed'][0]
                    if s['status']!='integrating' or not bykind['acquisition_integration_stalled'] or s['unfunded_workdays']<c.maximum_unfunded_workdays:
                        raise ValueError('independent acquisition failure trigger differs')
                    if v['departed']!=sum(value=='target' for value in s['employee_status'].values()) or v['assets_owned_but_idle'] is not True or v['historical_evidence_retained'] is not True:
                        raise ValueError('independent acquisition failed inventory conservation differs')
                    s['employee_status']={p:'departed' for p in s['employee_status']};s['status']='failed';failed+=1
                for v in bykind['acquisition_entry']:
                    person=v['source_person'];profile=next((p for p in t.employees if p.key==person),None)
                    if s['status']!='integrated' or s['ready_day']!=day or s['employee_status'].get(person)!='target' or profile is None or v['profile']!=asdict(profile) or v['historical_credit_added']!=0.:
                        raise ValueError('independent acquisition employee identity/profile differs')
                    if type(v['person']) is not int or v['person']<config.n or v['person'] in employee_ids:
                        raise ValueError('independent acquisition parent identity reused')
                    employee_ids.add(v['person']);s['employee_status'][person]='parent';s['employee_ids'][person]=v['person']
                if s['status']=='integrated' and working and operating and s['pending_evidence']:s['evidence_workdays']+=1
                if bykind['acquisition_evidence_quarantined']:
                    v=bykind['acquisition_evidence_quarantined'][0]
                    if s['status']!='integrated' or s['evidence_workdays']<c.evidence_resolution_workdays or not working or not operating:
                        raise ValueError('independent acquisition unresolved evidence delay differs')
                    due=len(s['pending_evidence'])*c.evidence_review_resource_units_per_record
                    close(v['paid'],due,'evidence review');close(v['parent_credit_added'],0.,'incompatible authority conversion')
                    if v['records']!=len(s['pending_evidence']):raise ValueError('independent acquisition evidence roster differs')
                    s['evidence_paid']+=due;s['resolved_evidence']=s['pending_evidence'];s['pending_evidence']=[]
                    parent_cash_delta-=due;provider_cash_received+=due
                current=end['enterprise_acquisition_state']['targets'][key]
                if any(current.get(field)!=value for field,value in s.items()):
                    raise ValueError('independent acquisition daily stock differs: '+key)
            previous=end
        if daily_count!=state['day']:raise ValueError('independent acquisition horizon differs')
        if previous is None or previous['enterprise_acquisition_state']!=state['enterprise_acquisition_state']:
            raise ValueError('independent acquisition final committed stock differs')
        sellers=math.fsum(s['seller_consideration_received'] for s in stocks.values())
        target_delta=math.fsum(s['target_cash_remaining']-terms[key].cash_resource_units for key,s in stocks.items())
        close(parent_cash_delta+target_delta+sellers+provider_cash_received-lender_cash_outflow,0.,'parent/target/seller/provider/lender total cash')
    finally:
        try:journal.close()
        finally:connection.close()
    return {'scope':'independent journal financial/inventory accounting; not an independent whole ABM',
        'days':daily_count,'companies_closed':closed,'companies_integrated':integrated,'companies_failed':failed,
        'unique_transferred_people':len(employee_ids),'parent_transaction_cash_delta':parent_cash_delta,
        'seller_consideration_received':sellers,'external_provider_receipts':provider_cash_received,
        'lender_cash_advanced':lender_cash_outflow,'conserved_acquisition_cash':True,
        'chronological_cash_events':chronological_cash}
