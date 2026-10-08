"""Strict schema-3 raw/publication validation. Pure I/O; no Model construction.

Checks rebuild the declared inventory, full Gregorian daily roster, window
integrals, paired endpoints, planning protocol and intervals from original raw.
JSON/SQLite bytes and semantic hashes are separate; neither is replaced by a
stage's self-reported counts. A partial task cannot pass the complete gate.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from dams_sim.config import Config
from dams_sim.longitudinal import GregorianClock
from dams_sim.longitudinal_design import PRIMARY_ENDPOINTS,resolve_longitudinal_spec,freeze_precision,declared_contrasts
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.longitudinal_storage import integer_key_map,snapshot_semantics
from dams_sim.longitudinal_outputs import INTEGRALS,build_tables,interval_table,iter_timeseries,snapshot_rows
from dams_sim.longitudinal_pipeline import stage_inventory,driver_hash
from dams_sim.spec import case_key
from dams_sim.storage import canonical,digest,file_digest,source_hash,verify_outputs
from research_tools import longitudinal_derived as _derived


VALIDATOR_IMPORT_SHA256=file_digest(Path(__file__))
SOURCE_IMPORT_SHA256=source_hash()


def _require_current_validator():
    if file_digest(Path(__file__))!=VALIDATOR_IMPORT_SHA256:
        raise ValueError('validator source changed after import')
    if file_digest(Path(_derived.__file__))!=_derived.IMPORTED_MODULE_SHA256:
        raise ValueError('derived semantic source changed after import')
    if source_hash()!=SOURCE_IMPORT_SHA256:
        raise ValueError('validator scientific source changed after import')


def _unique_json_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('raw journal duplicate JSON key: '+key)
        value[key]=item
    return value


def _adoption_csv(values,guilds,day):
    """Match the original numeric-key CSV, independently of journal codec.

    V2 journals sort string keys while retained CSV/history sorts integer keys.
    Reconstruct only legal, unique IDs; the exact CSV/history bytes still bind.
    """
    typed=integer_key_map(values,name='daily.adoption_days')
    if set(typed)!=set(range(guilds)):raise ValueError('daily adoption guild roster differs')
    if any(value is not None and (type(value) is not int or not 0<=value<=day) for value in typed.values()):
        raise ValueError('daily adoption date differs')
    return canonical(typed).decode()


def _publication_tree(pub):
    if pub.is_symlink() or not pub.is_dir() or any(path.is_symlink() for path in pub.rglob('*')):
        raise ValueError('publication tree is absent or symlinked')


def _csv_bytes(rows):
    if not rows:return b''
    fields=list(dict.fromkeys(k for row in rows for k in row));out=io.StringIO(newline='')
    writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    return out.getvalue().encode()


class _RawDailyAccounting:
    """Event-to-day accounting, not a rerun or a claim of empirical truth.

    The frozen journal records work and votes, but no separate leave/time-booking
    event. With declared zero leave the lifecycle/calendar gives the full present
    roster, including tail and zero-funding days. Otherwise work identifies that
    roster only on funded creation days. Unobserved individual attendance is not
    invented. Branch prefixes use their parent config when supplied by the study.
    """
    supported=frozenset(('calendar_days_observed','calendar_workdays_observed',
        'active_member_calendar_days','active_member_workdays','available_member_work_hours',
        'observation_available_member_work_hours','tail_available_member_work_hours',
        'post_adoption_available_member_work_hours','present_member_workdays','present_member_work_hours',
        'recruitment_resource_units','entrants','retirements','exits','memory_loss_units','migration_resource_units',
        'operating_resource_units','operating_unfunded_resource_units','payroll_resource_units',
        'maintenance_resource_units','dual_run_resource_units','decisions_completed','decisions_unresolved',
        'decision_errors','decision_regret_units','work_events','fraudulent_records_submitted','censored_records',
        'produced_work_units','effort_hours','cooperation_units','revenue_resource_units','training_hours',
        'attack_hours_consumed','attack_budget_hours',
        'governance_hours','review_hours','verification_resource_units','reviews_rejected','appeal_hours',
        'appeals_denied','confirmed_records','fraudulent_records_accepted','corrections'))
    def __init__(self,config,state,config_history=()):
        self.config=config;self.state=state;self.config_history=config_history
        self.people={p['id']:p for p in state['people']}
        self.tokens={p['token']:p for p in state['people']}
        self.agents={a['id']:a for a in state['agents']}
        self.members={};self.alias={g:g for g in range(config.guilds)}
        self.adoption={str(g):None for g in range(config.guilds)}
        self.closed=None;self.suspended=None;self.cash=config.n*config.longitudinal.initial_cash_per_member
        self.metrics=defaultdict(float)
        self.previous_reported={};self.unfunded_workdays=0
        self.seen_people=set();self.person_exposure={i:defaultdict(float) for i in self.people}
        self.unknown_present=set()

    def at(self,day):
        return next((config for end,config in self.config_history if day<end),self.config)

    @staticmethod
    def number(value,label,low=0.,high=math.inf):
        if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError('raw event invalid '+label)
        return value

    def source_domain(self,event,day):
        """Retained source-domain ID; queue causality is not a queue replay."""
        try:
            if not event.startswith('work:'):raise ValueError
            created,rest=event[5:].split(':',1);token,guild=rest.rsplit(':g:',1)
            created=int(created);guild=int(guild);person=self.tokens[token]
            if not person['entered_day']<=created<=day or person['exited_day'] is not None and created>=person['exited_day'] or guild not in self.alias:
                raise ValueError
            if event!=f'work:{created}:{token}:g:{guild}':raise ValueError
        except (ValueError,KeyError):raise ValueError('raw procedure source event identity differs') from None
        return guild,person['id']

    def verify(self,day,events,record):
        p=self.at(day);l=p.longitudinal;clock=GregorianClock(l);before=dict(self.metrics)
        working=clock.is_workday(day);creating=l.work_creation_stop_day is None or day<l.work_creation_stop_day
        if l.closure_day==day:self.closed=day
        if l.suspension_day==day:self.suspended=day
        operating=self.closed is None and self.suspended is None
        reported=record['metrics_cumulative'];daily=record['metrics_daily']
        for key in set(reported)|set(daily)|set(self.previous_reported):
            current=self.number(reported.get(key,0.),'cumulative metric '+key,low=-math.inf)
            change=self.number(daily.get(key,0.),'daily metric '+key,low=-math.inf)
            if change!=current-self.previous_reported.get(key,0.):
                raise ValueError('raw metric cumulative difference differs: '+key)
        work=[];votes={};decisions={};reviews=[];appeals=[];commits=[];auto_closure=False
        for kind,event,value in events:
            if kind=='entry':
                i=value['person'];person=self.people.get(i)
                if i in self.seen_people or person is None or person['entered_day']!=day or person['token']!=event:
                    raise ValueError('raw entry/person lifecycle differs')
                initial=i<p.n
                if type(value['initial']) is not bool or value['initial']!=initial or initial and (day!=0 or event!=f'initial:{i}') or value['slot']!=person['slot']:
                    raise ValueError('raw entry initial identity/slot differs')
                self.seen_people.add(i)
                self.members[i]=self.alias[value['guild']]
                if not value['initial']:
                    self.cash-=l.recruitment_resource_units_per_person
                    self.metrics['recruitment_resource_units']+=l.recruitment_resource_units_per_person
                    self.metrics['entrants']+=1
            elif kind in {'exit','retirement','layoff'}:
                i=value['person']
                if i not in self.members or self.people[i]['exited_day']!=day or self.people[i]['exit_reason']!=kind:
                    raise ValueError('raw exit/person lifecycle differs')
                del self.members[i];self.metrics['retirements' if kind=='retirement' else 'exits']+=1
                self.metrics['memory_loss_units']+=self.number(value['memory_loss'],'memory loss')
            elif kind=='guild_merger':
                a,b=value['source'],value['target']
                for g in self.alias:
                    if self.alias[g]==a:self.alias[g]=b
                for i in self.members:
                    if self.members[i]==a:self.members[i]=b
            elif kind=='guild_move':
                i=value['person']
                if self.members.get(i)!=value['old']:raise ValueError('raw guild move differs from roster')
                self.members[i]=value['new']
            elif kind=='adoption':
                g=value['guild'];cost=sum(v==g for v in self.members.values())*l.migration_resource_units_per_member
                if self.adoption[str(g)] is not None or value['migration_cost']!=cost:
                    raise ValueError('raw adoption migration cost differs')
                self.adoption[str(g)]=day;self.cash-=cost;self.metrics['migration_resource_units']+=cost
            elif kind=='closure':
                if self.closed is not None or auto_closure or value['cause']!='consecutive_unfunded_workdays':
                    raise ValueError('raw automatic closure differs')
                auto_closure=True;closure_record=value
            elif kind=='work':
                if event!=f"work:{day}:{self.people[value['person']]['token']}:g:{value['guild']}":raise ValueError('raw work event identity differs')
                work.append(value)
            elif kind=='vote':
                i=value['person']
                if i in votes:raise ValueError('raw duplicate person vote')
                if event!=f"vote:{day}:{self.people[i]['token']}:g:{value['guild']}":raise ValueError('raw vote event identity differs')
                votes[i]=value
            elif kind=='decision':
                g=value['guild']
                if g in decisions:raise ValueError('raw duplicate guild decision')
                if event!=f'decision:{day}:{g}':raise ValueError('raw decision event identity differs')
                decisions[g]=value
            elif kind=='review':reviews.append((event,value))
            elif kind=='appeal':appeals.append((event,value))
            elif kind=='commit':commits.append((event,value))
        active=sorted(self.members) if self.closed is None and self.suspended is None else []
        if record['active']!=len(active):raise ValueError('raw lifecycle active exposure differs')
        self.metrics['calendar_days_observed']+=1
        if working:self.metrics['calendar_workdays_observed']+=1
        self.metrics['active_member_calendar_days']+=len(active)
        for i in active:self.person_exposure[i]['active_calendar_days']+=1
        if working:
            self.metrics['active_member_workdays']+=len(active)
            for i in active:
                available=max(0.,1-self.agents[i]['care_hours'])
                self.metrics['available_member_work_hours']+=available
                self.person_exposure[i]['active_workdays']+=1;self.person_exposure[i]['available_work_hours']+=available
                self.metrics['observation_available_member_work_hours' if creating else 'tail_available_member_work_hours']+=available
                if self.adoption[str(self.members[i])] is not None:
                    self.metrics['post_adoption_available_member_work_hours']+=available
                    self.person_exposure[i]['post_adoption_available_work_hours']+=available
        # Exposure is measured before funding may close the organization.
        n=record['metrics_daily'].get('present_member_workdays',0.)
        if type(n) not in (int,float) or n!=int(n) or not 0<=n<=len(active) or not working and n:
            raise ValueError('raw present exposure outside lifecycle/calendar bounds')
        present=active if working and l.annual_leave_workdays==0 else None
        if not working or not active:present=[]
        if present is not None and len(present)!=n:raise ValueError('raw present exposure differs from zero-leave calendar')
        if present is None and work:present=sorted(v['person'] for v in work)
        if present is not None:
            if len(present)!=len(set(present)) or any(i not in active for i in present) or len(present)!=n:
                raise ValueError('raw work/present roster differs')
            self.metrics['present_member_workdays']+=len(present)
            for i in present:
                self.metrics['present_member_work_hours']+=max(0.,1-self.agents[i]['care_hours'])
                self.person_exposure[i]['present_workdays']+=1
        else:self.unknown_present.update(active)
        training={i:l.training_hours_per_workday if self.adoption[str(self.members[i])] is not None and day-self.adoption[str(self.members[i])]<l.transition_days else 0. for i in present or []}
        if present is not None:
            trainees=sum(v>0 for v in training.values())
            due=n*(l.payroll_resource_units_per_member_workday+l.maintenance_resource_units_per_member_workday)+trainees*l.dual_run_resource_units_per_member_workday
            funding=min(1.,self.cash/due) if due else 1.;paid=due*funding;self.cash-=paid
            for key,value in (('operating_resource_units',paid),('operating_unfunded_resource_units',due-paid),
                ('payroll_resource_units',n*l.payroll_resource_units_per_member_workday*funding),
                ('maintenance_resource_units',n*l.maintenance_resource_units_per_member_workday*funding),
                ('dual_run_resource_units',trainees*l.dual_run_resource_units_per_member_workday*funding)):
                self.metrics[key]+=value
        else:
            # No event identifies each absent person on zero-funding/tail days.
            # Retain that declared exposure, checking the paid component identity.
            daily=record['metrics_daily'];paid=daily.get('operating_resource_units',0.)
            components=sum(daily.get(k,0.) for k in ('payroll_resource_units','maintenance_resource_units','dual_run_resource_units'))
            if not math.isclose(paid,components,rel_tol=1e-12,abs_tol=1e-8):raise ValueError('raw operating paid components differ')
            self.cash-=paid;funding=0. if self.cash==0 and creating else None
        if working and operating:
            if funding is None:self.unfunded_workdays=None
            elif funding==1:self.unfunded_workdays=0
            elif self.unfunded_workdays is not None:self.unfunded_workdays+=1
            if self.unfunded_workdays is not None and auto_closure!=(self.unfunded_workdays>=l.closure_after_unfunded_workdays):
                raise ValueError('raw closure differs from consecutive unfunded workdays')
        elif auto_closure:raise ValueError('raw closure outside operating workday')
        if auto_closure and closure_record['cash']!=self.cash:raise ValueError('raw closure cash differs from paid costs')
        if auto_closure:self.closed=day
        if record['closed_day']!=self.closed or record['suspended_day']!=self.suspended or record['adoption_days']!=self.adoption:
            raise ValueError('raw event lifecycle/adoption differs from day end')
        observed_present=0 if auto_closure else n
        if record['present']!=observed_present:raise ValueError('raw day-end present count differs from pre-funding exposure')
        decision_day=creating and working and self.closed is None and self.suspended is None
        expected_guilds={g for g in self.alias if self.alias[g]==g} if decision_day else set()
        if set(decisions)!=expected_guilds or set(votes)!=(set(active) if decision_day else set()):
            raise ValueError('raw vote/decision daily roster differs')
        losses=[]
        for g,decision in sorted(decisions.items()):
            domain=[votes[i] for i in active if self.members[i]==g];effective=[];ballots=[];flags=[]
            for vote in domain:
                if vote['guild']!=g or type(vote['participates']) is not bool:raise ValueError('raw vote identity/participation differs')
                share=self.number(vote['formal_share'],'formal share',high=1.)
                signal=self.number(vote['observed_signal'],'vote signal',low=-math.inf)
                weight=share if vote['participates'] else 0.
                if vote['effective_weight']!=weight or vote['vote']!=float(signal>=0):raise ValueError('raw vote signal/weight differs')
                if vote['participates'] and present is not None and vote['person'] not in present:
                    raise ValueError('raw absent person participates')
                if vote['participates'] and funding==0:raise ValueError('raw unfunded person participates')
                effective.append(weight);ballots.append(vote['vote']);flags.append(vote['participates'])
            if domain and not math.isclose(math.fsum(v['formal_share'] for v in domain),1.,rel_tol=1e-12,abs_tol=1e-12):
                raise ValueError('raw formal allocation does not conserve guild mass')
            mass=math.fsum(effective);theta=self.number(decision['proposal_truth_research_only'],'proposal truth',low=-math.inf)
            accepted=math.fsum(w*v for w,v in zip(effective,ballots))/mass>=.5 if mass else None
            loss=abs(theta) if accepted is None or accepted!=(theta>=0) else 0.
            if decision['effective_mass']!=mass or decision['participation']!=sum(flags) or decision['loss']!=loss:
                raise ValueError('raw decision fields differ from recorded votes/truth')
            losses.append(loss);self.metrics['decisions_completed' if mass else 'decisions_unresolved']+=1
            if mass:self.metrics['decision_errors']+=int(loss>0)
        self.metrics['decision_regret_units']+=math.fsum(losses)
        should_work=creating and working and self.closed is None and self.suspended is None and (funding is None or funding>0)
        if not should_work and work or should_work and len(work)!=n:raise ValueError('raw work event daily roster differs')
        if [v['person'] for v in work]!=sorted(set(v['person'] for v in work)):
            raise ValueError('raw work person order/uniqueness differs')
        output=0.;efforts=[];cooperation=[];governance={}
        post_present=[] if auto_closure else present or []
        attack_active=creating and p.attack!='none' and p.attack_budget_hours_per_day>0 and p.attack_start_day<=day<p.attack_stop_day and bool(post_present)
        attack_slots=math.ceil(p.n*p.attack_cohort_fraction)
        attackers=[i for i in post_present if self.people[i]['slot']<attack_slots]
        attack_time=p.attack_budget_hours_per_day/len(attackers) if attack_active and attackers else 0.
        for value in work:
            i=value['person']
            if self.members.get(i)!=value['guild']:raise ValueError('raw work person/domain differs')
            output+=self.number(value['produced_research_only'],'produced work')
            efforts.append(self.number(value['effort'],'effort hours',high=1.))
            cooperation.append(self.number(value['cooperation'],'cooperation',high=1.))
            if funding is not None and (value['funding_fraction']!=funding or value['training']!=training[i]*funding):
                raise ValueError('raw work funding/training differs')
            if funding is not None:
                morning=value['morning_observed_state'];agent=self.agents[i]
                for key,high in (('share',1.),('trust',1.),('fatigue',1.),('learning',2.)):
                    self.number(morning[key],'morning '+key,high=high)
                original_cooperation=max(0.,min(1.,agent['reciprocity']*morning['trust']*(1-morning['fatigue'])))
                expected_cooperation=0. if attack_active and p.attack=='freeride' and i in attackers else original_cooperation
                if value['cooperation']!=expected_cooperation:raise ValueError('raw cooperation differs from morning evidence')
                reservations=.1*p.review_capacity_per_member_day+.15*p.appeal_capacity_per_member_day+.05*original_cooperation+training[i]
                governance[i]=.05 if (1-agent['care_hours']-reservations)*funding>=.05 else 0.
                if votes[i]['formal_share']!=morning['share'] or votes[i]['participates'] and not governance[i]:
                    raise ValueError('raw vote differs from morning work evidence/time capacity')
                attack=self.number(value['attack_hours'],'attack hours')
                if attack!=(attack_time if i in attackers else 0.):raise ValueError('raw work attack reservation differs from cohort/window')
                reserved=reservations*funding+governance[i]
                limit=max(0.,(1-agent['care_hours'])*funding-reserved)
                if attack>limit+1e-12 or value['effort']>limit-attack+1e-12 or agent['care_hours']+reserved+value['effort']+attack>1+1e-12:
                    raise ValueError('raw work exceeds funded individual time capacity')
                if value['effort']==0 and value['produced_research_only']!=0:raise ValueError('raw zero-effort work produces output')
            self.metrics['work_events']+=1
            for field,key in (('fraudulent','fraudulent_records_submitted'),('censored','censored_records')):
                if type(value[field]) is not bool:raise ValueError('raw work boolean differs')
                if value[field]:self.metrics[key]+=1
            if value['fraudulent']!=(attack_active and p.attack=='forge' and i in attackers) or value['censored'] and not (attack_active and p.attack=='censor' and i not in attackers):
                raise ValueError('raw work fraud/censorship differs from declared attack')
        self.metrics['produced_work_units']+=output
        self.metrics['effort_hours']+=math.fsum(efforts)
        consumed=attack_time*len(attackers) if attack_active else 0.
        self.metrics['attack_hours_consumed']+=consumed;self.metrics['attack_budget_hours']+=consumed
        # Every present person has a work record only during funded creation.
        if work or not observed_present:self.metrics['cooperation_units']+=math.fsum(cooperation)
        revenue=output*l.revenue_resource_units_per_work_unit;self.cash+=revenue;self.metrics['revenue_resource_units']+=revenue
        if present is not None:
            for i in ([] if auto_closure else present):self.metrics['training_hours']+=training[i]*funding
        if work or not observed_present or funding==0:
            for i in ([] if auto_closure else present or []):self.metrics['governance_hours']+=governance.get(i,0.)
        if (reviews or appeals) and (not observed_present or funding==0):raise ValueError('raw review/appeal lacks funded present capacity')
        if commits and not operating:raise ValueError('raw commit outside start-of-day operation')
        review_counts=Counter();appeal_counts=Counter()
        for event,value in reviews:
            source,_person=self.source_domain(event,day);domain=self.alias[source];review_counts[domain]+=1
            backend=p.backend if self.adoption[str(domain)] is not None else l.pre_adoption_backend
            if value['backend']!=backend:raise ValueError('raw review backend differs from source domain policy')
            if type(value['accepted']) is not bool or type(value['audit_detected']) is not bool:
                raise ValueError('raw review boolean differs')
            self.number(value['observed'],'review observation')
            if value['accepted']!=(value['observed']>=.25 and not value['audit_detected']):
                raise ValueError('raw review outcome differs from observed/audit evidence')
            self.metrics['review_hours']+=.1
            self.metrics['verification_resource_units']+=p.backend_review_multipliers[('central','witness','consensus').index(value['backend'])]-1
            if not value['accepted']:self.metrics['reviews_rejected']+=1
        for event,value in appeals:
            source,_person=self.source_domain(event,day);appeal_counts[self.alias[source]]+=1
            if type(value['detected']) is not bool:raise ValueError('raw appeal boolean differs')
            self.metrics['appeal_hours']+=.15
            if value['detected']:self.metrics['appeals_denied']+=1
        if present is not None:
            for domain in set(review_counts)|set(appeal_counts):
                size=sum(self.members[i]==domain for i in post_present)
                backend=p.backend if self.adoption[str(domain)] is not None else l.pre_adoption_backend
                unit=p.backend_review_multipliers[('central','witness','consensus').index(backend)]
                if review_counts[domain]>int(size*p.review_capacity_per_member_day*funding/unit) or appeal_counts[domain]>int(size*p.appeal_capacity_per_member_day*funding):
                    raise ValueError('raw review/appeal exceeds funded domain capacity')
        for event,value in commits:
            source,person=self.source_domain(event,day)
            if source!=value['guild'] or person!=value['person']:raise ValueError('raw commit source person/domain differs')
            if type(value['fraudulent']) is not bool or type(value['correction']) is not bool:raise ValueError('raw commit boolean differs')
            self.metrics['confirmed_records']+=1;self.metrics['fraudulent_records_accepted']+=int(value['fraudulent'])
            if value['correction']:self.metrics['corrections']+=1
        if record['output']!=output or record['cash']!=self.cash:raise ValueError('raw work/resource balance differs from day end')
        unsupported=set()
        if present is None:
            unsupported.update(('present_member_workdays','present_member_work_hours','training_hours',
                'operating_resource_units','operating_unfunded_resource_units','payroll_resource_units',
                'maintenance_resource_units','dual_run_resource_units'))
        if not work and observed_present:unsupported.add('cooperation_units')
        if not work and observed_present and funding!=0:unsupported.add('governance_hours')
        for key in self.supported:
            value=self.metrics.get(key,0.)
            if key in unsupported:continue
            if record['metrics_cumulative'].get(key,0.)!=value or record['metrics_daily'].get(key,0.)!=value-before.get(key,0.):
                raise ValueError('raw event daily/cumulative accounting differs: '+key)
        for key in unsupported:self.metrics[key]=record['metrics_cumulative'].get(key,0.)
        self.previous_reported=dict(reported)

    def finish(self):
        if self.seen_people!=set(self.people) or set(self.members)!=set(self.state['slots'].values()):
            raise ValueError('raw entry/final retained person roster differs')
        for i,person in self.people.items():
            for key in ('active_calendar_days','active_workdays','available_work_hours',
                        'post_adoption_available_work_hours','present_workdays'):
                if key=='present_workdays' and i in self.unknown_present:continue
                if person[key]!=self.person_exposure[i].get(key,0.):raise ValueError('raw individual exposure differs: '+key)


def verify_daily_evidence(path,config,state,database,*,config_history=()):
    """Stream CSV against both persisted state history and exact daily journal.

    This does not rerun a world. Underlying events independently reconcile the
    quantities their schema records; byte hashes remain an additional gate.
    """
    _require_current_validator()
    import sqlite3
    from itertools import zip_longest
    clock=GregorianClock(config.longitudinal)
    connection=sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro',uri=True)
    sentinel=object();last=None;count=0
    try:
        from itertools import groupby
        accounting=_RawDailyAccounting(config,state,config_history)
        journal=connection.execute('SELECT day,kind,event,payload FROM journal ORDER BY seq')
        events=groupby(journal,key=lambda r:r[0])
        for count,(row,stored,event) in enumerate(zip_longest(iter_timeseries(path),state['history'],events,fillvalue=sentinel),1):
            if any(v is sentinel for v in (row,stored,event)):raise ValueError('daily CSV/state/journal roster differs')
            day,group=event;daily=[];record=None
            for _,kind,ident,payload in group:
                if record is not None:raise ValueError('raw event follows day end')
                value=json.loads(payload,object_pairs_hook=_unique_json_object)
                if kind=='day_end':record=value
                else:daily.append((kind,ident,value))
            if record is None:raise ValueError('raw daily journal lacks day end')
            if day!=count-1 or row!=stored:raise ValueError('daily CSV differs from full retained state history')
            if row['day']!=day or row['calendar_date']!=record['date'] or record['date']!=clock.at(day).isoformat():
                raise ValueError('daily journal Gregorian date differs')
            if row['is_workday']!=clock.is_workday(day):raise ValueError('daily work calendar differs')
            direct={'active_members':'active','present_members':'present','cash_resource_units':'cash',
                    'output_work_units':'output','review_backlog_records':'review_backlog_records',
                    'appeal_backlog_records':'appeal_backlog_records'}
            if any(row[k]!=record[v] for k,v in direct.items()):raise ValueError('daily observed stock differs from journal')
            if row['closed']!=(record['closed_day'] is not None) or row['suspended']!=(record['suspended_day'] is not None):
                raise ValueError('daily closure/suspension differs from journal')
            if row['adoption_days']!=_adoption_csv(record['adoption_days'],config.guilds,day):raise ValueError('daily adoption differs from journal')
            creating=config.longitudinal.work_creation_stop_day is None or day<config.longitudinal.work_creation_stop_day
            if row['work_creation_enabled'] is not creating:raise ValueError('daily creation/tail scope differs')
            for key,value in row.items():
                for suffix,source in (('_daily','metrics_daily'),('_cumulative','metrics_cumulative')):
                    if key.endswith(suffix) and value!=record[source].get(key[:-len(suffix)],0):
                        raise ValueError('daily exposure/cost metric differs from journal: '+key)
            accounting.verify(day,daily,record)
            last=row
        if count!=config.days or state['day']!=config.days:raise ValueError('daily evidence incomplete/oversized horizon')
        accounting.finish()
    finally:connection.close()
    _require_current_validator()
    return last


def verify_summary_evidence(summary,config,state,database):
    """Bind all summary metrics and timing/lifecycle metadata to retained raw."""
    _require_current_validator()
    import sqlite3
    clock=GregorianClock(config.longitudinal)
    for key,value in state['metrics'].items():
        if summary.get(key)!=value:raise ValueError('summary metric differs from retained state: '+key)
    expected={'model_version':state['model_version'],'world':config.world,'regime':config.regime,
              'backend':config.backend,'attack':config.attack,'n':config.n,'days_completed':config.days,
              'calendar_start':config.longitudinal.calendar_start,'calendar_end_exclusive':clock.at(config.days).isoformat(),
              'ever_people':len(state['people']),'closure_day':state['closed_day'],'suspension_day':state['suspended_day'],
              'cash_resource_units':state['cash'],'adoption_days':state['adoption'],
              'active_members_final':len(state['slots']) if state['closed_day'] is None and state['suspended_day'] is None else 0,
              'branch_origin':state['branch_origin']}
    for key,value in expected.items():
        if summary.get(key)!=value:raise ValueError('summary identity/lifecycle differs from retained state: '+key)
    connection=sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro',uri=True)
    try:
        pending=dict(connection.execute('SELECT kind,count(*) FROM pending GROUP BY kind'))
        if summary['unfinished_records']!=pending.get('review',0)+pending.get('commit',0) or summary['unfinished_appeals']!=pending.get('appeal',0):
            raise ValueError('summary unfinished stocks differ from ledger')
    finally:connection.close()
    _require_current_validator()


class CheckedLongitudinalStudy:
    def __init__(self,root,*,publication_required=True,producer_in_progress=False,expected_provenance=None):
        _require_current_validator()
        self.root=Path(root).resolve();self.inputs={};self.cases={};self.observations=[];self.endpoints=[]
        self.pipeline=self.read_json('pipeline_manifest.json')
        if self.pipeline.get('schema_version')!=3:raise ValueError('not a longitudinal scientific version')
        if self.pipeline.get('status')!='complete' and not (producer_in_progress and self.pipeline.get('status')=='running' and self.pipeline.get('scientific_status')=='complete'):
            raise ValueError('pipeline is not complete or an explicitly completed scientific producer')
        self.spec=resolve_longitudinal_spec(self.pipeline['spec']['name'],self.pipeline['spec']['n'])
        if canonical(self.pipeline['spec'])!=canonical(self.spec.to_dict()) or self.pipeline['spec_sha256']!=self.spec.sha256:
            raise ValueError('resolved longitudinal scientific spec differs')
        self.identity={k:self.pipeline[k] for k in ('source_sha256','pipeline_driver_sha256','spec_sha256')}
        if self.identity['source_sha256']!=source_hash() or self.identity['pipeline_driver_sha256']!=driver_hash():
            raise ValueError('current longitudinal source/driver differs; use its exact source version')
        if canonical(self.read_json('spec_manifest.json'))!=canonical(self.spec.to_dict()):raise ValueError('spec manifest differs')
        self.base=Config.from_dict(self.pipeline['base_config'])
        self.protocol=self.read_json('protocol.json')
        if self.pipeline['protocol_sha256']!=self.inputs['protocol.json']:raise ValueError('protocol pointer digest differs')
        self._stage('precision-pilot',self.spec.pilot_ids(),expected_provenance)
        pilot=[{k:r[k] for k in ('contrast_id','endpoint','world','effect')} for r in self.endpoints if r['stage']=='precision-pilot' and r['primary']]
        rebuilt=freeze_precision(self.spec,pilot,pilot_inventory_sha256=digest(canonical(self.inventories['precision-pilot'])),source_sha256=self.identity['source_sha256'])
        rebuilt['pipeline_driver_sha256']=self.identity['pipeline_driver_sha256']
        if canonical(rebuilt)!=canonical(self.protocol) or self.protocol['status']!='frozen':raise ValueError('frozen precision protocol differs or refused')
        self._stage('confirmation',self.protocol['confirmation_world_ids'],expected_provenance)
        confirmation=[r for r in self.endpoints if r['stage']=='confirmation']
        self.paired_intervals=interval_table(self.spec,confirmation)
        if canonical(self.read_json('paired_intervals.json'))!=canonical(self.paired_intervals):raise ValueError('paired intervals differ from all assigned worlds')
        self.exact_csv('paired_intervals.csv',self.paired_intervals)
        self.analysis_manifest=self.read_json('analysis/manifest.json')
        if self.analysis_manifest.get('status')!='complete' or self.analysis_manifest.get('exit_code')!=0:raise ValueError('window analysis stage incomplete')
        for name,sha in self.analysis_manifest['input_sha256'].items():
            if self.read_hash(name)!=sha:raise ValueError('analysis input pointer differs')
        for name,sha in self.analysis_manifest['output_sha256'].items():
            if self.read_hash(str((Path('analysis')/name)))!=sha:raise ValueError('analysis output pointer differs')
        expected_precision=all(r['precision_met'] for r in self.paired_intervals if r['primary'])
        if self.pipeline['mc_precision_met'] is not expected_precision:raise ValueError('MC precision flag differs')
        stages=[('precision-pilot','precision-pilot/manifest.json'),('confirmation','confirmation/manifest.json'),
                ('longitudinal-analysis','analysis/manifest.json')]
        if publication_required:
            self._publication();stages.append(('publication','publication/generated/analysis/generation_manifest.json'))
        roster=[{'name':name,'manifest_sha256':self.read_hash(path)} for name,path in stages]
        if publication_required:
            if self.pipeline.get('stages')!=roster:raise ValueError('final stage roster/pointer hashes differ')
            total=sum(len(v) for v in self.inventories.values())
            if self.pipeline.get('unique_complete_cases')!=total or self.pipeline.get('logical_case_rows')!=total or self.pipeline.get('independent_primary_worlds')!=self.protocol['confirmation_worlds']:
                raise ValueError('final logical/unique/independent counts differ')
            if self.pipeline.get('exit_code')!=0:raise ValueError('final pipeline exit is not zero')
        elif self.pipeline.get('stages')[:3]!=roster:
            raise ValueError('scientific producer stage roster differs')
        _require_current_validator()

    def file(self,name):
        path=self.root/name
        if not path.resolve().is_relative_to(self.root) or path.is_symlink() or not path.is_file():raise ValueError('input absent or unsafe: '+str(name))
        return path.resolve()

    def read_hash(self,name):
        path=self.file(name);relative=str(path.relative_to(self.root));sha=file_digest(path);self.inputs[relative]=sha;return sha

    def read_json(self,name):
        self.read_hash(name);return json.loads(self.file(name).read_text())

    def exact_csv(self,name,rows):
        self.read_hash(name)
        if self.file(name).read_bytes()!=_csv_bytes(rows):raise ValueError('CSV differs from rebuilt raw table: '+str(name))

    def _case(self,row,reference,expected_provenance,config_history=()):
        config=Config.from_dict(row['config']);ident=row['case_id']
        if case_key(config)!=ident:raise ValueError('scientific case ID differs')
        attempt=self.root/reference['attempt']
        if not attempt.resolve().is_relative_to(self.root/'cases'/ident):raise ValueError('case attempt outside its identity directory')
        m=self.read_json(str(attempt.relative_to(self.root)/'manifest.json'))
        if m.get('status')!='complete' or m.get('exit_code')!=0 or m.get('scientific_case_id')!=ident:
            raise ValueError('declared case is not complete')
        if canonical(m['config'])!=canonical(config.to_dict()) or m['config_sha256']!=digest(canonical(config.to_dict())):
            raise ValueError('complete case config differs')
        if any(m.get(k)!=v for k,v in self.identity.items() if k!='spec_sha256'):raise ValueError('complete case source/driver differs')
        if self.read_hash(str(attempt.relative_to(self.root)/'manifest.json'))!=reference['manifest_sha256']:raise ValueError('case manifest pointer differs')
        for key,value in (expected_provenance or {}).items():
            if m.get('execution_provenance',{}).get(key)!=value:raise ValueError('actual execution provenance differs: '+key)
        required=('summary.json','timeseries.csv','final_state.json','final_state.sqlite','report.md','report.svg','checkpoint-index.json','.longitudinal-working.sqlite')
        verify_outputs(attempt,m,required=required)
        index=self.read_json(str(attempt.relative_to(self.root)/'checkpoint-index.json'))
        if not 1<=len(index.get('snapshots',[]))<=2 or index['source_sha256']!=self.identity['source_sha256'] or index['config_sha256']!=m['config_sha256']:
            raise ValueError('checkpoint index identity/retention differs')
        allowed=set(required);days=[];final_envelope=None
        for descriptor in [m['final_state_descriptor'],*index['snapshots']]:
            parts=descriptor.get('files')
            if not isinstance(parts,list) or len(parts)!=2 or {p['file'] for p in parts}!={descriptor['file'],str(Path(descriptor['file']).with_suffix('.sqlite'))}:
                raise ValueError('snapshot group descriptor differs')
            for part in parts:
                path=attempt/part['file'];allowed.add(part['file'])
                if path.parent!=attempt or self.read_hash(str(path.relative_to(self.root)))!=part['sha256'] or path.stat().st_size!=part['bytes']:
                    raise ValueError('snapshot group bytes differ')
            if file_digest(attempt/descriptor['file'])!=descriptor['sha256']:raise ValueError('descriptor pointer digest differs')
            envelope,_=verify_snapshot(attempt/descriptor['file'],expected_config=config)
            if envelope['state']['day']!=descriptor['day'] or envelope['state_semantic_sha256']!=descriptor['state_semantic_sha256']:
                raise ValueError('snapshot complete-state digest/day differs')
            if descriptor is m['final_state_descriptor']:final_envelope=envelope
            days.append(descriptor['day'])
        if any(days[i]<days[i+1] for i in range(1,len(days)-1)):raise ValueError('checkpoint generations not latest-first')
        roster={str(p.relative_to(attempt)) for p in attempt.rglob('*') if p.is_file() and p.name!='manifest.json'}
        if roster!=allowed or roster!=set(m['output_sha256']):raise ValueError('complete raw output roster differs')
        for name in m['output_sha256']:self.read_hash(str(attempt.relative_to(self.root)/name))
        envelope=final_envelope;state=envelope['state']
        if state['day']!=config.days or m['final_state_descriptor']['day']!=config.days:raise ValueError('final state horizon differs')
        people=state['people'];agents=state['agents'];l=config.longitudinal
        if len(people)!=len(agents) or not config.n<=len(people)<=(l.max_people_ever or config.n*5):raise ValueError('retained identity population differs')
        if [p['id'] for p in people]!=list(range(len(people))) or [a['id'] for a in agents]!=list(range(len(agents))) or len({p['token'] for p in people})!=len(people):
            raise ValueError('retained person identity reuse/order differs')
        for person in people:
            entered=person['entered_day'];exited=person['exited_day']
            if not 0<=entered<=config.days or exited is not None and not entered<=exited<config.days:raise ValueError('lifecycle dates differ')
            first=person['first_positive_authority_day']
            if first is not None and (type(first) is not int or not entered<=first<config.days or exited is not None and first>exited):
                raise ValueError('first positive formal allocation date differs from lifecycle')
        for person_key,metric in (('active_calendar_days','active_member_calendar_days'),('active_workdays','active_member_workdays'),('present_workdays','present_member_workdays')):
            if sum(p[person_key] for p in people)!=state['metrics'].get(metric,0):raise ValueError('dynamic exposure aggregate differs')
        if m['output_sha256']['.longitudinal-working.sqlite']!=envelope['ledger']['sha256']:
            working=snapshot_semantics(attempt/'.longitudinal-working.sqlite')
            if working['semantic_sha256']!=envelope['ledger']['semantic_sha256']:raise ValueError('working ledger and retained final state differ')
        summary=self.read_json(str(attempt.relative_to(self.root)/'summary.json'))
        verify_summary_evidence(summary,config,state,attempt/envelope['ledger']['file'])
        last=verify_daily_evidence(attempt/'timeseries.csv',config,state,attempt/envelope['ledger']['file'],config_history=config_history)
        for key in INTEGRALS:
            if last[key+'_cumulative']!=state['metrics'].get(key,0):raise ValueError('trajectory cumulative metric differs: '+key)
        return {'case_id':ident,'config':config,'tags':row['tags'],'attempt':attempt,'summary':summary,'manifest':m,
                'state_semantic_sha256':envelope['state_semantic_sha256'],
                'branch_origin':state['branch_origin'],'ledger_path':attempt/envelope['ledger']['file']}

    def _stage(self,name,worlds,expected_provenance):
        if not hasattr(self,'inventories'):self.inventories={}
        expected=stage_inventory(self.spec,self.base,worlds);inventory=self.read_json(name+'/case_inventory.json')
        if canonical(expected)!=canonical(inventory):raise ValueError('declared full stage inventory differs: '+name)
        self.inventories[name]=inventory;meta=self.read_json(name+'/manifest.json')
        if meta.get('status')!='complete' or meta.get('exit_code')!=0 or meta.get('inventory_sha256')!=digest(canonical(inventory)) or meta.get('expected_rows')!=len(inventory) or meta.get('completed_rows')!=len(inventory):
            raise ValueError('scientific stage incomplete or miscounted')
        for key,value in self.identity.items():
            if meta.get(key)!=value:raise ValueError('stage source/spec identity differs')
        refs=self.read_json(name+'/case_references.json');lookup={r['case_id']:r for r in refs}
        if len(refs)!=len(lookup) or set(lookup)!={r['case_id'] for r in inventory}:raise ValueError('stage case pointer roster differs')
        by_id={r['case_id']:r for r in inventory}
        def prefix_configs(row):
            history=[];seen={row['case_id']};parent_id=row['tags']['parent_case_id']
            while parent_id is not None:
                if parent_id in seen or parent_id not in by_id:raise ValueError('raw parent graph cyclic/incomplete')
                seen.add(parent_id);parent=by_id[parent_id];config=Config.from_dict(parent['config'])
                history.append((config.days,config));parent_id=parent['tags']['parent_case_id']
            return tuple(sorted(history,key=lambda r:r[0]))
        stage_cases=[self._case(row,lookup[row['case_id']],expected_provenance,prefix_configs(row)) for row in inventory]
        local={c['case_id']:c for c in stage_cases}
        for child in stage_cases:
            parent_id=child['tags']['parent_case_id'];m=child['manifest']
            if parent_id is None:
                if m.get('branch_origin') is not None:raise ValueError('unexpected state branch origin')
            else:
                parent=local[parent_id];desc=parent['manifest']['final_state_descriptor']
                expected_origin={'parent_case_id':parent_id,'parent_source_sha256':self.identity['source_sha256'],
                    'parent_config_sha256':parent['manifest']['config_sha256'],'parent_day':parent['config'].days,
                    'parent_state_semantic_sha256':desc['state_semantic_sha256']}
                if m.get('branch_origin')!=expected_origin or child['branch_origin']!=expected_origin:
                    raise ValueError('shared full pre-adoption state identity differs')
                from itertools import islice,zip_longest
                prefix=iter_timeseries(parent['attempt']/'timeseries.csv')
                child_prefix=islice(iter_timeseries(child['attempt']/'timeseries.csv'),parent['config'].days)
                if any(a!=b for a,b in zip_longest(prefix,child_prefix)):raise ValueError('branch rewrites observed pre-adoption trajectory')
        observations,effects=build_tables(self.spec,[c for c in stage_cases if c['tags']['role']=='strategy'])
        for rows in (observations,effects):
            for r in rows:r['stage']=name
        tables=self.read_json(name+'/tables.json')
        if canonical(tables)!=canonical({'observations':observations,'effects':effects}):raise ValueError('window/endpoint table differs from original raw')
        self.exact_csv(name+'/observations.csv',observations);self.exact_csv(name+'/longitudinal_endpoints.csv',effects)
        index=[]
        for row in inventory:
            case=local[row['case_id']];ref=lookup[row['case_id']]
            index.append({'stage':name,'case_id':row['case_id'],**row['tags'],'attempt':ref['attempt'],
                          'manifest_sha256':file_digest(case['attempt']/'manifest.json'),
                          'timeseries_sha256':file_digest(case['attempt']/'timeseries.csv'),
                          'final_state_descriptor_sha256':file_digest(case['attempt']/'final_state.json'),
                          'state_semantic_sha256':case['state_semantic_sha256']})
        self.exact_csv(name+'/case_index.csv',index)
        for filename,sha in meta['output_sha256'].items():
            if self.read_hash(name+'/'+filename)!=sha:raise ValueError('stage output pointer differs')
        files={p.name for p in (self.root/name).iterdir() if p.is_file() and p.name!='manifest.json'}
        if files!=set(meta['output_sha256']):raise ValueError('stage output inventory differs')
        self.cases.update(local);self.observations.extend(observations);self.endpoints.extend(effects)

    def _publication(self):
        _require_current_validator()
        _publication_tree(self.root/'publication')
        scientific_inputs={k:v for k,v in self.inputs.items() if k!='pipeline_manifest.json'}
        name='publication/generated/analysis/generation_manifest.json';self.publication=self.read_json(name)
        g=self.publication
        if g.get('status')!='complete' or g.get('schema_version')!=3 or g.get('version')!='DAMS-longitudinal-publication-2' or g.get('model_executions')!=0:
            raise ValueError('publication generation identity/status differs')
        if any(g.get(k)!=v for k,v in self.identity.items()) or g.get('protocol_sha256')!=scientific_inputs['protocol.json']:
            raise ValueError('publication source/driver/spec/protocol differs')
        root=Path(__file__).resolve().parents[1]
        code=('research_tools/longitudinal_analysis.py','research_tools/analyze.py',
              'research_tools/figure_style.py','research_tools/validate_longitudinal.py',
              'research_tools/longitudinal_derived.py')
        if g.get('analysis_sources_sha256')!={path:file_digest(root/path) for path in code}:
            raise ValueError('publication analyzer/style/validator sources differ')
        if self.pipeline.get('publication_generation_manifest_sha256')!=self.inputs[name]:raise ValueError('publication generation pointer differs')
        if self.pipeline.get('publication_log_sha256')!=self.read_hash('publication-analysis.log'):raise ValueError('publication execution log pointer differs')
        if g.get('inputs')!=scientific_inputs:raise ValueError('publication exact scientific input inventory differs')
        required={'generated/analysis/'+p for p in ('observations.csv','longitudinal_endpoints.csv','paired_intervals.csv',
            'cohort_and_memory_descriptives.csv','numerical_claims.json','derived_definitions.json',
            'paired_trajectory.csv','relative_adoption_world_coverage.csv','relative_adoption_trajectory.csv')}
        required.update('generated/'+p for p in ('longitudinal_effects.tex','longitudinal_trajectories.tex','longitudinal_results.tex'))
        for endpoint,_,_ in PRIMARY_ENDPOINTS:
            required.update('figures/results/longitudinal-'+endpoint.replace('_','-')+'.'+extension for extension in ('pdf','svg'))
        required.update('figures/results/'+name+'.'+extension for name in
                        ('longitudinal-paired-trajectory','longitudinal-relative-adoption-trajectory')
                        for extension in ('pdf','svg'))
        if not isinstance(g.get('outputs'),dict) or not required<=set(g['outputs']):
            raise ValueError('publication required artifact roster incomplete')
        for path,sha in g['inputs'].items():
            if self.read_hash(path)!=sha:raise ValueError('publication input differs')
        pub=self.root/'publication'
        for path,sha in g['outputs'].items():
            if self.read_hash('publication/'+path)!=sha:raise ValueError('publication output differs')
        actual={str(p.relative_to(pub)) for p in pub.rglob('*') if p.is_file() and p!=self.root/name}
        if actual!=set(g['outputs']):raise ValueError('publication exact output roster differs')
        for filename,rows in (('observations',self.observations),('longitudinal_endpoints',self.endpoints),('paired_intervals',self.paired_intervals)):
            self.exact_csv('publication/generated/analysis/'+filename+'.csv',rows)
        claims=self.read_json('publication/generated/analysis/numerical_claims.json')
        primary=[r for r in self.paired_intervals if r['primary']]
        expected={'schema_version':3,'source_sha256':self.identity['source_sha256'],'spec':self.spec.to_dict(),
            'confirmation_worlds':self.protocol['confirmation_worlds'],'independent_unit':'paired world',
            'mc_precision_met':all(r['precision_met'] for r in primary),'primary':primary,
            'post_window_status_counts':dict(Counter(r['status'] for r in self.endpoints if r['stage']=='confirmation')),
            'limitations':_derived.LIMITATIONS}
        if any(canonical(claims.get(k))!=canonical(v) for k,v in expected.items()):
            raise ValueError('publication numerical claims differ from rebuilt raw')
        self._derived_publication()
        _publication_tree(pub)
        _require_current_validator()

    def _derived_publication(self):
        """Rebuild descriptive values from verified state/events, without Model."""
        if self.read_json('publication/generated/analysis/derived_definitions.json')!=_derived.DERIVED_DEFINITIONS:
            raise ValueError('publication descriptive definitions differ')
        cases=self.select(stage='confirmation',role='strategy')
        cohort=[]
        for case in cases:
            envelope,_database=self.read_snapshot(case['case_id'])
            cohort.extend(_derived.derive_case(case,envelope,self.iter_journal(case['case_id'])))
        self.exact_csv('publication/generated/analysis/cohort_and_memory_descriptives.csv',cohort)
        contrast=next(c for c in declared_contrasts(self.spec) if c['primary'])
        case_map={(c['tags']['arm_id'],c['config'].world):c for c in cases}
        common=_derived.common_trajectory_data(self,contrast,case_map)
        relative=_derived.relative_trajectory_data(self,contrast,case_map,common['paired'])
        for filename,rows in (('paired_trajectory',common['rows']),
                              ('relative_adoption_world_coverage',relative['coverage']),
                              ('relative_adoption_trajectory',relative['rows'])):
            self.exact_csv('publication/generated/analysis/'+filename+'.csv',rows)

    def iter_timeseries(self,case_id):return iter_timeseries(self.cases[case_id]['attempt']/'timeseries.csv')

    def read_snapshot(self,case_id):
        """Read one verified full state at a time; never retain all populations."""
        case=self.cases[case_id]
        return verify_snapshot(case['attempt']/'final_state.json',expected_config=case['config'])

    def iter_journal(self,case_id):
        import sqlite3
        path=self.cases[case_id]['ledger_path']
        connection=sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True)
        try:yield from connection.execute('SELECT day,kind,event,payload FROM journal ORDER BY seq')
        finally:connection.close()

    def select(self,stage=None,**tags):
        return [c for c in self.cases.values() if (stage is None or any(r['case_id']==c['case_id'] for r in self.inventories[stage])) and all(c['tags'].get(k)==v for k,v in tags.items())]

    def result(self):
        _require_current_validator()
        return {'status':'validated','schema_version':3,'source_sha256':self.identity['source_sha256'],
                'pipeline_driver_sha256':self.identity['pipeline_driver_sha256'],'spec_sha256':self.spec.sha256,
                'unique_complete_cases':len(self.cases),'logical_case_rows':sum(len(v) for v in self.inventories.values()),
                'independent_primary_worlds':self.protocol['confirmation_worlds'],
                'inventory_sha256':digest(canonical(self.inventories)),'mc_precision_met':self.pipeline['mc_precision_met'],
                'inputs_sha256':self.inputs,'validator_sha256':file_digest(Path(__file__)),
                'validator_import_sha256':VALIDATOR_IMPORT_SHA256,'source_import_sha256':SOURCE_IMPORT_SHA256}


def validate_longitudinal_output(output,spec=None,scale=None,*,publication_required=True,producer_in_progress=False,expected_provenance=None):
    checked=CheckedLongitudinalStudy(output,publication_required=publication_required,producer_in_progress=producer_in_progress,expected_provenance=expected_provenance)
    if spec is not None and checked.spec.name!=spec or scale is not None and checked.spec.n!=scale:raise ValueError('requested longitudinal spec/scale differs')
    return checked.result()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--spec');parser.add_argument('--scale',type=int);parser.add_argument('--scientific-only',action='store_true')
    args=parser.parse_args();value=validate_longitudinal_output(args.output,args.spec,args.scale,publication_required=not args.scientific_only)
    print(json.dumps(value,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
