"""Opt-in calendar/lifecycle extension; synthetic mechanisms, not calibration.

The historical daily model remains a separate execution path. This extension
retains exact event evidence on disk and active/ever-person state in memory.
Review sensors never query the research-only produced-work or proposal truth.
"""
from __future__ import annotations

import copy
import dataclasses
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import statistics
from collections import defaultdict

from .authority import authority, gini, tier_authority, total_variation
from .config import Config
from .enterprise_growth import EnterpriseGrowthState, MODEL_VERSION as GROWTH_MODEL_VERSION
from .enterprise_operations import EnterpriseOperatingState, VERSION as CAUSAL_MODEL_VERSION
from .longitudinal import GregorianClock, anniversary, birth_for_age, interval_probability, retention
from .longitudinal_storage import (ExactLedger, snapshot_semantics, CHECKPOINT_FORMAT_VERSION,
    STATE_HASH_CODEC, LEGACY_STATE_HASH_CODEC, checkpoint_codec, hash_state_json,
    integer_state_maps, json_chunks, journal_json, load_checkpoint_json)
from .randomness import WorldRandom
from .storage import atomic_stream, canonical, digest, file_digest, source_hash

IMPORTED_SOURCE_SHA256=source_hash()


def age_at(birth: str, current: date) -> float:
    birth = date.fromisoformat(birth)
    years = current.year-birth.year
    if anniversary(birth, years)>current: years-=1
    before, after = anniversary(birth, years), anniversary(birth, years+1)
    return years+(current-before).days/(after-before).days


class LongitudinalEngine:
    version = 'longitudinal-1'
    def __init__(self, config, *, storage_dir=None, page_options=None, native_owner_dir=None,known_latest_floor=None,journal_chunk_bytes=None):
        from .model import Agent
        if source_hash()!=IMPORTED_SOURCE_SHA256:raise ValueError('longitudinal source changed after import; restart from a frozen source')
        self.execution_source_sha256=IMPORTED_SOURCE_SHA256
        self._state_hash_codec=STATE_HASH_CODEC
        self.config=config.validate(); self.l=config.longitudinal
        self.enterprise = EnterpriseGrowthState(config) if config.enterprise_growth is not None else None
        self.operations = EnterpriseOperatingState(config) if config.enterprise_operations is not None else None
        self.version = CAUSAL_MODEL_VERSION if self.operations is not None else GROWTH_MODEL_VERSION if self.enterprise is not None else type(self).version
        self.clock=GregorianClock(self.l)
        self.rng=WorldRandom(config.seed,config.world,world_context=self.l.world_context)
        self.ledger=ExactLedger(storage_dir,page_options=page_options,journal_chunk_bytes=journal_chunk_bytes)
        self._native_owner=None
        self.day=0; self.failed_day=False
        self.agents=[]; self.people=[]; self.slots={}; self.slot_guild={}
        self.guild_alias={g:g for g in range(config.guilds)}
        self.target=config.n; self.demand=1.
        self.cash=config.n*self.l.initial_cash_per_member
        self.memory={g:self.l.initial_memory for g in range(config.guilds)}
        self.routine={g:self.l.initial_routine_strength for g in range(config.guilds)}
        self.adoption={g:None for g in range(config.guilds)}
        self.migration_paid={g:False for g in range(config.guilds)}
        self.closed_day=None; self.suspended_day=None; self.unfunded_workdays=0
        self.metrics=defaultdict(float); self.history=[]; self.authority_change_sum=0.; self.authority_change_count=0
        self.branch_origin=None
        self.last_work_per_present_member=0.
        for i in range(config.n):
            if i%1000==0:self.check_disk()
            guild=i%config.guilds; token=f'initial:{i}'
            age=self.l.initial_age_min_years+(self.l.initial_age_max_years-self.l.initial_age_min_years)*self.rng.uniform('initial_age',token)
            self._enter(i,guild,token,age,initial=True)
        self.ledger.commit(); self._topology(); self._authority()
        self.ledger.commit()
        if page_options is not None:
            from .native_checkpoint_owner import CheckpointOwner, default_owner_directory
            if page_options.execution_source_sha256!=self.execution_source_sha256:
                raise ValueError('native storage executing source differs')
            self._native_owner=CheckpointOwner(native_owner_dir or default_owner_directory(page_options,digest(canonical(self.config.to_dict()))),
                source_sha256=self.execution_source_sha256,config_sha256=digest(canonical(self.config.to_dict())))
            if self._native_owner.latest() is not None:
                raise ValueError('native fresh world requires a fresh external owner namespace; restore the retained checkpoint')
            self.ledger.activate_pages(day=0,known_latest_floor=known_latest_floor)

    def check_disk(self):
        size=sum(p.stat().st_size for p in self.ledger.directory.iterdir() if p.is_file())
        if size>self.config.max_output_mb*1e6:raise RuntimeError('longitudinal disk/output resource stop; no complete result')
        import resource,sys
        measured=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        measured_mb=measured/(1024*1024) if sys.platform=='darwin' else measured/1024
        if measured_mb>self.config.max_rss_mb:raise MemoryError('longitudinal initialization/runtime RSS resource stop')

    @property
    def guild_count(self):
        return self.enterprise.guilds if self.enterprise is not None else self.config.guilds

    @property
    def site_count(self):
        return self.enterprise.sites if self.enterprise is not None else self.config.sites

    def _enterprise_begin_day(self):
        if self.enterprise is None:return
        previous_guilds = self.guild_count
        operating = self.closed_day is None and self.suspended_day is None
        paid, events = self.enterprise.begin_day(self.day, self.cash, active=len(self.slots),
            operating=operating, payroll_per_member=self.l.payroll_resource_units_per_member_workday,
            expansion_allowed=self.operations.expansion_allowed(self.enterprise) if self.operations is not None else True)
        self.cash -= paid
        self.metrics['infrastructure_setup_resource_units'] += paid
        self.target = self.enterprise.target
        for g in range(previous_guilds, self.guild_count):
            self.guild_alias[g]=g;self.memory[g]=0.;self.routine[g]=0.
            self.adoption[g]=None;self.migration_paid[g]=False
        for index,(kind,value) in enumerate(events):
            self.ledger.add_journal(self.day,kind,f'enterprise:{self.day}:{index}',value)
        self._topology()

    def _enter(self, slot, guild, token, age, *, initial=False, site=None):
        from .model import Agent
        if len(self.agents)>=(self.l.max_people_ever or self.config.n*5):
            raise RuntimeError('ever-person resource limit reached; no silent identity reuse or reduced workforce')
        i=len(self.agents); p=self.config; guild=self.guild_alias[guild]
        birth=birth_for_age(self.clock.at(self.day),age).isoformat()
        a=Agent(i,guild,0,guild%self.site_count if site is None else site,math.exp(.4*self.rng.normal('skill',token)),
                self.rng.uniform('tenure',token) if initial else 0.,
                .3*self.rng.uniform('care',token),self.rng.uniform('autonomy',token),self.rng.uniform('reciprocity',token))
        a.learning=self.memory[guild]*self.l.memory_transfer_fraction
        self.agents.append(a)
        self.people.append({'id':i,'token':token,'slot':slot,'birth_date':birth,'entered_day':self.day,
                            'exited_day':None,'exit_reason':None,'first_positive_authority_day':None,
                            'active_calendar_days':0,'active_workdays':0,'present_workdays':0,
                            'available_work_hours':0.,'post_adoption_available_work_hours':0.})
        self.slots[slot]=i; self.slot_guild[slot]=guild
        if initial and self.l.historical_verified_credit_per_member:
            self.ledger.add_credit(i,guild,self.l.historical_verified_credit_per_member)
        self.ledger.add_journal(self.day,'entry',token,{'person':i,'slot':slot,'guild':guild,'initial':initial,'birth_date':birth})
        if not initial:
            self.cash-=self.l.recruitment_resource_units_per_person
            self.metrics['recruitment_resource_units']+=self.l.recruitment_resource_units_per_person
            self.metrics['entrants']+=1

    def _exit(self, i, reason):
        record=self.people[i]
        if record['exited_day'] is not None: return
        a=self.agents[i]; self.slots.pop(record['slot']); record['exited_day']=self.day; record['exit_reason']=reason; a.share=0.
        # Knowledge loss is conditional on untransferred personal knowledge; old
        # verified credits and pending events are retained in their original domain.
        n=max(1,len(self.members.get(a.guild,[])))
        loss=(1-self.l.memory_transfer_fraction)*min(self.memory[a.guild],a.learning/n)
        self.memory[a.guild]-=loss
        self.metrics['retirements' if reason=='retirement' else 'exits']+=1
        self.metrics['memory_loss_units']+=loss
        self.ledger.add_journal(self.day,reason,record['token'],{'person':i,'retained_credit':self.ledger.credit(i,a.guild),'memory_loss':loss})

    def _topology(self):
        self.members={g:[] for g in range(self.guild_count) if self.guild_alias[g]==g}; self.teams=defaultdict(list)
        for slot,i in sorted(self.slots.items()):
            a=self.agents[i]; a.guild=self.guild_alias[a.guild]; self.members[a.guild].append(i)
        team=0
        for guild,ids in sorted(self.members.items()):
            for start in range(0,len(ids),self.config.team_size):
                for i in ids[start:start+self.config.team_size]:
                    self.agents[i].team=team; self.teams[team].append(i)
                team+=1
        self.active=sorted(self.slots.values())

    def _lifecycle(self):
        p,l,day=self.config,self.l,self.day
        for when,target in l.workforce_targets:
            if when==day:self.target=target;self.ledger.add_journal(day,'workforce_target',f'target:{day}',{'target':target})
        for when,value in l.demand_schedule:
            if when==day:self.demand=value;self.ledger.add_journal(day,'demand',f'demand:{day}',{'multiplier':value})
        for when,a,b in l.guild_mergers:
            if when!=day:continue
            a,b=self.guild_alias[a],self.guild_alias[b]
            if a==b:raise ValueError('scheduled merger already refers to the same domain')
            na,nb=len(self.members.get(a,[])),len(self.members.get(b,[])); total=na+nb
            self.memory[b]=(self.memory[a]*na+self.memory[b]*nb)/total if total else (self.memory[a]+self.memory[b])/2
            self.routine[b]=(self.routine[a]*na+self.routine[b]*nb)/total if total else (self.routine[a]+self.routine[b])/2
            for g,v in list(self.guild_alias.items()):
                if v==a:self.guild_alias[g]=b
            # A merged domain does not automatically grant the source-domain's
            # credits to its successor: existing source evidence remains auditable.
            self.ledger.add_journal(day,'guild_merger',f'merger:{day}:{a}:{b}',{'source':a,'target':b,'credit_transfer':False})
            self._topology()
        for when,i,g in l.guild_moves:
            if when!=day:continue
            if i>=len(self.agents) or self.people[i]['exited_day'] is not None:raise ValueError('guild move references a nonactive or unborn person')
            old=self.agents[i].guild;new=self.guild_alias[g];self.agents[i].guild=new;self.agents[i].site=new%self.site_count
            self.slot_guild[self.people[i]['slot']]=new
            self.ledger.add_journal(day,'guild_move',f'move:{day}:{i}',{'person':i,'old':old,'new':new,'credit_transfer':False})
        self._topology()
        scheduled={i:reason for when,i,reason in l.exit_schedule if when==day}
        if any(i>=len(self.agents) for i in scheduled):raise ValueError('exit references an unborn person')
        for i in list(self.active):
            record=self.people[i]
            if record['slot']>=self.target:self._exit(i,'layoff')
            elif i in scheduled:self._exit(i,scheduled[i])
            elif age_at(record['birth_date'],self.clock.at(day))>=l.retirement_age_years:self._exit(i,'retirement')
            elif self.rng.uniform('exit',day,record['token'])<interval_probability(l.annual_exit_probability,1,self.clock.days_in_year(day)):self._exit(i,'exit')
        self._topology()
        if self.closed_day is not None or self.suspended_day is not None:return
        if self.enterprise is not None:
            import heapq
            guild_load=[(len(self.members[g]),g) for g in range(self.guild_count)]
            site_counts=[0]*self.site_count
            for i in self.active:site_counts[self.agents[i].site]+=1
            site_load=[(count,site) for site,count in enumerate(site_counts)]
            heapq.heapify(guild_load);heapq.heapify(site_load)
        for slot in range(self.target):
            if slot in self.slots:continue
            if self.operations is not None and not self.operations.recruit_ready(slot,day):continue
            if self.rng.uniform('recruit',day,slot)>=interval_probability(l.annual_vacancy_fill_probability,1,self.clock.days_in_year(day)):continue
            if self.cash<l.recruitment_resource_units_per_person:
                self.metrics['recruitment_unfunded_opportunities']+=1;continue
            if self.enterprise is None:
                guild=self.slot_guild.get(slot,slot%self.guild_count)
                self._enter(slot,guild,f'entry:{day}:{slot}',l.entrant_age_years)
            else:
                count,guild=heapq.heappop(guild_load);site_count,site=heapq.heappop(site_load)
                if count>=self.enterprise.c.members_per_guild or site_count>=self.enterprise.c.members_per_site:
                    raise RuntimeError('installed guild/site capacity cannot fit recruitment; no overbooking')
                self._enter(slot,guild,f'entry:{day}:{slot}',l.entrant_age_years,site=site)
                heapq.heappush(guild_load,(count+1,guild));heapq.heappush(site_load,(site_count+1,site))
        if self.operations is not None:
            for slot in self.slots:self.operations.filled(slot)
        self._topology()

    def policy(self, guild):
        adopted=self.adoption[guild] is not None
        return (self.config.regime,self.config.backend) if adopted else (self.l.pre_adoption_regime,self.l.pre_adoption_backend)

    def _adopt(self):
        day,l=self.day,self.l
        for g,ids in sorted(self.members.items()):
            if self.adoption[g] is not None or l.adoption_mode=='never':continue
            if l.adoption_mode=='fixed':requested=day>=l.adoption_day
            elif l.adoption_mode=='staged':requested=day>=dict(l.guild_adoption_days)[g]
            else:
                value={'review_backlog_per_active_member':self.ledger.pending_count('review')/max(1,len(self.active)),
                       'active_members':len(self.active),'last_work_per_present_member':self.last_work_per_present_member}[l.observable_trigger]
                requested=day>=l.trigger_not_before_day and value>=l.trigger_threshold
            if not requested:continue
            cost=len(ids)*l.migration_resource_units_per_member
            if self.cash<cost:
                self.metrics['adoption_unfunded_guild_days']+=1;continue
            self.cash-=cost;self.metrics['migration_resource_units']+=cost
            self.adoption[g]=day;self.migration_paid[g]=True
            self.ledger.add_journal(day,'adoption',f'adoption:{day}:{g}',{'guild':g,'migration_cost':cost,'memory_retained':self.memory[g],'routine_retained':self.routine[g],'policy':self.config.regime,'backend':self.config.backend})

    def _authority(self):
        p=self.config
        signature=(tuple((g,tuple(ids),self.policy(g)) for g,ids in sorted(self.members.items())),self.closed_day,self.suspended_day)
        refresh=self.day%p.update_interval_days==0 or signature!=getattr(self,'_authority_signature',None)
        for a in self.agents:
            a.confirmed=0.
            a.share=0. if self.people[a.id]['exited_day'] is not None or self.closed_day is not None or self.suspended_day is not None else a.share
        for person,guild,amount in self.ledger.rows('credits'):
            if self.agents[person].guild==guild:self.agents[person].confirmed=amount
        for g,ids in sorted(self.members.items()):
            if not ids or self.closed_day is not None or self.suspended_day is not None:continue
            c=[self.agents[i].confirmed for i in ids]
            if not refresh:continue
            regime,_=self.policy(g)
            if regime in {'hierarchy','hierarchy_tenure'}:
                scores=c if regime=='hierarchy' and p.hierarchy_basis=='performance' else [self.agents[i].tenure for i in ids]
                weights=tier_authority(scores,p.hierarchy_weights)
            else:weights=authority(c,{'equal':0.,'linear':1.,'sublinear':p.alpha}[regime],zero_policy='equal')
            self.authority_change_sum+=math.fsum(abs(self.agents[i].share-v) for i,v in zip(ids,weights))/2;self.authority_change_count+=1
            for i,v,credit in zip(ids,weights,c):
                self.agents[i].share=v;self.agents[i].confirmed=credit
                if v>0 and self.people[i]['first_positive_authority_day'] is None:self.people[i]['first_positive_authority_day']=self.day
        self._authority_signature=signature

    def _commit(self):
        from .model import clip
        day=self.day
        for g in range(self.guild_count):
            _,backend=self.policy(self.guild_alias[g])
            if backend=='consensus' and self.config.quorum_unavailable_start_day<=day<self.config.quorum_unavailable_stop_day:
                self.metrics['quorum_unavailable_guild_days']+=1;continue
            # Bounded retrieval, including event streams larger than RAM.
            while True:
                batch=self.ledger.take_ready('commit',g,day,1024)
                if not batch:break
                for claim in batch:
                    if not self.ledger.mark_seen(claim.event):self.metrics['duplicate_records_rejected']+=1;continue
                    value=max(0.,claim.observed*retention(self.l.credit_half_life_days,day-claim.created))
                    self.ledger.add_credit(claim.agent,g,value);self.agents[claim.agent].trust=clip(self.agents[claim.agent].trust+.015)
                    self.metrics['confirmed_records']+=1;self.metrics['fraudulent_records_accepted']+=int(claim.fraudulent)
                    self.ledger.add_delay('confirmation',day-claim.created)
                    if claim.correction:self.metrics['corrections']+=1;self.ledger.add_delay('appeal',day-claim.created)
                    self.ledger.add_journal(day,'commit',claim.event,{'person':claim.agent,'guild':g,'credit':value,'fraudulent':claim.fraudulent,'correction':claim.correction})

    def _review(self,present,funding):
        from .model import clip
        p,day=self.config,self.day
        for g in range(self.guild_count):
            domain=self.guild_alias[g]; ids=[i for i in self.members.get(domain,[]) if i in present]
            # Source-domain queues survive mergers, but share the successor's
            # single budget. No duplicated capacity for former domains.
            if domain!=g:continue
            _,backend=self.policy(domain); index=('central','witness','consensus').index(backend)
            unit=p.backend_review_multipliers[index];settlement=p.backend_settlement_days[index]
            slots=int(len(ids)*p.review_capacity_per_member_day*funding/unit)
            appeal_slots=int(len(ids)*p.appeal_capacity_per_member_day*funding)
            source_domains=[v for v in range(self.guild_count) if self.guild_alias[v]==domain]
            # Global lottery/order across merged queues, not one free pool per old domain.
            remaining=slots
            while remaining>0:
                batch=self.ledger.take_ready_domains('review',source_domains,day,min(1024,remaining))
                if not batch:break
                remaining-=len(batch)
                for source,claim in batch:
                    self._review_one(source,claim,day,backend,unit,settlement)
            remaining=appeal_slots
            while remaining>0:
                batch=self.ledger.take_ready_domains('appeal',source_domains,day,min(1024,remaining))
                if not batch:break
                remaining-=len(batch)
                for source,claim in batch:self._appeal_one(source,claim,day,settlement)

    def _review_one(self,source,claim,day,backend,unit,settlement):
        from .model import clip
        p=self.config
        self.metrics['review_hours']+=.1;self.metrics['verification_resource_units']+=unit-1
        accepted=claim.observed>=.25 and not claim.audit_detected
        self.ledger.add_journal(day,'review',claim.event,{'accepted':accepted,'observed':claim.observed,'audit_detected':claim.audit_detected,'backend':backend})
        if accepted:self.ledger.push('commit',source,dataclasses.replace(claim,ready=day+settlement))
        else:
            self.metrics['reviews_rejected']+=1;self.metrics['fraudulent_records_detected']+=int(claim.fraudulent);self.metrics['honest_records_rejected']+=int(not claim.fraudulent)
            a=self.agents[claim.agent];a.trust=clip(a.trust-.025)
            if claim.observed>=.25 and a.trust>.2:self.ledger.push('appeal',source,dataclasses.replace(claim,ready=day+p.appeal_delay_days))
    def _appeal_one(self,source,claim,day,settlement):
        self.metrics['appeal_hours']+=.15
        token=self.people[claim.agent]['token']
        detected=self.rng.uniform('appeal_audit',claim.created,token)<(.9 if claim.fraudulent else .01)
        if not detected:self.ledger.push('commit',source,dataclasses.replace(claim,ready=day+settlement,correction=True))
        else:self.metrics['appeals_denied']+=1
        self.ledger.add_journal(day,'appeal',claim.event,{'detected':detected})


    def step(self, *, reverse_agents=False):
        if self._state_hash_codec!=STATE_HASH_CODEC:raise ValueError('legacy checkpoint is inspection-only; resume with the original frozen implementation')
        if self.failed_day:raise RuntimeError('partial day failed; restore a durable checkpoint before continuing')
        if self.day>=self.config.days:raise ValueError('world has reached its specified horizon')
        try:
            if getattr(self.ledger,'native_pages_active',False):
                self.ledger.commit_day(self.day+1,self._step)
            else:
                self.ledger.begin();self._step();self.ledger.commit()
        except BaseException:
            self.ledger.rollback();self.failed_day=True;raise

    def _step(self):
        from .model import Claim,clip
        p,l,day=self.config,self.l,self.day
        before=dict(self.metrics)
        creating=l.work_creation_stop_day is None or day<l.work_creation_stop_day
        if l.closure_day==day:self.closed_day=day
        if l.suspension_day==day:self.suspended_day=day
        operation_received=operation_borrowed=0.
        operation_begin=None
        if self.operations is not None:
            demand=next((v for when,v in reversed(l.demand_schedule) if when<=day),self.demand)
            operation_received,operation_borrowed,operation_begin=self.operations.start_day(day,
                working=self.clock.is_workday(day),creating=creating,
                operating=self.closed_day is None and self.suspended_day is None,cash=self.cash,
                demand_multiplier=demand,capacity=self.enterprise.capacity,growth=self.enterprise,
                noise=self.rng.normal('enterprise_market_orders',day),year_days=self.clock.days_in_year(day))
            self.cash+=operation_received+operation_borrowed
            self.metrics['revenue_resource_units']+=operation_received
            self.metrics['financing_inflow_resource_units']+=operation_borrowed
            self.ledger.add_journal(day,'enterprise_market_begin',f'market:begin:{day}',operation_begin)
        self._enterprise_begin_day()
        self._lifecycle()
        self.ledger.decay_credits(retention(l.credit_half_life_days))
        for g in self.memory:self.memory[g]*=retention(l.memory_half_life_days)
        for a in self.agents:a.learning*=retention(l.skill_forgetting_half_life_days)
        operating=self.closed_day is None and self.suspended_day is None
        if operating:self._commit();self._adopt()
        self._authority()
        working=self.clock.is_workday(day)
        self.metrics['calendar_days_observed']+=1
        if working:self.metrics['calendar_workdays_observed']+=1
        active=self.active if operating else []
        self.metrics['active_member_calendar_days']+=len(active)
        for i in active:self.people[i]['active_calendar_days']+=1
        present=set()
        if working:
            self.metrics['active_member_workdays']+=len(active)
            for i in active:
                a=self.agents[i];r=self.people[i];available=max(0.,1-a.care_hours)
                r['active_workdays']+=1;r['available_work_hours']+=available
                self.metrics['available_member_work_hours']+=available
                self.metrics['observation_available_member_work_hours' if creating else 'tail_available_member_work_hours']+=available
                if self.adoption[a.guild] is not None:
                    r['post_adoption_available_work_hours']+=available;self.metrics['post_adoption_available_member_work_hours']+=available
                leave=min(1.,l.annual_leave_workdays/max(1,self.clock.workdays_in_year(day)))
                if self.rng.uniform('leave',day,r['token'])>=leave:
                    present.add(i);r['present_workdays']+=1
                    self.metrics['present_member_work_hours']+=available
            self.metrics['present_member_workdays']+=len(present)
        training={i:l.training_hours_per_workday if self.adoption[self.agents[i].guild] is not None and day-self.adoption[self.agents[i].guild]<l.transition_days else 0. for i in present}
        if self.operations is not None:
            for i in training:training[i]+=self.operations.training(self.people[i],day)
        due=len(present)*(l.payroll_resource_units_per_member_workday+l.maintenance_resource_units_per_member_workday)+sum(training[i]>0 for i in present)*l.dual_run_resource_units_per_member_workday
        infrastructure_due = self.enterprise.maintenance_due() if self.enterprise is not None and working and operating else 0.
        operation_due = (math.fsum(self.operations.context(self.agents[i].site).legal_resource_units_per_worker_workday for i in present)
                         + operation_begin['interest_due']) if self.operations is not None else 0.
        due += infrastructure_due+operation_due
        funding=min(1.,self.cash/due) if due else 1.
        paid=due*funding;self.cash-=paid
        if self.enterprise is not None:
            self.metrics['infrastructure_maintenance_resource_units'] += infrastructure_due*funding
        if self.operations is not None:
            self.metrics['enterprise_legal_and_interest_resource_units']+=operation_due*funding
        self.metrics['operating_resource_units']+=paid;self.metrics['operating_unfunded_resource_units']+=due-paid
        self.metrics['payroll_resource_units']+=len(present)*l.payroll_resource_units_per_member_workday*funding
        self.metrics['maintenance_resource_units']+=len(present)*l.maintenance_resource_units_per_member_workday*funding
        self.metrics['dual_run_resource_units']+=sum(training[i]>0 for i in present)*l.dual_run_resource_units_per_member_workday*funding
        if working and operating:
            self.unfunded_workdays=self.unfunded_workdays+1 if funding<1 else 0
            if self.unfunded_workdays>=l.closure_after_unfunded_workdays:
                self.closed_day=day;self.ledger.add_journal(day,'closure',f'closure:{day}',{'cause':'consecutive_unfunded_workdays','cash':self.cash});present=set();self._authority()
        snapshot={i:(self.agents[i].share,self.agents[i].trust,self.agents[i].fatigue,self.agents[i].learning) for i in self.active}
        # A vote/admin operation costs its full .05 workday. Fractional funding
        # scales available capacity and divisible reservations, not the cost of
        # an indivisible governance operation into a free vote.
        cooperation={i:clip(self.agents[i].reciprocity*snapshot[i][1]*(1-snapshot[i][2])) for i in present}
        coordination={i:self.operations.coordination(len(active),self.guild_count,self.enterprise.departments,self.site_count,self.agents[i].site) if self.operations is not None else 0. for i in present}
        variable_reservations={i:coordination[i]+.1*p.review_capacity_per_member_day+.15*p.appeal_capacity_per_member_day+.05*cooperation[i]+training[i] for i in present}
        governance_time={i:.05 if (1-self.agents[i].care_hours-variable_reservations[i])*funding>=.05 else 0. for i in present}
        losses=[];participation=[];influence=[]
        if creating and working and operating and self.closed_day is None:
            for g,ids in sorted(self.members.items()):
                theta=self.rng.normal('proposal_value',day,g)
                effective=[];votes=[];flags=[]
                for i in ids:
                    share,trust,fatigue,learning=snapshot[i];token=self.people[i]['token']
                    signal=theta+p.decision_noise_sd/(1+learning)*self.rng.normal('decision_signal',day,token)+p.shared_signal_sd*self.rng.normal('shared_signal',day,g)
                    flag=i in present and governance_time[i]>0 and self.rng.uniform('participate',day,token)<clip(.25+.65*trust-.35*fatigue)
                    flags.append(flag);effective.append(share if flag else 0.);votes.append(float(signal>=0))
                    self.ledger.add_journal(day,'vote',f'vote:{day}:{token}:g:{g}',{'person':i,'guild':g,'observed_signal':signal,'participates':flag,'formal_share':share,'effective_weight':share if flag else 0.,'vote':float(signal>=0)})
                mass=math.fsum(effective)
                if mass:
                    accepted=math.fsum(w*v for w,v in zip(effective,votes))/mass>=.5
                    loss=abs(theta) if accepted!=(theta>=0) else 0.
                    influence.append(total_variation([snapshot[i][0] for i in ids],[v/mass for v in effective]))
                    self.metrics['decisions_completed']+=1;self.metrics['decision_errors']+=int(loss>0)
                else:loss=abs(theta);self.metrics['decisions_unresolved']+=1
                losses.append(loss);participation.append(sum(flags)/len(ids) if ids else 0.)
                self.ledger.add_journal(day,'decision',f'decision:{day}:{g}',{'guild':g,'proposal_truth_research_only':theta,'loss':loss,'participation':sum(flags),'effective_mass':mass})
        attack_active=creating and p.attack!='none' and p.attack_budget_hours_per_day>0 and p.attack_start_day<=day<p.attack_stop_day and bool(present)
        # A fixed slot cohort remains policy-common despite nonreused person IDs.
        attack_slots=math.ceil(p.n*p.attack_cohort_fraction)
        attackers=sorted(i for i in present if self.people[i]['slot']<attack_slots)
        attack_time=p.attack_budget_hours_per_day/len(attackers) if attack_active and attackers else 0.
        efforts={};time_limits={}
        for i in sorted(present):
            a=self.agents[i];share,trust,fatigue,_=snapshot[i]
            reserved=variable_reservations[i]*funding+governance_time[i]
            limit=max(0.,(1-a.care_hours)*funding-reserved)
            attack=attack_time if i in attackers else 0.
            if attack>limit+1e-12:raise ValueError('longitudinal attack budget infeasible for present actor capacity')
            limit-=attack;time_limits[i]=limit
            autonomy=a.autonomy*p.autonomy_response*(len(self.members[a.guild])*share-1)
            if p.behavior_rule=='linear_response':effort=.5+.35*trust+autonomy-.4*fatigue-a.care_hours
            elif p.behavior_rule=='satisficing':effort=(.45 if a.confirmed/max(1,self.people[i]['active_workdays'])>=.5 else .8)+autonomy-.3*fatigue
            else:effort=a.effort_memory+.1*(trust-.5)+autonomy-.2*fatigue
            efforts[i]=clip(effort,0,limit)
            if self.operations is not None:efforts[i]*=self.operations.calendar_fraction(self.clock,a.site,day)
            if not creating:efforts[i]=0.
            if attack_active and p.attack=='freeride' and i in attackers:efforts[i]=max(0.,efforts[i]-attack);cooperation[i]=0.
            booked=a.care_hours+reserved+efforts[i]+attack
            if booked>1+1e-12:raise ValueError('longitudinal time budget exceeds a workday')
            self.metrics['max_individual_time_booked_hours']=max(self.metrics['max_individual_time_booked_hours'],booked)
            self.metrics['training_hours']+=training[i]*funding
            self.metrics['governance_hours']+=governance_time[i]
        team_help={team:statistics.fmean(cooperation.get(i,0.) for i in ids) for team,ids in self.teams.items()}
        production_fraction=1.
        if self.operations is not None:
            potentials=[]
            for index,i in enumerate(self.active):
                if i not in present or not creating or funding==0:continue
                a=self.agents[i];token=self.people[i]['token'];cross=self.active[(index+1)%len(self.active)]
                help_input=(team_help[a.team]+cooperation.get(cross,0.))/2
                shock=.7 if p.fault_start_day<=day<p.fault_stop_day and a.site==0 else 1.
                adopt=self.adoption[a.guild];transition=1. if adopt is None else 1-.2*self.routine[a.guild]*(1-clip((day-adopt)/l.transition_days))
                potentials.append(a.skill*(1+a.learning)*efforts[i]*(1-a.fatigue)*math.exp(.1*self.rng.normal('task_output',day,token))*shock*transition/(.5+self.rng.uniform('task_difficulty',day,token))*(1+p.cooperation_strength*help_input))
            potential=math.fsum(potentials)
            productive_limit=self.operations.productive_limit(self.enterprise.capacity,self.cash,self.site_count)
            production_fraction=min(1.,productive_limit/potential) if potential else 1.
            efforts={i:effort*production_fraction for i,effort in efforts.items()}
            self.metrics['enterprise_coordination_hours']+=math.fsum(coordination.values())*funding
        produced_by_site=defaultdict(float)
        daily_output=0.;yearwork=max(1,self.clock.workdays_in_year(day))
        for index,i in enumerate(self.active):
            a=self.agents[i];token=self.people[i]['token']
            if i not in present:a.fatigue=clip(a.fatigue-.1);continue
            if not creating or funding==0:
                a.fatigue=clip(a.fatigue-.1);continue
            if self.metrics['work_events']>=p.max_events:raise RuntimeError('longitudinal actual work-event guard exceeded; no silently shortened horizon')
            cross=self.active[(index+1)%len(self.active)]
            help_input=(team_help[a.team]+cooperation.get(cross,0.))/2
            shock=.7 if p.fault_start_day<=day<p.fault_stop_day and a.site==0 else 1.
            adopt=self.adoption[a.guild];transition=1.
            if adopt is not None:
                progress=clip((day-adopt)/l.transition_days)
                transition=1-.2*self.routine[a.guild]*(1-progress)
            difficulty=.5+self.rng.uniform('task_difficulty',day,token)
            quality=math.exp(.1*self.rng.normal('task_output',day,token))
            produced=a.skill*(1+a.learning)*efforts[i]*(1-a.fatigue)*quality*shock*(self.demand if self.operations is None else 1.)*transition/difficulty*(1+p.cooperation_strength*help_input)
            daily_output+=produced;produced_by_site[a.site]+=produced;observed=max(0.,produced+p.review_error_sd*self.rng.normal('observed_work',day,token))
            fraud=attack_active and p.attack=='forge' and i in attackers
            if fraud:observed+=attack_time;self.metrics['fraudulent_records_submitted']+=1
            audit=self.rng.uniform('review_audit',day,token)<(.6 if fraud else .02)
            event=f'work:{day}:{token}:g:{a.guild}'
            claim=Claim(day+1,event,i,day,observed,audit,fraud,priority=self.rng.uniform('review_priority',day,token))
            _,backend=self.policy(a.guild);resistance=p.administrative_censorship_exposure[('central','witness','consensus').index(backend)]
            censor=attack_active and p.attack=='censor' and i not in attackers and self.rng.uniform('censor',day,token)<min(1.,p.attack_budget_hours_per_day/max(1,len(present)))*resistance
            if censor:self.metrics['censored_records']+=1;a.trust=clip(a.trust-.03)
            else:self.ledger.push('review',a.guild,claim)
            if attack_active and p.attack=='duplicate' and i in attackers:self.ledger.push('review',a.guild,dataclasses.replace(claim))
            self.metrics['work_events']+=1
            self.ledger.add_journal(day,'work',event,{'person':i,'guild':a.guild,'produced_research_only':produced,'observed':observed,'audit':audit,'fraudulent':fraud,'censored':censor,'effort':efforts[i],'training':training[i]*funding,'attack_hours':attack_time if i in attackers else 0.,'morning_observed_state':{'share':snapshot[i][0],'trust':snapshot[i][1],'fatigue':snapshot[i][2],'learning':snapshot[i][3]},'cooperation':cooperation[i],'help_input':help_input,'funding_fraction':funding,**({'enterprise_coordination':coordination[i]*funding,'enterprise_calendar_fraction':self.operations.calendar_fraction(self.clock,a.site,day),'enterprise_production_fraction':production_fraction} if self.operations is not None else {})})
            workload=efforts[i]+coordination[i]*funding+(attack_time if i in attackers else 0.)+training[i]*funding
            a.fatigue=clip(a.fatigue+.15*workload-.1*(1-workload)-.03)
            a.learning=clip(a.learning+l.skill_learning_per_work_year*efforts[i]/yearwork,0,2)
            a.effort_memory=efforts[i]
        for g,ids in self.members.items():
            if self.adoption[g] is not None:self.routine[g]*=retention(l.routine_adjustment_half_life_days)
            mean_effort=math.fsum(efforts.get(i,0.) for i in ids)/max(1,len(ids))
            self.memory[g]=min(2.,self.memory[g]+l.memory_learning_per_work_year*mean_effort/yearwork)
            if self.adoption[g] is None:self.routine[g]=min(1.,self.routine[g]+(1-self.routine[g])*(1-retention(l.routine_adjustment_half_life_days)))
        for i in self.active:self.agents[i].tenure+=1/self.clock.days_in_year(day)
        revenue=daily_output*l.revenue_resource_units_per_work_unit
        if self.operations is not None:
            material_cost,operation_end=self.operations.finish_day(day,produced_by_site,self.cash,interest_paid=operation_begin['interest_due']*funding)
            self.cash-=material_cost;self.metrics['operating_resource_units']+=material_cost
            self.metrics['enterprise_material_and_logistics_resource_units']+=material_cost
            self.metrics['orders_arrived_units']+=operation_begin['arrivals'];self.metrics['orders_expired_units']+=operation_begin['expired']
            self.metrics['delivered_product_units']+=operation_begin['delivered']
            revenue=operation_received
            self.ledger.add_journal(day,'enterprise_market_end',f'market:end:{day}',operation_end)
        else:
            self.cash+=revenue;self.metrics['revenue_resource_units']+=revenue
        if self.enterprise is not None:
            self.enterprise.observe(day,revenue,paid+(material_cost if self.operations is not None else 0.),working=working and operating)
        self.metrics['produced_work_units']+=daily_output;self.metrics['effort_hours']+=math.fsum(efforts.values());self.metrics['cooperation_units']+=math.fsum(cooperation.values());self.metrics['decision_regret_units']+=math.fsum(losses)
        consumed=attack_time*len(attackers) if attack_active else 0.;self.metrics['attack_hours_consumed']+=consumed;self.metrics['attack_budget_hours']+=consumed
        self.last_work_per_present_member=daily_output/max(1,len(present))
        if present:self._review(present,funding)
        backlog=self.ledger.pending_count('review')+self.ledger.pending_count('commit');self.metrics['backlog_peak_records']=max(self.metrics['backlog_peak_records'],backlog)
        if day%p.trace_every_days==0 or day+1==p.days:
            tvs=[];gaps=[];ginis=[]
            for g,ids in self.members.items():
                if not ids or self.closed_day is not None or self.suspended_day is not None:continue
                shares=[self.agents[i].share for i in ids];credits=[self.ledger.credit(i,g) for i in ids]
                tvs.append(total_variation(shares,authority(credits,1,zero_policy='equal')));ginis.append(gini(shares));gaps.append(gini(shares)-gini(credits))
            avg=lambda values:statistics.fmean(values) if values else None
            self.history.append({'day':day,'calendar_date':self.clock.at(day).isoformat(),'fiscal_year':self.clock.fiscal_year(day),'is_workday':working,
                'organization_age_years':l.organization_initial_age_years+age_at(l.calendar_start,self.clock.at(day)),
                'active_members':len(active),'present_members':len(present),'ever_people':len(self.agents),'available_member_work_hours_cumulative':self.metrics['available_member_work_hours'],
                'output_work_units':daily_output,'review_backlog_records':backlog,'appeal_backlog_records':self.ledger.pending_count('appeal'),'decision_regret_units':math.fsum(losses),
                'participation_fraction':avg(participation),'formal_effective_weight_tv':avg(influence),'authority_contribution_tv':avg(tvs),'signed_gini_gap':avg(gaps),'gini_authority':avg(ginis),
                'mean_trust':avg([self.agents[i].trust for i in self.active]),'mean_fatigue':avg([self.agents[i].fatigue for i in self.active]),'cash_resource_units':self.cash,
                'adopted_guilds':sum(v is not None for v in self.adoption.values()),'closed':self.closed_day is not None,'suspended':self.suspended_day is not None})
            row=self.history[-1]
            if self.enterprise is not None:
                row.update({'initial_population':p.n,'organization_capacity':self.enterprise.capacity,
                    'workforce_target':self.target,'guild_count':self.guild_count,
                    'department_count':self.enterprise.departments,'site_count':self.site_count,
                    'infrastructure_setup_resource_units_cumulative':self.metrics.get('infrastructure_setup_resource_units',0.),
                    'infrastructure_maintenance_resource_units_cumulative':self.metrics.get('infrastructure_maintenance_resource_units',0.)})
            if self.operations is not None:
                row.update({'market_price_resource_units':self.operations.price,'order_backlog_units':self.operations.backlog(),'receivable_resource_units':math.fsum(v['revenue'] for v in self.operations.shipments),'credit_debt_resource_units':self.operations.debt})
            row['work_creation_enabled']=creating
            row['adoption_days']=canonical(self.adoption).decode()
            for key in ('active_member_calendar_days','active_member_workdays','present_member_workdays','present_member_work_hours','available_member_work_hours','post_adoption_available_member_work_hours','produced_work_units','decision_regret_units','decisions_completed','decisions_unresolved','review_hours','appeal_hours','governance_hours','training_hours','migration_resource_units','dual_run_resource_units','maintenance_resource_units','payroll_resource_units','recruitment_resource_units','operating_resource_units','operating_unfunded_resource_units','revenue_resource_units'):
                row[key+'_daily']=self.metrics.get(key,0.)-before.get(key,0.)
                row[key+'_cumulative']=self.metrics.get(key,0.)
        self.ledger.add_journal(day,'day_end',f'day:{day}',{**({'enterprise_growth_state':self.enterprise.to_dict()} if self.enterprise is not None else {}),**({'enterprise_operating_state':self.operations.to_dict()} if self.operations is not None else {}),'date':self.clock.at(day).isoformat(),'active':len(active),'present':len(present),'output':daily_output,'cash':self.cash,'closed_day':self.closed_day,'suspended_day':self.suspended_day,'adoption_days':self.adoption,
            'metrics_daily':{k:self.metrics[k]-before.get(k,0.) for k in self.metrics},'metrics_cumulative':dict(self.metrics),
            'memory':self.memory,'routine':self.routine,'review_backlog_records':backlog,'appeal_backlog_records':self.ledger.pending_count('appeal')})
        self.day+=1

    def summary(self):
        confirmation,p95=self.ledger.delay_summary('confirmation');appeal,_=self.ledger.delay_summary('appeal')
        out=dict(self.metrics);denom=out.get('available_member_work_hours',0.)
        out.update({'model_version':self.version,'world':self.config.world,'regime':self.config.regime,'backend':self.config.backend,'attack':self.config.attack,'n':self.config.n,'days_completed':self.day,
                    'calendar_start':self.l.calendar_start,'calendar_end_exclusive':self.clock.at(self.day).isoformat(),'active_members_final':len(self.active) if self.closed_day is None and self.suspended_day is None else 0,
                    'ever_people':len(self.agents),'closure_day':self.closed_day,'suspension_day':self.suspended_day,'cash_resource_units':self.cash,'adoption_days':dict(self.adoption),
                    'confirmation_mean_days':confirmation,'confirmation_p95_days':p95,'appeal_mean_days':appeal,
                    'unfinished_records':self.ledger.pending_count('review')+self.ledger.pending_count('commit'),'unfinished_appeals':self.ledger.pending_count('appeal'),
                    'produced_work_per_available_member_hour':out.get('produced_work_units',0.)/denom if denom else None,
                    'decision_regret_per_available_member_hour':out.get('decision_regret_units',0.)/denom if denom else None,
                    'last_authority_contribution_tv':self.history[-1]['authority_contribution_tv'] if self.history else None,'last_signed_gini_gap':self.history[-1]['signed_gini_gap'] if self.history else None,
                    'assumptions_status':'synthetic design/stress assumptions; not calibrated','history_mode':self.l.history_mode,'branch_origin':self.branch_origin})
        if self.enterprise is not None:
            out.update({'initial_population':self.config.n,'organization_capacity_final':self.enterprise.capacity,
                'workforce_target_final':self.target,'guild_count_final':self.guild_count,
                'department_count_final':self.enterprise.departments,'site_count_final':self.site_count,
                'growth_assumptions_status':'synthetic design; no empirical calibration or holdout validation'})
        if self.operations is not None:
            out.update({'enterprise_operating_state_final':self.operations.to_dict(),'operating_assumptions_status':'synthetic mechanisms; not externally validated'})
        return out

    def header(self):
        value={'state_schema_version':1,'model_version':self.version,'config':self.config.to_dict(),'day':self.day,
                'execution_source_sha256':self.execution_source_sha256,
                'people':self.people,'slots':self.slots,'slot_guild':self.slot_guild,'guild_alias':self.guild_alias,'target':self.target,'demand':self.demand,
                'cash':self.cash,'memory':self.memory,'routine':self.routine,'adoption':self.adoption,'migration_paid':self.migration_paid,
                'closed_day':self.closed_day,'suspended_day':self.suspended_day,'unfunded_workdays':self.unfunded_workdays,'metrics':dict(self.metrics),'history':self.history,
                'authority_change_sum':self.authority_change_sum,'authority_change_count':self.authority_change_count,'last_work_per_present_member':self.last_work_per_present_member,'branch_origin':self.branch_origin}
        # Historical hashes covered a header without this field. Preserve that
        # identity when examining a historical state; new writers mark v2.
        if self.enterprise is not None:value['enterprise_growth_state']=self.enterprise.to_dict()
        if self.operations is not None:value['enterprise_operating_state']=self.operations.to_dict()
        if self._state_hash_codec!=LEGACY_STATE_HASH_CODEC:value['state_hash_codec']=self._state_hash_codec
        return value

    def semantic_digest(self):
        return self._semantic_digest_with_ledger()

    def _semantic_digest_with_ledger(self, ledger_semantic_sha256=None):
        """Compose the existing codec with this writer's committed ledger digest."""
        if self.failed_day:raise ValueError('cannot snapshot a partially failed day')
        h=hashlib.sha256();hash_state_json(h,self.header(),self._state_hash_codec)
        for a in self.agents:h.update(b'\n');h.update(canonical(dataclasses.asdict(a)))
        if ledger_semantic_sha256 is None:ledger_semantic_sha256=self.ledger.semantic_digest()
        h.update(b'\n');h.update(ledger_semantic_sha256.encode());return h.hexdigest()

    def state(self):
        if self.failed_day:raise ValueError('cannot serialize a partially failed day as a restartable state')
        value=copy.deepcopy(self.header());value['agents']=[dataclasses.asdict(a) for a in self.agents];value['ledger_rows']=self.ledger.materialize();return value

    def write_snapshot(self,path,*,final=False):
        if self.failed_day or self.ledger.db.in_transaction:raise ValueError('snapshot requires a successfully completed day boundary')
        if self.operations is not None:self.validate_semantics()
        if source_hash()!=self.execution_source_sha256:raise ValueError('longitudinal execution source changed; cannot certify a mixed-version snapshot')
        if self._state_hash_codec!=STATE_HASH_CODEC:raise ValueError('new checkpoint writer requires the current state hash codec; retain the original legacy checkpoint')
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() or path.with_suffix('.sqlite').exists():raise ValueError('immutable longitudinal checkpoint already exists')
        native=getattr(self.ledger,'native_pages_active',False)
        database=path.with_suffix('.sqlite') if not native or final else None
        temporary=None if database is None else database.with_name('.'+database.name+'.tmp')
        native_descriptor=None
        try:
            if native:
                floor=self.ledger.latest_floor
                owned=self._native_owner.latest()
                if owned is not None:floor=owned['floor']
                native_descriptor=self.ledger.snapshot_pages(self.day,known_latest_floor=floor)
                descriptor=native_descriptor
                if final:
                    receipt=self.ledger.export_pages(native_descriptor['handle'],temporary,known_latest_floor=self.ledger.latest_floor)
                    actual=snapshot_semantics(temporary)
                    if any(actual[k]!=native_descriptor[k] for k in actual):
                        raise ValueError('native final export ledger semantics differs')
                    descriptor={**actual,'file':database.name,'sha256':receipt['byte_sha256'],'bytes':receipt['bytes']}
            else:
                descriptor=self.ledger.snapshot(temporary)
            if temporary is not None:
                if temporary.stat().st_size>self.config.max_output_mb*1e6:raise RuntimeError('longitudinal sidecar output resource limit exceeded')
                temporary.rename(database)
                descriptor['file']=database.name
                if native:
                    from .native_checkpoint_owner import observed_file
                    published=observed_file(database)
                    if (published['sha256']!=receipt['byte_sha256'] or published['bytes']!=receipt['bytes']
                            or published['identity'][:4]!=receipt['anchor'][:4]):
                        raise ValueError('native final export changed during sidecar publication')
            envelope={'checkpoint_format_version':CHECKPOINT_FORMAT_VERSION,'state_hash_codec':self._state_hash_codec,
                      'source_sha256':source_hash(),'config_sha256':digest(canonical(self.config.to_dict())),
                      'state_semantic_sha256':self._semantic_digest_with_ledger(descriptor['semantic_sha256']),'ledger':descriptor,'state':self.header()}
            if native_descriptor is not None:envelope['native_checkpoint']=native_descriptor
            def writer(stream):
                stream.write('{')
                for n,k in enumerate(sorted(envelope)):
                    if n:stream.write(',')
                    stream.write(canonical(k));stream.write(':');value=envelope[k]
                    if k=='state':
                        stream.write('{"agents":[')
                        for j,a in enumerate(self.agents):
                            if j:stream.write(',')
                            stream.write(canonical(dataclasses.asdict(a)))
                        stream.write(']')
                        for key in sorted(value):
                            stream.write(',');stream.write(canonical(key));stream.write(':')
                            for chunk in json_chunks(value[key]):stream.write(chunk)
                        stream.write('}')
                    else:stream.write(canonical(value))
                stream.write('}\n')
            atomic_stream(path,writer,max_bytes=int(self.config.max_output_mb*1e6)-(0 if database is None else database.stat().st_size))
        finally:
            if temporary is not None and temporary.exists():temporary.unlink()
        item={'file':path.name,'sha256':file_digest(path),'day':self.day,'source_sha256':envelope['source_sha256'],'config_sha256':envelope['config_sha256'],
                'checkpoint_format_version':CHECKPOINT_FORMAT_VERSION,'state_hash_codec':self._state_hash_codec,
                'state_semantic_sha256':envelope['state_semantic_sha256'],'files':[{'file':p.name,'sha256':file_digest(p),'bytes':p.stat().st_size} for p in ((path,) if database is None else (path,database))]}
        if native:
            item['native_checkpoint']=native_descriptor
            self._native_owner.publish(path,item,self.ledger.latest_floor)
        return item

    @classmethod
    def restore(cls,value,*,storage_dir=None,ledger_snapshot=None,ledger_instance=None,native_owner=None):
        from .model import Agent
        if source_hash()!=IMPORTED_SOURCE_SHA256 or value.get('execution_source_sha256')!=IMPORTED_SOURCE_SHA256:
            raise ValueError('longitudinal restored execution source differs; resume with the original frozen source')
        obj=cls.__new__(cls);obj.config=Config.from_dict(value['config']);obj.l=obj.config.longitudinal
        obj.version = CAUSAL_MODEL_VERSION if obj.config.enterprise_operations is not None else GROWTH_MODEL_VERSION if obj.config.enterprise_growth is not None else cls.version
        obj.operations = EnterpriseOperatingState.restore(obj.config,value.get('enterprise_operating_state'),value['day']) if obj.config.enterprise_operations is not None else None
        if obj.operations is None and 'enterprise_operating_state' in value:raise ValueError('unexpected operating state')
        obj.enterprise = (EnterpriseGrowthState.restore(obj.config,value.get('enterprise_growth_state'),value['day'])
            if obj.config.enterprise_growth is not None else None)
        if obj.enterprise is None and 'enterprise_growth_state' in value:raise ValueError('unexpected enterprise growth state')
        if obj.l is None or value.get('model_version')!=obj.version or value.get('state_schema_version')!=1:raise ValueError('longitudinal state schema differs')
        obj._state_hash_codec=value.get('state_hash_codec',LEGACY_STATE_HASH_CODEC)
        if obj._state_hash_codec not in (STATE_HASH_CODEC,LEGACY_STATE_HASH_CODEC):raise ValueError('unknown longitudinal state hash codec')
        typed=integer_state_maps(value)
        obj.clock=GregorianClock(obj.l)
        obj.rng=WorldRandom(obj.config.seed,obj.config.world,world_context=obj.l.world_context)
        obj.failed_day=False
        obj._native_owner=native_owner
        obj.agents=[Agent(**a) for a in value['agents']]
        for name in ('day','people','target','demand','cash','closed_day','suspended_day','unfunded_workdays','history','authority_change_sum','authority_change_count','last_work_per_present_member','branch_origin','execution_source_sha256'):setattr(obj,name,copy.deepcopy(value[name]))
        for name in ('slots','slot_guild','guild_alias','memory','routine','adoption','migration_paid'):setattr(obj,name,typed[name])
        obj.metrics=defaultdict(float,value['metrics'])
        if not 0<=obj.day<=obj.config.days or len(obj.agents)!=len(obj.people) or len(obj.agents)>(obj.l.max_people_ever or obj.config.n*5):raise ValueError('longitudinal day/population outside bounds')
        if [a.id for a in obj.agents]!=list(range(len(obj.agents))) or [r['id'] for r in obj.people]!=list(range(len(obj.people))):raise ValueError('person identity roster differs')
        if len(set(r['token'] for r in obj.people))!=len(obj.people) or len(set(obj.slots.values()))!=len(obj.slots):raise ValueError('person/exogenous identities reused')
        for slot,i in obj.slots.items():
            if not 0<=i<len(obj.agents) or obj.people[i]['exited_day'] is not None or obj.people[i]['slot']!=slot:raise ValueError('active roster differs from retained people')
        if {r['id'] for r in obj.people if r['exited_day'] is None}!=set(obj.slots.values()):raise ValueError('active eligibility roster differs')
        if len(obj.slots)>(obj.l.max_active_members or obj.config.n):raise ValueError('active member cap exceeded')
        obj.ledger=ledger_instance if ledger_instance is not None else ExactLedger(storage_dir,snapshot=ledger_snapshot)
        obj.ledger.journal_codec=obj._state_hash_codec
        if ledger_snapshot is None and ledger_instance is None:obj.ledger.restore_rows(value['ledger_rows'])
        try:
            obj._topology();obj._authority_signature=(tuple((g,tuple(ids),obj.policy(g)) for g,ids in sorted(obj.members.items())),obj.closed_day,obj.suspended_day)
            obj.validate_semantics();return obj
        except BaseException:
            if ledger_instance is None:obj.ledger.close()
            raise

    def validate_semantics(self):
        if not math.isfinite(self.cash) or self.cash<0:raise ValueError('cash is nonfinite/negative')
        expected=self.config.n*self.l.initial_cash_per_member-self.metrics.get('recruitment_resource_units',0)-self.metrics.get('migration_resource_units',0)-self.metrics.get('operating_resource_units',0)-self.metrics.get('infrastructure_setup_resource_units',0)+self.metrics.get('revenue_resource_units',0)+self.metrics.get('financing_inflow_resource_units',0)
        if not math.isclose(self.cash,expected,rel_tol=1e-12,abs_tol=1e-8):raise ValueError('resource balance does not conserve funds')
        if self.operations is not None:
            self.operations.validate(self.day)
            stock_metrics={'delivered_revenue':'revenue_resource_units',
                'delivered_units':'delivered_product_units','product_units':'produced_work_units',
                'orders_arrived':'orders_arrived_units','orders_expired':'orders_expired_units',
                'borrowed':'financing_inflow_resource_units'}
            for stock,metric in stock_metrics.items():
                if not math.isclose(getattr(self.operations,stock),self.metrics.get(metric,0.),rel_tol=1e-12,abs_tol=1e-8):
                    raise ValueError('operating stock differs from committed metric: '+stock)
            if not math.isclose(self.operations.material_paid+self.operations.logistics_paid,
                    self.metrics.get('enterprise_material_and_logistics_resource_units',0.),rel_tol=1e-12,abs_tol=1e-8):
                raise ValueError('operating material/logistics stock differs from committed metric')
            last=self.ledger.latest_journal_row()
            if self.day==0:
                if (canonical(self.operations.to_dict())!=canonical(EnterpriseOperatingState(self.config).to_dict())
                        or canonical(self.enterprise.to_dict())!=canonical(EnterpriseGrowthState(self.config).to_dict())):
                    raise ValueError('initial enterprise state differs from configuration')
            else:
                if last is None or last[1]!=self.day-1 or last[2]!='day_end':
                    raise ValueError('operating state lacks its final committed day')
                value=json.loads(last[4],parse_constant=lambda v:(_ for _ in ()).throw(ValueError('nonfinite journal value')))
                for name,state in (('metrics_cumulative',self.metrics),
                        ('enterprise_operating_state',self.operations.to_dict()),
                        ('enterprise_growth_state',self.enterprise.to_dict())):
                    if canonical(value.get(name))!=canonical(state):
                        raise ValueError('operating recovery differs from final committed journal: '+name)
        if self.config.enterprise_growth is not None:
            self.enterprise.validate(self.config,self.day)
            if self.target!=self.enterprise.target or len(self.slots)>self.enterprise.capacity:
                raise ValueError('enterprise headcount target/capacity differs')
            if set(self.guild_alias)!=set(range(self.guild_count)):
                raise ValueError('enterprise domain inventory differs')
            domain_counts=[0]*self.guild_count;site_counts=[0]*self.site_count
            for i in self.slots.values():
                a=self.agents[i]
                if type(a.guild) is not int or not 0<=a.guild<self.guild_count or type(a.site) is not int or not 0<=a.site<self.site_count:
                    raise ValueError('enterprise person domain/site outside installed inventory')
                domain_counts[a.guild]+=1;site_counts[a.site]+=1
            if max(domain_counts)>self.enterprise.c.members_per_guild or max(site_counts)>self.enterprise.c.members_per_site:
                raise ValueError('enterprise person allocation overbooks installed units')
        for i,a in enumerate(self.agents):
            for field,v in dataclasses.asdict(a).items():
                if isinstance(v,float) and not math.isfinite(v):raise ValueError('nonfinite person state')
            r=self.people[i];date.fromisoformat(r['birth_date'])
            if date.fromisoformat(r['birth_date'])>self.clock.at(r['entered_day']):raise ValueError('person born after entering')
            if r['entered_day']>self.day or r['entered_day']<0 or (r['exited_day'] is not None and not r['entered_day']<=r['exited_day']<self.day):raise ValueError('invalid lifecycle dates')
            if r['active_workdays']>r['active_calendar_days'] or r['present_workdays']>r['active_workdays'] or r['available_work_hours']<0 or r['post_adoption_available_work_hours']>r['available_work_hours']:raise ValueError('invalid exposure accounting')
        if sum(r['active_workdays'] for r in self.people)!=self.metrics.get('active_member_workdays',0):raise ValueError('member exposure does not equal the aggregate')
        if sum(r['active_calendar_days'] for r in self.people)!=self.metrics.get('active_member_calendar_days',0) or sum(r['present_workdays'] for r in self.people)!=self.metrics.get('present_member_workdays',0):raise ValueError('calendar/present exposure does not equal aggregate')
        for key,amount in [('available_member_work_hours',math.fsum(r['available_work_hours'] for r in self.people)),('present_member_work_hours',math.fsum(r['present_workdays']*(1-self.agents[r['id']].care_hours) for r in self.people))]:
            if not math.isclose(self.metrics.get(key,0.),amount,rel_tol=1e-11,abs_tol=1e-8):raise ValueError('member-hour exposure does not equal aggregate')
        for row in self.ledger.rows('pending'):
            if row[1] not in {'review','appeal','commit'} or not 0<=row[2]<self.guild_count or not 0<=row[6]<len(self.agents) or not 0<=row[7]<self.day or row[3]<row[7] or not math.isfinite(row[4]) or not math.isfinite(row[8]) or row[8]<0 or any(v not in (0,1) for v in row[9:]):raise ValueError('pending evidence semantic validation failed')
        for person,guild,amount in self.ledger.rows('credits'):
            if not 0<=person<len(self.agents) or not 0<=guild<self.guild_count or not math.isfinite(amount) or amount<0:raise ValueError('credit semantic validation failed')

    def fork(self,new_config,*,storage_dir=None,page_options=None,native_owner_dir=None):
        if self._state_hash_codec!=STATE_HASH_CODEC:raise ValueError('legacy checkpoint is inspection-only; fork with the original frozen implementation')
        new_config.validate()
        if source_hash()!=self.execution_source_sha256:raise ValueError('branch parent execution source changed')
        if new_config.longitudinal is None:raise ValueError('cannot fork a longitudinal state into the legacy model')
        old=self.config.to_dict();new=new_config.to_dict()
        allowed={'regime','backend','days','alpha','update_interval_days','review_capacity_per_member_day','appeal_capacity_per_member_day','review_error_sd','max_wall_seconds','max_output_mb','max_rss_mb','max_events'}
        long_allowed={'adoption_mode','adoption_day','guild_adoption_days','observable_trigger','trigger_threshold','trigger_not_before_day','transition_days','training_hours_per_workday','migration_resource_units_per_member','dual_run_resource_units_per_member_workday'}
        for k in old:
            if k not in allowed|{'longitudinal'} and old[k]!=new[k]:raise ValueError('fork changes shared world/history field: '+k)
        for k in old['longitudinal']:
            if k=='holidays':
                boundary=self.clock.at(self.day)
                if [v for v in old['longitudinal'][k] if date.fromisoformat(v)<boundary]!=[v for v in new['longitudinal'][k] if date.fromisoformat(v)<boundary]:raise ValueError('fork changes an observed holiday calendar')
                continue
            if k in {'workforce_targets','exit_schedule','guild_moves','guild_mergers','demand_schedule'}:
                if [r for r in old['longitudinal'][k] if r[0]<self.day]!=[r for r in new['longitudinal'][k] if r[0]<self.day]:raise ValueError('fork rewrites an observed schedule: '+k)
                continue
            if k in {'work_creation_stop_day','closure_day','suspension_day'}:
                a,b=old['longitudinal'][k],new['longitudinal'][k]
                if a!=b and (a is not None and a<self.day or b is not None and b<self.day):raise ValueError('fork changes an observed cutoff: '+k)
                continue
            if k not in long_allowed and old['longitudinal'][k]!=new['longitudinal'][k]:raise ValueError('fork changes shared longitudinal state assumption: '+k)
        if any(v is not None for v in self.adoption.values()):raise ValueError('branch parent already adopted; use an unadopted complete prestate')
        if new_config.days<self.day:raise ValueError('branch horizon precedes shared parent state')
        if new_config.longitudinal.adoption_mode=='fixed' and new_config.longitudinal.adoption_day<self.day:raise ValueError('cannot backdate adoption into shared history')
        if new_config.longitudinal.adoption_mode=='staged' and any(when<self.day for _,when in new_config.longitudinal.guild_adoption_days):raise ValueError('cannot backdate staged adoption')
        header=copy.deepcopy(self.header());header['agents']=[dataclasses.asdict(a) for a in self.agents]
        # Disk backup avoids materializing the O(N*T) journal for a branch.
        directory=Path(storage_dir) if storage_dir is not None else Path(__import__('tempfile').mkdtemp(prefix='dams-branch-'))
        directory.mkdir(parents=True,exist_ok=True)
        if self.failed_day or self.ledger.db.in_transaction:raise ValueError('branch parent is not at a committed day boundary')
        if getattr(self.ledger,'native_pages_active',False):
            if page_options is None:raise ValueError('native scientific fork requires a fresh operational storage branch')
            descriptor=self.ledger.snapshot_pages(self.day,known_latest_floor=self.ledger.latest_floor)
            ledger=ExactLedger.from_pages(directory,descriptor,page_options=page_options,known_latest_floor=self.ledger.latest_floor,
                verify_export=lambda database,desc,receipt:verify_native_image(header,database,desc))
            child=self.restore(header,storage_dir=directory,ledger_instance=ledger)
        else:
            if page_options is not None:raise ValueError('native fork cannot relabel a legacy parent; use native storage from initialization')
            child=self.restore(header,storage_dir=directory,ledger_snapshot=self.ledger.path)
        child.config=new_config;child.l=new_config.longitudinal;child.clock=GregorianClock(child.l)
        child.branch_origin={'parent_source_sha256':source_hash(),'parent_config_sha256':digest(canonical(self.config.to_dict())),'parent_day':self.day,'parent_state_semantic_sha256':self.semantic_digest()}
        if page_options is not None:
            from .native_checkpoint_owner import CheckpointOwner,default_owner_directory
            child._native_owner=CheckpointOwner(native_owner_dir or default_owner_directory(page_options,digest(canonical(new_config.to_dict()))),
                source_sha256=source_hash(),config_sha256=digest(canonical(new_config.to_dict())))
            if child._native_owner.latest() is not None:raise ValueError('native scientific fork owner namespace already exists')
        return child


def verify_native_image(state,database,descriptor,*,expected_state_sha256=None,expected_source_sha256=None):
    """Actual exported image rows plus the unchanged full engine-state codec."""
    actual=snapshot_semantics(database)
    if any(actual[k]!=descriptor[k] for k in actual):raise ValueError('native exported ledger semantics mismatch')
    codec=state.get('state_hash_codec',LEGACY_STATE_HASH_CODEC)
    header={k:v for k,v in state.items() if k!='agents'}
    integer_state_maps(header)
    h=hashlib.sha256();hash_state_json(h,header,codec)
    for agent in state['agents']:h.update(b'\n');h.update(canonical(agent))
    h.update(b'\n');h.update(actual['semantic_sha256'].encode())
    if expected_state_sha256 is not None and h.hexdigest()!=expected_state_sha256:
        raise ValueError('longitudinal checkpoint full state digest mismatch')
    validate_persisted_state(state,database,expected_source_sha256=expected_source_sha256)
    return h.hexdigest()


def load_snapshot_envelope(path,*,expected_config=None,expected_source_sha256=None):
    """Decode the exact no-follow bytes read under an original FD observation."""
    from ._committed_pages import Directory,identity
    import os
    if source_hash()!=IMPORTED_SOURCE_SHA256:raise ValueError('longitudinal verifier source changed after import; restart from a frozen source')
    path=Path(path).absolute();directory=Directory(path.parent)
    try:
        info=os.stat(path.name,dir_fd=directory.fd,follow_symlinks=False)
        raw,anchor=directory.read_observed(path.name,info.st_size)
        def pairs(items):
            value={}
            for key,item in items:
                if key in value:raise ValueError('duplicate checkpoint JSON object key')
                value[key]=item
            return value
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite checkpoint JSON value')))
        if identity(os.stat(path.name,dir_fd=directory.fd,follow_symlinks=False))!=anchor:
            raise ValueError('longitudinal checkpoint envelope changed during decode')
    finally:directory.close()
    checkpoint_codec(value)
    expected_source=source_hash() if expected_source_sha256 is None else expected_source_sha256
    if value['source_sha256']!=expected_source or value['state'].get('execution_source_sha256')!=expected_source:
        raise ValueError('longitudinal checkpoint source differs')
    config=Config.from_dict(value['state']['config'])
    if value['config_sha256']!=digest(canonical(config.to_dict())) or expected_config is not None and config.to_dict()!=expected_config.to_dict():
        raise ValueError('longitudinal checkpoint config differs')
    if len(raw)>config.max_output_mb*1e6:raise ValueError('longitudinal checkpoint envelope exceeds declared output limit')
    return value,{'path':str(path),'sha256':digest(raw),'bytes':len(raw),'identity':list(anchor)}


def verify_snapshot(path,*,expected_config=None,expected_source_sha256=None,page_options=None,known_latest_floor=None,export_dir=None,native_floor_observer=None):
    """Pure-I/O verification; an explicit historical source never enables resume.

    Historical checkpoints retain their original source/model/hash identities.
    Consumers may inspect them by specifying the independently retained source
    hash. Model restoration still requires the executing frozen source to match.
    """
    if source_hash()!=IMPORTED_SOURCE_SHA256:raise ValueError('longitudinal verifier source changed after import; restart from a frozen source')
    path=Path(path);value,_=load_snapshot_envelope(path,expected_config=expected_config,expected_source_sha256=expected_source_sha256);codec=checkpoint_codec(value)
    expected_source=source_hash() if expected_source_sha256 is None else expected_source_sha256
    if value['source_sha256']!=expected_source or value['state'].get('execution_source_sha256')!=expected_source:
        raise ValueError('longitudinal checkpoint source differs')
    config=Config.from_dict(value['state']['config'])
    if value['config_sha256']!=digest(canonical(config.to_dict())) or expected_config is not None and config.to_dict()!=expected_config.to_dict():raise ValueError('longitudinal checkpoint config differs')
    side=value['ledger']
    native=value.get('native_checkpoint')
    if side.get('backend')=='native-committed-pages-v1':
        if native!=side or page_options is None or known_latest_floor is None or export_dir is None:
            raise ValueError('native checkpoint requires options, external latest floor and owned export directory')
        from .native_page_backend import export_existing,NativePageBackend
        from .longitudinal_storage import TABLE_COLUMNS
        export_dir=Path(export_dir);export_dir.mkdir(parents=True,exist_ok=True)
        database=export_dir/(path.stem+'.sqlite')
        receipt=export_existing(page_options,native,database,known_latest_floor=known_latest_floor,tables=TABLE_COLUMNS)
        if receipt['bytes']!=native['bytes']:raise ValueError('native export geometry differs')
        verify_native_image(value['state'],database,native,expected_state_sha256=value['state_semantic_sha256'],expected_source_sha256=expected_source)
        NativePageBackend.verify_export(database,receipt)
        if native_floor_observer is not None:native_floor_observer(receipt['latest_floor'])
        NativePageBackend.verify_export(database,receipt)
        return value,database
    database=path.parent/side['file']
    if database.parent.resolve()!=path.parent.resolve() or database.is_symlink() or not database.is_file() or file_digest(database)!=side['sha256'] or database.stat().st_size!=side['bytes']:raise ValueError('longitudinal checkpoint ledger integrity mismatch')
    actual=snapshot_semantics(database)
    if any(actual[k]!=side[k] for k in actual):raise ValueError('longitudinal checkpoint ledger semantics mismatch')
    state=value['state'];header={k:v for k,v in state.items() if k!='agents'}
    integer_state_maps(header)
    h=hashlib.sha256();hash_state_json(h,header,codec)
    for a in state['agents']:h.update(b'\n');h.update(canonical(a))
    h.update(b'\n');h.update(actual['semantic_sha256'].encode())
    if h.hexdigest()!=value['state_semantic_sha256']:raise ValueError('longitudinal checkpoint full state digest mismatch')
    validate_persisted_state(state,database,expected_source_sha256=expected_source)
    if native is not None:
        if page_options is None or known_latest_floor is None or export_dir is None:
            raise ValueError('native final state requires options, external latest floor and owned export directory')
        from .native_page_backend import export_existing,NativePageBackend
        from .longitudinal_storage import TABLE_COLUMNS
        export_dir=Path(export_dir);export_dir.mkdir(parents=True,exist_ok=True)
        exported=export_dir/(path.stem+'.sqlite')
        receipt=export_existing(page_options,native,exported,known_latest_floor=known_latest_floor,tables=TABLE_COLUMNS)
        if receipt['byte_sha256']!=side['sha256'] or receipt['bytes']!=side['bytes']:
            raise ValueError('native final CAS/exported flat SQLite bytes differ')
        if any(native[k]!=actual[k] for k in actual) or native['day']!=state['day'] or native['bytes']!=side['bytes']:
            raise ValueError('native final checkpoint/flat ledger binding differs')
        NativePageBackend.verify_export(exported,receipt)
        if native_floor_observer is not None:native_floor_observer(receipt['latest_floor'])
        NativePageBackend.verify_export(exported,receipt)
    return value,database


def write_recovery_receipt(path,destination,*,expected_source_sha256,expected_config=None):
    """Verify original bytes and save a new IO-only receipt, without migration.

    A receipt does not relabel the checkpoint, certify new-source results, or
    authorize execution under a different source. Restore verified state using
    the original frozen implementation; retain this verifier's source separately.
    """
    value,database=verify_snapshot(path,expected_config=expected_config,expected_source_sha256=expected_source_sha256)
    path=Path(path);destination=Path(destination)
    if destination.exists():raise ValueError('immutable checkpoint recovery receipt already exists')
    if source_hash()!=IMPORTED_SOURCE_SHA256:raise ValueError('longitudinal verifier source changed during checkpoint verification')
    receipt={'recovery_receipt_version':1,'status':'verified-original-checkpoint-io-only',
             'verifier_source_sha256':IMPORTED_SOURCE_SHA256,'original_source_sha256':value['source_sha256'],
             'original_model_version':value['state']['model_version'],'original_state_schema_version':value['state']['state_schema_version'],
             'original_checkpoint_format_version':value.get('checkpoint_format_version',1),
             'original_state_hash_codec':checkpoint_codec(value),'original_state_semantic_sha256':value['state_semantic_sha256'],
             'original_ledger':copy.deepcopy(value['ledger']),
             'config_sha256':value['config_sha256'],'day':value['state']['day'],
             'resume_requires_original_execution_source':True,
             'files':[{'file':str(p.resolve()),'sha256':file_digest(p),'bytes':p.stat().st_size} for p in (path,database)]}
    atomic_stream(destination,lambda stream:stream.write(canonical(receipt)+b'\n'))
    return receipt


def validate_persisted_state(state,database,*,expected_source_sha256=None):
    """Pure read-only lifecycle, resource, event and exposure consistency gate."""
    from types import SimpleNamespace
    import sqlite3
    from .model import Agent
    config=Config.from_dict(state['config']);l=config.longitudinal
    expected_source=source_hash() if expected_source_sha256 is None else expected_source_sha256
    if state.get('execution_source_sha256')!=expected_source:raise ValueError('persisted execution source differs')
    enterprise = (EnterpriseGrowthState.restore(config,state.get('enterprise_growth_state'),state['day'])
        if config.enterprise_growth is not None else None)
    if enterprise is None and 'enterprise_growth_state' in state:raise ValueError('unexpected persisted enterprise state')
    guild_count = enterprise.guilds if enterprise is not None else config.guilds
    operations=EnterpriseOperatingState.restore(config,state.get('enterprise_operating_state'),state['day']) if config.enterprise_operations is not None else None
    if operations is None and 'enterprise_operating_state' in state:raise ValueError('unexpected operating persisted state')
    expected_model = CAUSAL_MODEL_VERSION if operations is not None else GROWTH_MODEL_VERSION if enterprise is not None else 'longitudinal-1'
    if l is None or state.get('model_version')!=expected_model or state.get('state_schema_version')!=1:raise ValueError('persisted longitudinal schema differs')
    codec=state.get('state_hash_codec',LEGACY_STATE_HASH_CODEC)
    if codec not in (STATE_HASH_CODEC,LEGACY_STATE_HASH_CODEC):raise ValueError('unknown persisted longitudinal hash codec')
    typed=integer_state_maps(state)
    day=state['day'];people=state['people'];agents=[Agent(**v) for v in state['agents']]
    if type(day) is not int or not 0<=day<=config.days:raise ValueError('persisted day differs from horizon')
    if len(agents)!=len(people) or not config.n<=len(agents)<=(l.max_people_ever or config.n*5):raise ValueError('persisted ever-person count differs')
    if [a.id for a in agents]!=list(range(len(agents))) or [r['id'] for r in people]!=list(range(len(people))):raise ValueError('persisted identity roster differs')
    if len({r['token'] for r in people})!=len(people):raise ValueError('persisted external identities reused')
    slots=typed['slots']
    if len(slots)>(l.max_active_members or config.n) or len(set(slots.values()))!=len(slots):raise ValueError('persisted active slots exceed capacity or reuse identity')
    if set(slots.values())!={r['id'] for r in people if r['exited_day'] is None} or any(people[i]['slot']!=slot for slot,i in slots.items()):raise ValueError('persisted active eligibility differs')
    aliases=typed['guild_alias']
    if set(aliases)!=set(range(guild_count)) or any(type(v) is not int or v not in aliases or aliases[v]!=v for v in aliases.values()):raise ValueError('persisted guild alias map invalid')
    for name in ('memory','routine','adoption','migration_paid'):
        if {int(k) for k in state[name]}!=set(range(guild_count)):raise ValueError('persisted guild state inventory differs')
    for a in agents:
        if type(a.guild) is not int or not 0<=a.guild<guild_count or a.skill<=0 or not 0<=a.care_hours<=.3 or not 0<=a.share<=1:raise ValueError('persisted agent bounds invalid')
        if people[a.id]['exited_day'] is not None and a.share!=0:raise ValueError('inactive person retained authority')
    for key in ('closed_day','suspended_day'):
        value=state[key]
        if value is not None and (type(value) is not int or not 0<=value<day):raise ValueError('persisted closure/suspension date invalid')
    for r in people:
        for key in ('active_calendar_days','active_workdays','present_workdays','entered_day'):
            if type(r[key]) is not int or r[key]<0:raise ValueError('persisted exposure must be nonnegative integer counts')
        if r['active_calendar_days']>day-r['entered_day']:raise ValueError('persisted member exposure exceeds elapsed time')
    clock=GregorianClock(l)
    # This is a complete sealed flat snapshot, not a live WAL database. SQLite's
    # mode=ro alone can create WAL/SHM beside a WAL-header export and mutate the
    # retained output roster. Byte/semantic gates bind this exact flat image;
    # immutable=1 prevents a validation reader from creating recovery sidecars.
    db=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro&immutable=1',uri=True);ledger=ExactLedger.__new__(ExactLedger);ledger.db=db
    try:
        ledger.validate_schema()
        view=SimpleNamespace(config=config,l=l,clock=clock,day=day,agents=agents,people=people,cash=state['cash'],metrics=state['metrics'],ledger=ledger,
            operations=operations,enterprise=enterprise,guild_count=guild_count,site_count=enterprise.sites if enterprise is not None else config.sites,target=state['target'],slots=slots,guild_alias=aliases)
        LongitudinalEngine.validate_semantics(view)
        expected_day=0
        for seq,when,kind,event,payload in ledger.rows('journal'):
            if type(seq) is not int or seq<1 or type(when) is not int or when<0 or when>=max(1,day):raise ValueError('persisted journal date/sequence invalid')
            value=json.loads(payload,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('nonfinite journal value')))
            if journal_json(value,codec=codec,kind=kind)!=payload:raise ValueError('persisted journal payload not canonical')
            if kind=='day_end':
                if when!=expected_day or value['date']!=clock.at(when).isoformat():raise ValueError('persisted journal day roster incomplete/duplicate')
                expected_day+=1
                if when==day-1:
                    if value['metrics_cumulative']!=state['metrics']:raise ValueError('final persisted event metrics differ from state')
                    if operations is not None:
                        for name in ('enterprise_operating_state','enterprise_growth_state'):
                            if canonical(value.get(name))!=canonical(state[name]):
                                raise ValueError('final persisted enterprise journal differs from state: '+name)
        if expected_day!=day:raise ValueError('persisted journal day count differs from state')
        previous=-1
        for row in state['history']:
            if type(row['day']) is not int or not previous<row['day']<day or row['calendar_date']!=clock.at(row['day']).isoformat():raise ValueError('persisted trace date/order invalid')
            previous=row['day']
    finally:db.close()
    return {'valid':True,'day':day,'ever_people':len(agents),'active_person_roster':len(slots),'work_events':state['metrics'].get('work_events',0.)}
