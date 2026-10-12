"""Actual native two-firm engineering controls; no formal or calibration claim."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from dams_sim.config import Config
from dams_sim.joint_firm import JointRun, JointTerms, full_inventory, inventory_digest, references, verify_cash_records, validate_control
from dams_sim.storage import canonical, digest, atomic_csv
from dams_sim.longitudinal_model import verify_snapshot
from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence

ROOT=Path(__file__).resolve().parents[1]


def configs(days=20):
    v=json.loads((ROOT/'tests/fixtures/r7-organic-acquisition-checkpoint-integration.json').read_bytes())
    v['enterprise_acquisition']=None;v['days']=days;v['world']=81000;v['seed']=731
    v['attack_start_day']=0;v['attack_stop_day']=days
    v['longitudinal']['work_creation_stop_day']=None
    v['longitudinal']['adoption_day']=0
    v['enterprise_growth']['review_interval_days']=5
    v['enterprise_growth']['construction_delay_days']=3
    v['enterprise_growth']['hiring_delay_days']=3
    v['enterprise_operations']['capital_credit_limit_resource_units']=0.
    buyer=Config.from_dict(v)
    target=replace(buyer,world=81001,seed=732,regime='hierarchy',
        longitudinal=replace(buyer.longitudinal,adoption_mode='never'))
    return buyer,target


class NativeJointControls(unittest.TestCase):
    def setUp(self):
        base=Path(os.environ.get('DAMS_R8_TEST_TMP',str(Path(tempfile.gettempdir())/'dams-joint-r8-owned-tests'))).resolve()
        base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.temp.name);self.runs=[]
    def tearDown(self):
        for r in self.runs:r.close()
        failed=any(test is self for test,_ in self._outcome.result.errors+self._outcome.result.failures)
        if failed:
            keep=self.root.parent/('failure-'+self.id().rsplit('.',1)[-1]+'-'+self.root.name)
            self.root.rename(keep);self.temp.cleanup()
        else:self.temp.cleanup()
    def run_new(self,name='run',days=20,terms=None,target_change=None,buyer_change=None):
        b,t=configs(days)
        if target_change:t=target_change(t)
        if buyer_change:b=buyer_change(b)
        r=JointRun(b,t,self.root/name,terms or JointTerms(offer_day=8));self.runs.append(r);return r

    def test_preoffer_target_private_state_noninterference(self):
        a=self.run_new('a');b=self.run_new('b',target_change=lambda t:replace(t,seed=900,world=81002,
            longitudinal=replace(t.longitudinal,initial_cash_per_member=500.)))
        a.run(8);b.run(8)
        self.assertEqual(canonical(a.buyer_public_state()),canonical(b.buyer_public_state()))
        self.assertNotEqual(a._private['target'].semantic_digest(),b._private['target'].semantic_digest())
        self.assertIsNone(a.offer);self.assertFalse((a.root/'offer-target.json').exists())
        self.assertEqual(a.units['buyer'].metrics.get('joint_coordination_reserved_hours',0),0)

    def test_early_offer_and_replayed_offer_refused(self):
        r=self.run_new()
        with self.assertRaises(ValueError):r._expose_offer()
        r.run(8);r._expose_offer()
        with self.assertRaises((ValueError,KeyError)):r._expose_offer()

    def test_whole_native_inventory_transfers_with_namespace_without_flattening(self):
        r=self.run_new();r.run(8);m=r._private['target'];before=full_inventory(m)
        self.assertGreater(m.operations.backlog(),0)
        self.assertGreater(m.ledger.pending_count('review')+m.ledger.pending_count('commit'),0)
        r._expose_offer()
        self.assertIs(r.units['target'],m);self.assertNotIn('target',r._private)
        self.assertEqual(before,full_inventory(r.units['target']))
        self.assertEqual(r.offer['inventory_sha256'],inventory_digest(before))
        self.assertEqual(r.owners['target'],'buyer')
        self.assertNotEqual(references('buyer',r.units['buyer'])['people']['0'],references('target',m)['people']['0'])
        self.assertEqual(before['agents'],[vars(a) for a in m.agents])
        self.assertEqual(before['organization'],m.organization.to_dict())

    def test_actual_pending_deliveries_and_order_obligations_settle_once_after_transfer(self):
        r=self.run_new();r.run(8);m=r._private['target'];arrived=m.operations.orders_arrived
        output=m.operations.product_units;delivered=m.operations.delivered_units
        r.run(20);m=r.units['target'];self.assertEqual(m.day,20)
        self.assertGreater(m.operations.orders_arrived,arrived);self.assertGreater(m.operations.product_units,output)
        self.assertGreater(m.operations.delivered_units,delivered)
        self.assertAlmostEqual(m.operations.orders_arrived,m.operations.orders_expired+m.operations.product_units+m.operations.backlog())
        self.assertAlmostEqual(m.operations.product_units,m.operations.delivered_units+sum(s['units'] for s in m.operations.shipments))
        self.assertEqual(len(r.records),20)

    def test_pending_hiring_or_construction_is_retained_and_completes(self):
        r=self.run_new(days=30,terms=JointTerms(offer_day=6));r.run(6);m=r._private['target']
        pending=copy.deepcopy((m.enterprise.pending_construction,m.enterprise.pending_hiring))
        self.assertTrue(any(pending),'fixture must have actual already-paid infrastructure/hiring obligation')
        r.run(30);m=r.units['target']
        self.assertGreater(m.enterprise.capacity,30)
        self.assertTrue(any(e['kind']=='ownership-transfer' for e in r.events))

    def test_independent_cash_conservation_and_tampering(self):
        r=self.run_new();r.run();self.assertEqual(verify_cash_records(r.records)['days'],20)
        self.assertEqual(r.seller_account,10.)
        bad=copy.deepcopy(r.records);bad[-1]['firms']['target']['cash_after']+=1
        with self.assertRaises(ValueError):verify_cash_records(bad)
        bad=copy.deepcopy(r.records);bad[8]['firms']['target']['control']['cash_delta']+=1
        with self.assertRaises(ValueError):verify_cash_records(bad)

    def test_centralization_changes_governance_and_real_funding_and_output(self):
        a=self.run_new('autonomy');b=self.run_new('central',terms=JointTerms(offer_day=8,integration_mode='centralize',subsidiary_daily_cash_budget=.1))
        a.run();b.run()
        self.assertEqual(a.units['target'].policy(0),('hierarchy','central'))
        self.assertEqual(b.units['target'].policy(0),b.units['buyer'].policy(0))
        self.assertNotEqual(a.units['target'].metrics['produced_work_units'],b.units['target'].metrics['produced_work_units'])
        self.assertNotEqual(a.units['target'].cash,b.units['target'].cash)
        self.assertGreater(a.units['target'].metrics['joint_coordination_reserved_hours'],0)
        self.assertNotEqual(a.units['target'].metrics['joint_coordination_reserved_hours'],b.units['target'].metrics['joint_coordination_reserved_hours'])

    def test_nonworking_days_do_not_create_paid_integration_days(self):
        r=self.run_new(terms=JointTerms(offer_day=3));r.run(5)
        self.assertEqual(r.integration_paid_days,0);self.assertEqual(r.status,'integrating')

    def test_integration_failure_preserves_owned_assets_and_obligations(self):
        r=self.run_new(terms=JointTerms(offer_day=8,integration_cost_per_employee_workday=10000.,maximum_unfunded_workdays=1))
        r.run();self.assertEqual(r.status,'failed-owned');self.assertEqual(r.owners['target'],'buyer')
        self.assertEqual(r.units['target'].day,20);self.assertNotIn('target',r._private)
        self.assertTrue(any(e['kind']=='integration-failed' for e in r.events))
        verify_cash_records(r.records)

    def test_capacity_refusal_never_drops_people_or_clamps_assets(self):
        b,t=configs()
        with self.assertRaises(ValueError):JointRun(b,t,self.root/'cap',JointTerms(offer_day=8,max_active_people=59))
        r=self.run_new();r.run(8);before=full_inventory(r._private['target']);r.terms=replace(r.terms,max_installed_seats=1)
        with self.assertRaises(ValueError):r.step()
        self.assertEqual(r.owners['target'],'seller');self.assertEqual(before,full_inventory(r._private['target']))

    def test_calendars_and_independent_company_identity_are_required(self):
        b,t=configs()
        with self.assertRaises(ValueError):JointRun(b,b,self.root/'same',JointTerms(offer_day=8))
        with self.assertRaises(ValueError):JointRun(b,replace(t,longitudinal=replace(t.longitudinal,calendar_start='2021-01-01')),self.root/'calendar',JointTerms(offer_day=8))

    def test_native_before_and_after_acquisition_checkpoint_tails_equal(self):
        r=self.run_new();r.run(7);p=self.root/'before.json';r.snapshot(p)
        before=JointRun.restore(p,self.root/'restored-before');self.runs.append(before)
        r.run(14);q=self.root/'after.json';r.snapshot(q)
        after=JointRun.restore(q,self.root/'restored-after');self.runs.append(after)
        r.run();before.run();after.run()
        for x in (before,after):
            self.assertEqual(canonical(x.records),canonical(r.records));self.assertEqual(canonical(x.events),canonical(r.events))
            self.assertEqual(x.owners,r.owners)
            for company in ('buyer','target'):self.assertEqual(x.units[company].semantic_digest(),r.units[company].semantic_digest())

    def test_actual_full_entity_raw_validators_pass_after_joint_controls(self):
        r=self.run_new();r.run()
        for company,m in r.units.items():
            d=self.root/('validate-'+company);d.mkdir();p=d/'final_state.json';m.write_final_state(p)
            env,db=verify_snapshot(p,expected_config=m.config)
            atomic_csv(d/'timeseries.csv',m.history)
            verify_daily_evidence(d/'timeseries.csv',m.config,env['state'],db)
            verify_summary_evidence(json.loads(canonical(m.summary())),m.config,env['state'],db)

    def test_wrong_control_day_unknown_fields_and_unfunded_spend_refuse(self):
        r=self.run_new();r.run(8);r._expose_offer();c=r._controls(10.,1.)['buyer']
        for change in ({'day':7},{'extra':True},{'cash_delta':float('inf')},{'coordination_hours':-.1}):
            bad={**c,**change}
            with self.assertRaises(ValueError):validate_control(bad,8)
        r.units['buyer']._long.set_joint_control({**c,'consideration':1e12})
        with self.assertRaises(ValueError):r.units['buyer'].step()

    def test_engine_alias_is_refused_not_double_stepped(self):
        r=self.run_new();old=r._private['target'];r._private['target']=r.units['buyer']
        try:
            with self.assertRaises(ValueError):r.step()
            self.assertEqual(r.units['buyer'].day,0)
        finally:r._private['target']=old

    def test_checkpoint_metadata_cannot_grant_early_integration_or_mint_accounts(self):
        r=self.run_new();r.run(9);p=r.root/'original-checkpoint.json';r.snapshot(p)
        original=json.loads(p.read_bytes())
        for i,delta in enumerate(({'status':'integrated'},{'integration_paid_days':999},
                {'seller_account':999.},{'provider_account':0.},{'owners':{'buyer':'buyer','target':'seller'}})):
            bad={**original,**delta};q=r.root/('bad-'+str(i)+'.json');q.write_bytes(canonical(bad))
            with self.assertRaises(ValueError):JointRun.restore(q,self.root/('restore-bad-'+str(i)))
        bad=copy.deepcopy(original);bad['records'][8]['firms']['target']['active_before']+=1
        q=r.root/'bad-staff.json';q.write_bytes(canonical(bad))
        with self.assertRaises(ValueError):JointRun.restore(q,self.root/'restore-bad-staff')
        bad=copy.deepcopy(original);bad['offer']['target_world']+=1
        q=r.root/'bad-offer.json';q.write_bytes(canonical(bad))
        with self.assertRaises(ValueError):JointRun.restore(q,self.root/'restore-bad-offer')
        for i,field in enumerate(('references','inventory_sha256')):
            bad=copy.deepcopy(original)
            if field=='references':bad['offer'][field]['people']['0']='buyer/person/0'
            else:bad['offer'][field]='0'*64
            for event in bad['events']:
                if 'target' in event:event['target']=copy.deepcopy(bad['offer'])
            q=r.root/('bad-both-'+field+'.json');q.write_bytes(canonical(bad))
            with self.assertRaises(ValueError):JointRun.restore(q,self.root/('restore-both-'+field))

    def test_closed_target_eligible_roster_is_not_operating_active_count(self):
        r=self.run_new(target_change=lambda t:replace(t,longitudinal=replace(t.longitudinal,closure_day=0)))
        r.run(12);self.assertEqual(len(r.units['target'].active),30)
        self.assertEqual(r.units['target'].summary()['active_members_final'],0)
        p=r.root/'closed.json';r.snapshot(p)
        x=JointRun.restore(p,self.root/'restored-closed');self.runs.append(x)
        r.run();x.run()
        self.assertEqual(canonical(r.records),canonical(x.records))
        self.assertEqual(r.units['target'].semantic_digest(),x.units['target'].semantic_digest())

    def test_closed_buyer_declines_purchase_without_dropping_target_history(self):
        r=self.run_new(buyer_change=lambda b:replace(b,longitudinal=replace(b.longitudinal,closure_day=6)))
        r.run();self.assertEqual(r.status,'declined');self.assertEqual(r.owners['target'],'seller')
        self.assertEqual(r._private['target'].day,20);self.assertEqual(r.seller_account,0.)
        r.validate_joint_metadata()

    def test_new_inventory_provenance_digest_normalizes_int_and_string_maps(self):
        native={'slots':{i:i+100 for i in range(30)},'guilds':{i:.125*i for i in range(12)}}
        restored=json.loads(canonical(native))
        self.assertNotEqual(canonical(native),canonical(restored))
        self.assertEqual(inventory_digest(native),inventory_digest(restored))


if __name__=='__main__':unittest.main()
