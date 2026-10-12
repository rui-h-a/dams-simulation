"""Bounded pure-state controls, not formal worlds or empirical validation."""
import copy
import dataclasses
import importlib.util
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from dams_sim.organization_tasks import OrganizationTasksState, OrganizationTasksConfig, topology, model_version
from dams_sim.joint_firm import references, references_from_snapshot, JointRun, JointTerms
from research_tools.validate_organization_tasks import _links

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('r9_task_reference',HERE/'fixtures/task-structure-r9-reference/organization_tasks.py')
reference=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reference;spec.loader.exec_module(reference)


def config(version=2):
    raw=json.loads((HERE.parent/'candidate-evidence/calendar-networks-r7.json').read_bytes())
    raw.pop('enterprise_acquisition',None);raw['organization_tasks']['schema_version']=version
    # Pure fixtures declare a 3-day construction/3-day integration boundary.
    raw['enterprise_growth'].update(initial_departments=2,construction_delay_days=3,hiring_delay_days=3)
    return Config.from_dict(raw)


def installation(cfg, installed=3, day=8):
    return {'capacity':35,'guilds':5,'departments':installed,'sites':3,'target':35,
            'requested_day':day-cfg.enterprise_growth.construction_delay_days,'ready_day':day}


def day_step(state,day,active=tuple(range(30)),present=None,funding=1.):
    present=list(active) if present is None else list(present)
    result=state.prepare(day,list(active),present,{i:.8 for i in present},{i:.5 for i in present},funding=funding)
    potential=state.task_allocation({i:result[3][i]*funding for i in present})
    record=state.finish(day,potential)
    return result,record


class TaskStructureTests(unittest.TestCase):
    def at_installation(self):
        cfg=config();state=OrganizationTasksState(cfg)
        for day in range(8):day_step(state,day)
        request=state.completed_installation(8,installation(cfg));return cfg,state,request

    def ready(self):
        cfg,state,q=self.at_installation()
        for day in range(8,q['ready_day']):
            self.assertEqual(state.advance_structure(day,list(range(30)),operating=True),[])
            day_step(state,day)
        return cfg,state,q

    def test_v1_config_and_sixty_day_state_bytes_match_preserved_r9(self):
        cfg=config(1);old=reference.OrganizationTasksState(cfg);new=OrganizationTasksState(cfg)
        self.assertEqual(dataclasses.asdict(cfg.organization_tasks),dataclasses.asdict(reference.OrganizationTasksConfig.from_dict(dataclasses.asdict(cfg.organization_tasks))))
        for day in range(60):
            active=list(range(30+(day//20)));present=[i for i in active if i!=day%len(active)]
            available={i:.8 for i in present};cooperation={i:.5 for i in present}
            for state in (old,new):
                result=state.prepare(day,active,present,available,cooperation,funding=.9)
                produced=state.task_allocation({i:result[3][i]*.9 for i in present});state.finish(day,produced)
            self.assertEqual(json.dumps(new.to_dict(),sort_keys=True),json.dumps(old.to_dict(),sort_keys=True))
            new.validate(day+1);old.validate(day+1)
        self.assertEqual(model_version(cfg),reference.VERSION)
        self.assertIsNone(new.completed_installation(60,installation(cfg,day=60)))

    def test_explicit_schema2_rejects_unpaid_growth_missing_operations_and_capsules(self):
        cfg=config()
        for change in ({'enterprise_growth':None},{'enterprise_operations':None},
                       {'enterprise_growth':dataclasses.replace(cfg.enterprise_growth,department_setup_resource_units=0.)},
                       {'enterprise_operations':dataclasses.replace(cfg.enterprise_operations,entrant_training_workday_units=0.)}):
            with self.subTest(change=change),self.assertRaises(ValueError):dataclasses.replace(cfg,**change).validate()
        self.assertNotEqual(model_version(cfg),reference.VERSION)

    def test_only_completed_installation_starts_delayed_paid_subset_once(self):
        cfg,state,q=self.at_installation()
        self.assertEqual(q['ready_day'],8+cfg.enterprise_growth.hiring_delay_days)
        self.assertEqual(q['setup_cost_subset_resource_units'],cfg.enterprise_growth.department_setup_resource_units)
        self.assertEqual(state.c.department_count,2)
        self.assertIsNone(state.completed_installation(8,installation(cfg)))
        for bad in ({**installation(cfg),'ready_day':7},{**installation(cfg),'departments':1}):
            with self.assertRaises(ValueError):state.completed_installation(8,bad)

    def test_zero_service_cannot_commit_and_untouched_department_membership_stays_exact(self):
        cfg,state,q=self.ready();before=dict(state.structure['assignments'])
        original_links=topology(list(range(30)),state.c,before)
        state.advance_structure(q['ready_day'],list(range(30)),operating=True)
        for day in range(q['ready_day'],q['ready_day']+3):
            if day>q['ready_day']:self.assertEqual(state.advance_structure(day,list(range(30)),operating=True),[])
            day_step(state,day,funding=0.)
            self.assertEqual(state.c.department_count,2);self.assertEqual(state.structure['assignments'],before)
        day=q['ready_day']+3;day_step(state,day)
        state.advance_structure(day+1,list(range(30)),operating=True)
        for i in (1,3,5):self.assertEqual(state.department(i),before[str(i)])
        self.assertEqual(state.c.department_count,3)
        new_links=topology(list(range(30)),state.c,state.structure['assignments'])
        for i in range(1,30,2):
            self.assertEqual(new_links['reporting_parent'][str(i)],original_links['reporting_parent'][str(i)])
            self.assertEqual(new_links['reporting_depth'][str(i)],original_links['reporting_depth'][str(i)])
        self.assertIsNone(new_links['reporting_parent']['16'])
        self.assertIsNotNone(original_links['reporting_parent']['16'])

    def test_zero_production_split_conserves_every_stock_without_new_seed(self):
        cfg=config();cfg=dataclasses.replace(cfg,organization_tasks=dataclasses.replace(cfg.organization_tasks,
            department_objectives=((0.,0.,1.),(0.,0.,1.)))).validate()
        state=OrganizationTasksState(cfg)
        for day in range(8):day_step(state,day)
        q=state.completed_installation(8,installation(cfg))
        for day in range(8,q['ready_day']):day_step(state,day)
        state.advance_structure(q['ready_day'],list(range(30)),operating=True);day_step(state,q['ready_day'])
        state.advance_structure(q['ready_day']+1,list(range(30)),operating=True);day_step(state,q['ready_day']+1)
        self.assertEqual(math.fsum(state.generated.values()),0.);self.assertEqual(math.fsum(state.consumed.values()),0.)
        self.assertEqual(math.fsum(state.stocks.values()),2.);self.assertEqual(math.fsum(state.structure['initial_units'].values()),2.)
        self.assertLess(state.stocks['2'],1.)

    def test_same_day_reentry_and_departed_mover_sunk_cancelled_replanning(self):
        cfg,state,q=self.ready();planned=state.advance_structure(q['ready_day'],list(range(30)),operating=True)
        owed=dict(state.structure['training_remaining'])
        self.assertEqual(state.advance_structure(q['ready_day'],list(range(30)),operating=True),[])
        self.assertEqual(state.structure['training_remaining'],owed)
        moving=int(next(iter(owed)));present=[i for i in range(30) if i!=moving]
        day_step(state,q['ready_day'],active=present)
        records=state.advance_structure(q['ready_day']+1,present,operating=True)
        self.assertEqual(records[0][0],'task_reorganization_replan')
        cancellation=records[0][1]
        self.assertEqual(cancellation['cancelled_unserved_hours_by_person'][str(moving)],owed[str(moving)])
        self.assertEqual(cancellation['served_work_hours_by_person'][str(moving)],0.)
        self.assertNotIn(str(moving),state.structure['training_remaining'])
        self.assertEqual(state.c.department_count,2)
        with self.assertRaises(ValueError):state.prepare(q['ready_day'],present,present,{i:.8 for i in present},{i:.5 for i in present},funding=1.)
        for day in range(q['ready_day']+1,cancellation['replan_not_before']):
            self.assertEqual(state.advance_structure(day,present,operating=True),[]);day_step(state,day,active=present)
            restored=OrganizationTasksState.restore(cfg,state.to_dict(),day+1)
            self.assertEqual(restored.to_dict(),state.to_dict())
        day=cancellation['replan_not_before']
        self.assertEqual(state.advance_structure(day,present,operating=True)[0][0],'task_reorganization_planned')
        self.assertGreater(sum(state.structure['training_remaining'].values()),0.)
        day_step(state,day,active=present)
        self.assertEqual(state.advance_structure(day+1,present,operating=True)[0][0],'task_reorganization_complete')
        day_step(state,day+1,active=present);state.validate(day+2)
        restored=OrganizationTasksState.restore(cfg,state.to_dict(),day+2)
        self.assertEqual(restored.to_dict(),state.to_dict());self.assertEqual(len(restored.structure['replans']),1)

    def test_split_inherits_task_technology_without_new_dependency_stage(self):
        cfg,state,q=self.ready();state.advance_structure(q['ready_day'],list(range(30)),operating=True)
        day_step(state,q['ready_day']);state.advance_structure(q['ready_day']+1,list(range(30)),operating=True)
        self.assertEqual(state.structure['task_families'],{'0':0,'1':1,'2':0})
        # Only an accounting counterexample: no morning tokens in either
        # family. Split source-family work still needs NO predecessor; the
        # original downstream family remains constrained by family0 tokens.
        state.stocks={d:0. for d in state.stocks}
        state.prepare(q['ready_day']+1,list(range(30)),list(range(30)),{i:.8 for i in range(30)},{i:.5 for i in range(30)},funding=1.)
        produced=state.task_allocation({i:1. for i in range(30)})
        for i in range(30):self.assertEqual(produced[i],0. if state.department(i)==1 else 1.)
        record=state.finish(q['ready_day']+1,produced)
        self.assertEqual(sum(record['consumed_input_units'].values()),0.)

    def test_split_family_pool_has_one_common_factor_and_proportional_consumption(self):
        cfg,state,q=self.ready();state.advance_structure(q['ready_day'],list(range(30)),operating=True)
        day_step(state,q['ready_day']);state.advance_structure(q['ready_day']+1,list(range(30)),operating=True)
        state.stocks={'0':.3,'1':0.,'2':.7}
        state.prepare(q['ready_day']+1,list(range(30)),list(range(30)),{i:.8 for i in range(30)},{i:.5 for i in range(30)},funding=1.)
        produced=state.task_allocation({i:1. for i in range(30)})
        rec=state.finish(q['ready_day']+1,produced)
        self.assertAlmostEqual(rec['consumed_input_units']['0'],.3)
        self.assertAlmostEqual(rec['consumed_input_units']['2'],.7)
        self.assertEqual(rec['consumed_input_units']['1'],0.)
        self.assertEqual(rec['dependency_factors']['0'],rec['dependency_factors']['2'])

    def test_split_conserves_initial_and_accumulated_knowledge_with_new_roles(self):
        cfg,state,q=self.ready();before={k:math.fsum(getattr(state,k).values()) for k in ('stocks','generated','consumed')}
        initial=math.fsum(state.structure['initial_units'].values());old=copy.deepcopy(state.to_dict())
        planning=state.advance_structure(q['ready_day'],list(range(30)),operating=True)
        self.assertEqual(planning[0][0],'task_reorganization_planned');self.assertEqual(state.c.department_count,2)
        self.assertEqual(state.stocks,old['stocks'])
        day_step(state,q['ready_day'])
        before={k:math.fsum(getattr(state,k).values()) for k in before};old=copy.deepcopy(state.to_dict())
        records=state.advance_structure(q['ready_day']+1,list(range(30)),operating=True)
        self.assertEqual(len(records),1);self.assertEqual(records[0][0],'task_reorganization_complete');self.assertEqual(state.c.department_count,3)
        records=[payload for kind,payload in records]
        for k,total in before.items():self.assertAlmostEqual(math.fsum(getattr(state,k).values()),total)
        self.assertEqual(math.fsum(state.structure['initial_units'].values()),initial)
        self.assertEqual(records[0]['stocks_before']['stocks'],old['stocks'])
        self.assertNotEqual(state.structure['assignments'],old['structure']['assignments'])
        result,record=day_step(state,q['ready_day']+1);state.validate(q['ready_day']+2)
        self.assertEqual(len(result[-1]['topology']['department_members']),3)
        self.assertEqual(result[-1]['topology'],_links(list(range(30)),state.c,state.structure['assignments']))

    def test_real_paid_time_reduces_production_and_is_not_new_cash_expense(self):
        cfg,state,q=self.ready();state.advance_structure(q['ready_day'],list(range(30)),operating=True)
        without=copy.deepcopy(state);without.structure['training_remaining']={}
        first,_=day_step(state,q['ready_day']);second,_=day_step(without,q['ready_day'])
        self.assertGreater(sum(a['reorganization_hours'] for a in first[-1]['allocations'].values()),0.)
        self.assertLess(sum(first[3].values()),sum(second[3].values()))
        self.assertNotIn('cash',state.to_dict());self.assertTrue(all(v==0. for v in state.structure['training_remaining'].values()))

    def test_absence_and_zero_funding_preserve_training_debt_without_free_production(self):
        cfg,state,q=self.ready();state.advance_structure(q['ready_day'],list(range(30)),operating=True)
        mover=int(next(k for k,v in state.structure['training_remaining'].items() if v>0))
        before=state.structure['training_remaining'][str(mover)]
        result,_=day_step(state,q['ready_day'],present=[i for i in range(30) if i!=mover],funding=0.)
        self.assertEqual(state.structure['training_remaining'][str(mover)],before)
        next_day=q['ready_day']+1
        result,_=day_step(state,next_day,funding=0.)
        self.assertEqual(result[3][mover]*0.,0.)
        self.assertEqual(state.c.department_count,2)
        self.assertEqual(state.structure['training_remaining'][str(mover)],before)
        result,_=day_step(state,next_day+1)
        self.assertGreater(result[3][mover],0.)
        state.validate(next_day+2)

    def test_closed_or_nonworking_company_preserves_pending_split(self):
        cfg,state,q=self.ready();before=copy.deepcopy(state.to_dict())
        self.assertEqual(state.advance_structure(q['ready_day'],list(range(30)),operating=False),[])
        self.assertEqual(state.to_dict(),before)

    def test_before_and_after_split_checkpoint_state_restore_exact_and_fork_refused(self):
        cfg,state,q=self.ready();before=OrganizationTasksState.restore(cfg,state.to_dict(),q['ready_day'])
        self.assertEqual(before.to_dict(),state.to_dict())
        for s in (state,before):s.advance_structure(q['ready_day'],list(range(30)),operating=True);day_step(s,q['ready_day'],funding=.5)
        self.assertEqual(before.to_dict(),state.to_dict())
        partial=OrganizationTasksState.restore(cfg,state.to_dict(),q['ready_day']+1)
        for s in (state,partial):
            self.assertEqual(s.advance_structure(q['ready_day']+1,list(range(30)),operating=True),[])
            day_step(s,q['ready_day']+1)
        self.assertEqual(state.to_dict(),partial.to_dict())
        ready=OrganizationTasksState.restore(cfg,state.to_dict(),q['ready_day']+2)
        for s in (state,ready):s.advance_structure(q['ready_day']+2,list(range(30)),operating=True);day_step(s,q['ready_day']+2)
        self.assertEqual(state.to_dict(),ready.to_dict())
        restored=OrganizationTasksState.restore(cfg,state.to_dict(),q['ready_day']+3)
        self.assertEqual(restored.to_dict(),state.to_dict())
        with self.assertRaises(ValueError):OrganizationTasksState.restore(config(1),state.to_dict(),q['ready_day']+3)
        bad=copy.deepcopy(state.to_dict());bad['structure']['initial_units']['2']+=1.
        with self.assertRaises(ValueError):OrganizationTasksState.restore(cfg,bad,q['ready_day']+3)

    def test_guild_count_and_reporting_span_do_not_trigger_task_stock_creation(self):
        cfg=config();state=OrganizationTasksState(cfg);stock=copy.deepcopy(state.stocks)
        changed=dataclasses.replace(state.c,management_span=9)
        self.assertEqual(set(topology(list(range(30)),changed)['department_members']),{'0','1'})
        self.assertEqual(state.stocks,stock);self.assertEqual(state.c.department_count,2)

    def test_subsidiary_live_and_restore_namespace_preserve_own_dynamic_task_structure(self):
        cfg,state,q=self.ready();state.advance_structure(q['ready_day'],list(range(30)),operating=True);day_step(state,q['ready_day']);state.advance_structure(q['ready_day']+1,list(range(30)),operating=True);day_step(state,q['ready_day']+1)
        engine=types.SimpleNamespace(organization=state,people=[{'id':i} for i in range(30)],guild_alias={i:i for i in range(4)},site_count=2)
        live=references('target',engine)
        envelope={'state':{'people':engine.people,'guild_alias':engine.guild_alias,
            'enterprise_growth_state':{'sites':2},'organization_tasks_state':state.to_dict(),
            'config':cfg.to_dict()}}
        self.assertEqual(live,references_from_snapshot('target',envelope))
        self.assertEqual(len(live['departments']),3)
        self.assertTrue(all(v.startswith('target/') for v in live['departments'].values()))
        buyer=references('buyer',engine)
        self.assertNotEqual(buyer['departments']['0'],live['departments']['0'])

    def test_original_declined_and_failed_owned_controls_do_not_flatten_task_state(self):
        cfg,state,q=self.ready();state.advance_structure(q['ready_day'],list(range(30)),operating=True);day_step(state,q['ready_day'])
        baseline=copy.deepcopy(state.to_dict());terms=JointTerms(maximum_unfunded_workdays=1)
        target=types.SimpleNamespace(active=list(range(30)),cash=1.,l=cfg.longitudinal,organization=state)
        buyer=types.SimpleNamespace(cash=0.,clock=types.SimpleNamespace(is_workday=lambda d:True),organization=OrganizationTasksState(cfg))
        run=object.__new__(JointRun);run.units={'buyer':buyer};run._private={'target':target};run.terms=terms
        run.owners={'buyer':'buyer','target':'seller'};run.day=35;run.status='declined';run.unfunded_days=0;run.events=[];run.integration_paid_days=0
        self.assertEqual(run._controls(0.,0.),{'buyer':None,'target':None});self.assertEqual(state.to_dict(),baseline)
        run.owners['target']='buyer';run.status='integrating'
        run._controls(0.,0.);self.assertEqual(run.status,'failed-owned');self.assertEqual(state.to_dict(),baseline)
        self.assertEqual(run.owners['target'],'buyer')


if __name__=='__main__':unittest.main()
