"""r7 causal/custody controls; zero formal or externally validated worlds."""
import copy
import dataclasses as D
import importlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.organization_tasks import OrganizationTasksConfig as OC, OrganizationTasksState as OS, topology, VERSION
from dams_sim.storage import canonical,atomic_csv
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.longitudinal_storage import TABLE_COLUMNS
from research_tools.validate_longitudinal import verify_daily_evidence,verify_summary_evidence,iter_journal_rows
from research_tools.validate_acquisition_accounts import verify_acquisition_accounts
from research_tools.validate_organization_tasks import verify_organization_accounts
from test_enterprise_acquisition import configuration


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parent.parent/'candidate-evidence/test-tmp';root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.root=Path(self.tmp.name);self.models=[]
    def tearDown(self):
        for m in self.models:m.ledger.close()
        self.tmp.cleanup()
    def config(self,days=14,organization=None,**kw):
        p=configuration(days=max(days,2))
        return D.replace(p,days=days,organization_tasks=organization or OC(),**kw).validate()
    def model(self,p,name,chunk=1024):
        m=Model(p,storage_dir=self.root/name,journal_chunk_bytes=chunk);self.models.append(m);return m
    def raw(self,m,name):
        m.write_final_state(self.root/(name+'.json'))
        env,db=verify_snapshot(self.root/(name+'.json'),expected_config=m.config)
        atomic_csv(self.root/(name+'.csv'),m.history)
        verify_daily_evidence(self.root/(name+'.csv'),m.config,env['state'],db)
        verify_summary_evidence(json.loads(canonical(m.summary())),m.config,env['state'],db)
        if m.config.enterprise_acquisition is not None:verify_acquisition_accounts(m.config,env['state'],db)
        receipt=verify_organization_accounts(m.config,env['state'],db)
        self.assertFalse(receipt['formal_worlds_added']);self.assertFalse(receipt['empirical_calibration_completed']);return env,db
    def events(self,m,kind):return [(row[1],row[3],json.loads(row[4])) for row in m.ledger.rows('journal') if row[2]==kind]

    def test_strict_optional_schema_units_and_no_demographic_effects(self):
        self.assertNotIn('organization_tasks',Config().to_dict())
        p=self.config();self.assertEqual(Config.from_dict(p.to_dict()),p)
        for bad in ({'schema_version':True},{'schema_version':2},{'management_span':True},
                    {'reporting_hours_per_layer':math.nan},{'verification_hours_per_claim':0},
                    {'department_objectives':((.8,.8,.8),(.3,.3,.4))}, {'nationality_skill_multiplier':2.},
                    {'department_count':3}, {'collaboration_contacts':17}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):OC.from_dict(bad)
        with self.assertRaises(ValueError):D.replace(Config(),organization_tasks=OC()).validate()
        with self.assertRaises(ValueError):D.replace(p,organization_tasks={}).validate()

    def test_default_disabled_daily_r6_joint_source_bit_equivalence(self):
        # Read-only original source; avoid pycache writes to protected baseline.
        self.assertTrue(sys.dont_write_bytecode)
        pkg=types.ModuleType('r7_disabled_actual_r6');pkg.__path__=['/Volumes/DAMS-Workspace/projects/dams-simulation-enterprise-integrated-r6-candidate/dams_sim'];sys.modules[pkg.__name__]=pkg
        RC=importlib.import_module(pkg.__name__+'.config').Config;RE=importlib.import_module(pkg.__name__+'.longitudinal_model').LongitudinalEngine
        from dams_sim.joint_population import JointPopulationConfig
        p=D.replace(configuration(days=16),joint_population=JointPopulationConfig(skill_tenure_latent_correlation=.3)).validate()
        r=RE(RC.from_dict(p.to_dict()),storage_dir=self.root/'r6',journal_chunk_bytes=1024);self.models.append(r)
        m=self.model(p,'disabled')._long
        for _ in range(p.days):
            r.step();m.step();a=copy.deepcopy(r.header());b=copy.deepcopy(m.header());a.pop('execution_source_sha256');b.pop('execution_source_sha256')
            self.assertEqual(canonical(a),canonical(b))
            self.assertEqual([D.asdict(v) for v in r.agents],[D.asdict(v) for v in m.agents])
            for table in TABLE_COLUMNS:self.assertEqual(list(r.ledger.rows(table)),list(m.ledger.rows(table)))
        self.assertEqual(r.summary(),m.summary())

    def test_three_roles_separate_nonself_and_span_orthogonal_to_guilds(self):
        c=OC();a=topology(range(12),c);b=topology(range(12),D.replace(c,management_span=1))
        self.assertNotEqual(a['reporting_depth'],b['reporting_depth'])
        self.assertEqual(a['collaboration_peers'],b['collaboration_peers'])
        for key,peers in a['verification_candidates'].items():
            i=int(key)
            self.assertNotIn(i,peers);self.assertNotIn(a['reporting_parent'][key],peers)
            self.assertTrue(all(v%c.department_count!=i%c.department_count for v in peers))
        p=self.config(guilds=1,enterprise_growth=None,enterprise_operations=None,enterprise_acquisition=None);self.assertEqual(p.organization_tasks.department_count,2)

    def test_previous_day_dependency_no_free_bootstrap_and_conservation(self):
        p=self.config(organization=D.replace(OC(),initial_dependency_units_per_department=0.,dependency_input_units_per_output_work_unit=.25),enterprise_acquisition=None)
        m=self.model(p,'dependency');m.run()
        records=[v for _,_,v in self.events(m,'organization_tasks')]
        self.assertEqual(records[0]['produced_by_department']['1'],0.)
        self.assertGreater(records[0]['produced_by_department']['0'],0.)
        self.assertTrue(any(v['produced_by_department']['1']>0 for v in records[1:]))
        for v in records:
            self.assertLessEqual(v['consumed_input_units']['0'],v['morning_stocks']['0']+1e-8)
        self.raw(m,'dependency-raw')

    def test_dependency_ablation_changes_real_output_not_population(self):
        c=D.replace(OC(),initial_dependency_units_per_department=0.)
        a=self.model(self.config(organization=c,enterprise_acquisition=None),'with-dep');b=self.model(self.config(organization=D.replace(c,dependency_input_units_per_output_work_unit=0.),enterprise_acquisition=None),'no-dep')
        a.run();b.run();self.assertNotEqual(a.metrics['produced_work_units'],b.metrics['produced_work_units'])
        self.assertEqual(len(a.people),len(b.people));self.raw(a,'dep-a');self.raw(b,'dep-b')

    def test_local_objective_tradeoff_changes_output_quality_learning(self):
        a=OC(department_objectives=((1.,0.,0.),(1.,0.,0.)))
        b=OC(department_objectives=((.2,.4,.4),(.2,.4,.4)))
        x=self.model(self.config(organization=a,enterprise_acquisition=None),'production-goal');y=self.model(self.config(organization=b,enterprise_acquisition=None),'mixed-goal')
        x.run();y.run();self.raw(x,'goals-x');self.raw(y,'goals-y')
        self.assertLess(y.metrics['produced_work_units'],x.metrics['produced_work_units'])
        self.assertGreater(y.metrics['organization_learning_hours'],x.metrics['organization_learning_hours'])
        self.assertNotEqual(sum(a.learning for a in y.agents),sum(a.learning for a in x.agents))
        # Separate one-day intervention holds production share fixed; more explicit learning is causal.
        q=self.model(self.config(days=1,organization=OC(department_objectives=((.2,.8,0.),(.2,.8,0.))),enterprise_acquisition=None),'quality-only')
        z=self.model(self.config(days=1,organization=OC(department_objectives=((.2,0.,.8),(.2,0.,.8))),enterprise_acquisition=None),'learning-only');q.run();z.run()
        self.assertGreater(sum(a.learning for a in z.agents),sum(a.learning for a in q.agents))
        self.assertGreater(y.metrics['organization_quality_hours'],0)
        self.assertNotEqual(x._long.organization.stocks,y._long.organization.stocks)

    def test_collaboration_contacts_causal_and_costed(self):
        a=OC(collaboration_contacts=0);b=OC(collaboration_contacts=2)
        x=self.model(self.config(organization=a,enterprise_acquisition=None),'no-links');y=self.model(self.config(organization=b,enterprise_acquisition=None),'links')
        x.run();y.run();self.assertNotEqual(x.metrics['produced_work_units'],y.metrics['produced_work_units'])
        self.assertGreater(y.metrics['organization_network_hours'],x.metrics['organization_network_hours'])
        self.raw(x,'links-x');self.raw(y,'links-y')

    def test_reporting_depth_changes_latency_time_and_production(self):
        a=OC(management_span=1);b=OC(management_span=8)
        x=self.model(self.config(organization=a,enterprise_acquisition=None),'deep');y=self.model(self.config(organization=b,enterprise_acquisition=None),'flat')
        x.run();y.run();self.assertNotEqual(x.metrics['produced_work_units'],y.metrics['produced_work_units'])
        self.assertGreater(x.metrics['organization_network_hours'],y.metrics['organization_network_hours'])
        self.raw(x,'report-x');self.raw(y,'report-y')

    def test_verification_capacity_causes_explicit_deferral_and_no_free_credit(self):
        p=self.config(organization=OC(verification_hours_per_member_workday=0.),enterprise_acquisition=None)
        m=self.model(p,'no-reviewer');m.run();self.assertEqual(m.metrics.get('review_hours',0.),0.)
        self.assertEqual(m.metrics.get('confirmed_records',0.),0.)
        self.assertGreater(m.metrics['organization_verification_deferred_records'],0.)
        self.assertEqual(m.metrics.get('organization_verification_hours',0.),0.)
        self.raw(m,'verification-deferred')

    def test_actual_verification_is_independent_and_bounded_by_paid_time(self):
        m=self.model(self.config(),'verify-paid');m.run();self.raw(m,'verification-paid')
        self.assertGreater(m.metrics['organization_verification_hours'],0.)
        self.assertLessEqual(m.metrics['organization_verification_hours'],m.metrics['organization_network_hours']+1e-8)
        for _,_,v in self.events(m,'review'):
            self.assertIsInstance(v['verification_reviewer'],int);self.assertEqual(v['verification_hours'],OC().verification_hours_per_claim)

    def test_complete_schema2_checkpoint_resume_and_ma_profiles(self):
        p=self.config(days=24);full=self.model(p,'full');split=self.model(p,'split');full.run();split.run(7)
        cp=self.root/'checkpoint.json';split.write_checkpoint(cp)
        resumed=Model.restore_checkpoint(cp,expected_config=p,storage_dir=self.root/'resumed');self.models.append(resumed);resumed.run()
        self.assertEqual(full.semantic_digest(),resumed.semantic_digest());self.assertEqual(full.state(),resumed.state())
        self.assertEqual(full.summary()['model_version'],VERSION);self.assertGreater(full.metrics['acquired_people'],0.)
        self.raw(full,'full-raw');self.raw(resumed,'resumed-raw')

    def test_weekend_leave_exit_hire_and_year_boundary_rebuilds_roster(self):
        p=self.config(days=20,enterprise_acquisition=None)
        p=D.replace(p,longitudinal=D.replace(p.longitudinal,calendar_start='2020-12-28',annual_leave_workdays=40.,exit_schedule=((2,0,'exit'),),annual_vacancy_fill_probability=1.)).validate()
        m=self.model(p,'roster');m.run();self.raw(m,'roster-raw');self.assertGreater(m.metrics['entrants'],0.)
        plans=self.events(m,'organization_plan');self.assertTrue(any(not v['present_people'] for _,_,v in plans))
        for _,_,v in plans:self.assertEqual(set(v['allocations']),{str(i) for i in v['present_people']})

    def test_closure_zero_funding_and_tail_do_not_create_dependency_output(self):
        p=self.config(days=12,enterprise_acquisition=None)
        p=D.replace(p,longitudinal=D.replace(p.longitudinal,closure_day=4)).validate()
        m=self.model(p,'closed');m.run();self.raw(m,'closed-raw')
        for day,_,v in self.events(m,'organization_tasks'):
            if day>=4:self.assertTrue(all(x==0 for x in v['produced_by_department'].values()))

    def test_none_to_enabled_and_enabled_to_none_fork_refused(self):
        disabled=self.model(configuration(days=12),'disabled-parent');disabled.run(2)
        with self.assertRaises(ValueError):disabled._long.fork(D.replace(disabled.config,organization_tasks=OC()),storage_dir=self.root/'bad-enable')
        enabled=self.model(self.config(),'enabled-parent');enabled.run(2)
        with self.assertRaises(ValueError):enabled._long.fork(D.replace(enabled.config,organization_tasks=None),storage_dir=self.root/'bad-disable')
        self.assertEqual(disabled.day,2);self.assertEqual(enabled.day,2)

    def test_state_inventory_topology_quota_and_version_forgeries_refused(self):
        m=self.model(self.config(),'state-forgery');m.run(6)
        before=m.semantic_digest()
        for field in ('stock','topology','quota','version','schema'):
            state=copy.deepcopy(m.state());o=state['organization_tasks_state']
            if field=='stock':o['stocks']['0']+=10.;o['generated']['0']+=10.
            elif field=='topology':o['last_plan']['topology']['reporting_parent']['2']=2
            elif field=='quota':o['review_remaining']['0']+=1
            elif field=='version':state['model_version']='longitudinal-enterprise-integrated-6-joint-1'
            else:o['schema_version']=True
            with self.subTest(field=field),self.assertRaises(ValueError):Model.restore(state,storage_dir=self.root/('bad-'+field))
        self.assertEqual(m.semantic_digest(),before)

    def test_coherent_dayend_state_stock_forgery_requires_full_history_rejection(self):
        m=self.model(self.config(days=6),'coherent',chunk=None);m.run();env,db=self.raw(m,'coherent-raw')
        forged=copy.deepcopy(env['state']);o=forged['organization_tasks_state'];o['generated']['0']+=1.;o['stocks']['0']+=1.
        changed=self.root/'forged.sqlite';connection=sqlite3.connect(db);dest=sqlite3.connect(changed);connection.backup(dest);connection.close()
        row=dest.execute("SELECT seq,payload FROM journal WHERE kind='day_end' ORDER BY seq DESC LIMIT 1").fetchone();payload=json.loads(row[1]);payload['organization_tasks_state']=o
        dest.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(payload).decode(),row[0]));dest.commit()
        with self.assertRaises(ValueError):verify_organization_accounts(m.config,forged,dest)
        dest.close()

    def test_raw_production_measurement_and_verifier_tampering_refused(self):
        m=self.model(self.config(days=6),'raw-tamper',chunk=None);m.run();env,db=self.raw(m,'raw-tamper-raw')
        for field in ('potential_before_dependencies','quality_hours','verification_reviewer'):
            changed=self.root/(field+'.sqlite');connection=sqlite3.connect(db);dest=sqlite3.connect(changed);connection.backup(dest);connection.close()
            kind='review' if field=='verification_reviewer' else 'work'
            seq,payload=dest.execute('SELECT seq,payload FROM journal WHERE kind=? LIMIT 1',(kind,)).fetchone();v=json.loads(payload)
            if kind=='review':v[field]=-1
            else:v['organization'][field]+=.1
            dest.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(v).decode(),seq));dest.commit()
            with self.subTest(field=field),self.assertRaises(ValueError):verify_organization_accounts(m.config,env['state'],dest)
            dest.close()

    def test_extreme_dependency_and_objective_endpoints_keep_exact_accounts(self):
        for index,c in enumerate((OC(dependency_input_units_per_output_work_unit=100.,initial_dependency_units_per_department=0.),OC(department_objectives=((0.,.5,.5),(0.,1.,0.))))):
            m=self.model(self.config(organization=c,enterprise_acquisition=None),'endpoint'+str(index));m.run();self.raw(m,'endpoint-raw'+str(index))
            self.assertLessEqual(m.metrics['max_individual_time_booked_hours'],1+1e-12)

    def test_control_and_dams_same_candidate_have_valid_accounts_without_winner_claim(self):
        for regime in ('hierarchy','sublinear'):
            m=self.model(self.config(days=10,regime=regime,enterprise_acquisition=None),regime);m.run();self.raw(m,regime+'-raw')
            self.assertEqual(m.day,10);self.assertEqual(m.summary()['model_version'],VERSION)

    def test_initial_day_zero_checkpoint_and_allowed_fork_are_exact(self):
        p=self.config(days=10,enterprise_acquisition=None)
        m=self.model(p,'initial');m._long.validate_semantics()
        cp=self.root/'initial-cp.json';m.write_checkpoint(cp)
        resumed=Model.restore_checkpoint(cp,expected_config=p,storage_dir=self.root/'initial-resume');self.models.append(resumed)
        self.assertEqual(m.semantic_digest(),resumed.semantic_digest())
        child=m._long.fork(D.replace(p,backend='witness'),storage_dir=self.root/'initial-fork');self.models.append(child)
        self.assertEqual(child.day,0);self.assertEqual(child.organization.to_dict(),m._long.organization.to_dict())

    def test_historical_site_change_and_fault_do_not_use_final_site(self):
        p=self.config(days=8,sites=2,enterprise_growth=None,enterprise_operations=None,enterprise_acquisition=None,
                      fault_start_day=0,fault_stop_day=1)
        p=D.replace(p,longitudinal=D.replace(p.longitudinal,guild_moves=((1,0,1),))).validate()
        m=self.model(p,'site-move');m.run();self.raw(m,'site-move-raw');self.assertEqual(m.agents[0].site,1)

    def test_learning_and_fatigue_forgery_rejected_from_full_raw(self):
        m=self.model(self.config(days=8),'dynamic-forged');m.run();env,db=self.raw(m,'dynamic-forged-raw')
        for field in ('learning','fatigue'):
            forged=copy.deepcopy(env['state']);forged['agents'][0][field]+=.01
            with self.subTest(field=field),self.assertRaises(ValueError):verify_organization_accounts(m.config,forged,db)

    def test_sparse_network_storage_is_linear_and_large_topology_is_measured(self):
        import time
        begin=time.perf_counter();links=topology(range(10000),OC());elapsed=time.perf_counter()-begin
        self.assertEqual(sum(len(x) for x in links['verification_candidates'].values()),20000)
        self.assertEqual(sum(len(x) for x in links['collaboration_peers'].values()),10000)
        self.assertLess(len(canonical(links)),5_000_000)
        self.assertTrue(math.isfinite(elapsed))

    def test_pure_state_phase_and_recovery_controls(self):
        outer=self.config();o=OS(outer);o.prepare(0,[0,1],[0,1],{0:.8,1:.8},{0:.2,1:.3},funding=1.);o.task_allocation({0:1.,1:1.});o.finish(0,{0:1.,1:1.});o.validate(1)
        with self.assertRaises(ValueError):o.finish(0,{0:1.,1:1.})
        with self.assertRaises(ValueError):o.prepare(1,[0,1],[99],{99:.8},{99:.2},funding=1.)
        for label in ('quota','negative','boolday','boolfunding','outside'):
            v=o.to_dict()
            if label=='quota':v['review_remaining']['0']=10**9
            elif label=='negative':v['last_plan']['allocations']['0']['available_hours']=-100.
            elif label=='boolday':v['last_plan']['day']=False
            elif label=='boolfunding':v['last_plan']['funding_fraction']=True
            else:v['last_plan']['present_people']=[99];v['review_remaining']={'99':1}
            with self.subTest(label=label),self.assertRaises(ValueError):OS.restore(outer,v,1)
        initial=OS(outer).to_dict();initial['stocks']['0']+=5.;initial['generated']['0']+=5.
        with self.assertRaises(ValueError):OS.restore(outer,initial,0)

    def test_acquired_empty_guild_preserves_transferred_knowledge(self):
        p=self.config(days=20);t=p.enterprise_acquisition.targets[0]
        target=D.replace(t,employees=t.employees[:1],installed_seats=1,guilds=2,evidence=(),pending_evidence=())
        p=D.replace(p,enterprise_acquisition=D.replace(p.enterprise_acquisition,targets=(target,))).validate()
        m=self.model(p,'empty-acquired-guild');m.run();self.raw(m,'empty-acquired-guild-raw')
        self.assertEqual(m.metrics['acquired_people'],1);self.assertEqual(m._long.guild_count,4)
        self.assertGreater(m._long.memory[3],0.)

    def test_bool_raw_integers_and_verifier_source_drift_refused(self):
        m=self.model(self.config(days=6),'bool-raw',chunk=None);m.run();env,db=self.raw(m,'bool-raw-original')
        changed=self.root/'bool.sqlite';connection=sqlite3.connect(db);dest=sqlite3.connect(changed);connection.backup(dest);connection.close()
        seq,payload=dest.execute("SELECT seq,payload FROM journal WHERE kind='organization_tasks' AND day=0").fetchone();v=json.loads(payload);v['day']=False
        dest.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(v).decode(),seq));dest.commit()
        with self.assertRaises(ValueError):verify_organization_accounts(m.config,env['state'],dest)
        dest.close()
        with patch('research_tools.validate_organization_tasks.file_digest',return_value='0'*64),self.assertRaises(ValueError):verify_organization_accounts(m.config,env['state'],db)

    def test_transition_demand_product_tamper_and_outer_schema_bool_refused(self):
        p=self.config(days=6,enterprise_growth=None,enterprise_operations=None,enterprise_acquisition=None)
        m=self.model(p,'context',chunk=None);m.run();env,db=self.raw(m,'context-raw')
        changed=self.root/'context.sqlite';connection=sqlite3.connect(db);dest=sqlite3.connect(changed);connection.backup(dest);connection.close()
        seq,payload=dest.execute("SELECT seq,payload FROM journal WHERE kind='work' LIMIT 1").fetchone();v=json.loads(payload)
        v['organization']['transition_fraction']/=2;v['organization']['demand_fraction']*=2
        dest.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(v).decode(),seq));dest.commit()
        with self.assertRaises(ValueError):verify_organization_accounts(m.config,env['state'],dest)
        dest.close();forged=copy.deepcopy(m.state());forged['state_schema_version']=True
        with self.assertRaises(ValueError):Model.restore(forged,storage_dir=self.root/'outer-bool')

    def test_freeride_forge_duplicate_and_censor_preserve_raw_and_time_accounts(self):
        for attack in ('freeride','forge','duplicate','censor'):
            p=self.config(days=10,enterprise_acquisition=None,attack=attack,attack_budget_hours_per_day=.01,attack_start_day=1,attack_stop_day=4)
            m=self.model(p,attack+'-attack');m.run();self.raw(m,attack+'-raw')
            self.assertLessEqual(m.metrics['max_individual_time_booked_hours'],1+1e-12)

    def test_daily_verify_implementation_does_not_share_transition_functions(self):
        m=self.model(self.config(days=6),'independent');m.run();env,db=self.raw(m,'independent-raw')
        with (patch('dams_sim.organization_tasks.topology',side_effect=AssertionError('shared topology')),
             patch.object(OS,'prepare',side_effect=AssertionError('shared prepare')),
             patch.object(OS,'task_allocation',side_effect=AssertionError('shared task')),
             patch.object(OS,'finish',side_effect=AssertionError('shared finish')),
             patch.object(OS,'reviewer',side_effect=AssertionError('shared reviewer'))):
            self.assertTrue(verify_organization_accounts(m.config,env['state'],db)['independent_transition_functions'])

if __name__=='__main__':unittest.main()
