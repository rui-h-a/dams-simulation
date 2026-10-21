"""Pure synthetic observable/provenance controls; no Model, data or provider.

Bindings are deliberately synthetic. Their acceptance never establishes that
raw evidence was verified, that parameters were calibrated or that a company
held out of design has been predicted. These controls test the adapter only.
"""
from dataclasses import replace
from datetime import date, timedelta
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from research_tools.enterprise_validation import (
    V2_MODEL, R7_MODEL, R8_MODEL, digest, EntityPredictionBinding,
    CompletedPredictionBinding, checked_prediction, extract_supported_observables,
    freeze_protocol, fit_calibration, freeze_validation, evaluate_validation,
    read_employee_target,
)
from research_tools.enterprise_observations import DataLabel

SOURCE='a'*64
GATE='b'*64
ENTITY_GATE='c'*64


def config(n=30, seed=7, world=100, days=1827):
    return {'n':n,'seed':seed,'world':world,'days':days,
            'longitudinal':{'calendar_start':'2020-01-01','holidays':[],
                            'world_context':'synthetic-adapter-control'}}


def summary(cfg, model=R7_MODEL, employees=35, revenue=100., cost=50.):
    return {'model_version':model,'n':cfg['n'],'world':cfg['world'],
            'days_completed':cfg['days'],'calendar_start':cfg['longitudinal']['calendar_start'],
            'calendar_end_exclusive':(date(2020,1,1)+timedelta(days=cfg['days'])).isoformat(),
            'active_members_final':employees,'closure_day':None,'suspension_day':None,
            'enterprise_operating_state_final':{'delivered_revenue':revenue},
            'operating_resource_units':cost,'cash_resource_units':200.,
            'payroll_resource_units':25.}


def entity(company, cfg, value):
    return EntityPredictionBinding(company,value['model_version'],SOURCE,digest(cfg),digest(value),
        ENTITY_GATE,cfg['seed'],cfg['world'],cfg['days'],value['days_completed'])


def binding(cfg, value, model=R7_MODEL, scope='single-enterprise'):
    return CompletedPredictionBinding(model,scope,SOURCE,digest(cfg),digest(value),GATE,
        cfg['seed'],cfg['days'],(entity('single',cfg,value),))


def joint(mode='integrated', scope='buyer-corporate-group', closed_target=False):
    buyer=config();target=config(seed=9,world=101)
    buyer_summary=summary(buyer,R8_MODEL,employees=40)
    target_summary=summary(target,R8_MODEL if mode!='declined' else R7_MODEL,employees=20,revenue=200.,cost=60.)
    if closed_target:target_summary.update(active_members_final=0,closure_day=10)
    for s,treasury,consideration,provider in ((buyer_summary,-1000.,10.,13.),(target_summary,1000.,0.,0.)):
        s.update(joint_treasury_net_resource_units=treasury,
                 joint_consideration_resource_units=consideration,joint_provider_resource_units=provider)
    if mode=='declined':
        for s in (buyer_summary,target_summary):
            s.update(joint_treasury_net_resource_units=0.,joint_consideration_resource_units=0.,joint_provider_resource_units=0.)
    cfg={'buyer':buyer,'target':target,'terms':{'global_world':91000,'offer_day':30}}
    value={'status':'ACTUAL_TWO_EVOLVED_FIRM_HISTORY_ENGINEERING_COMPLETED',
           'initial_two_firm_global_world':91000,'day':1827,'source_sha256':SOURCE,
           'owners':{'buyer':'buyer','target':'seller' if mode=='declined' else 'buyer'},
           'integration_status':mode,'offer':{'day':30,'source_sha256':SOURCE,
               'target_config_sha256':digest(target),'target_seed':9,'target_world':101},
           'firm_summaries':{'buyer':buyer_summary,'target':target_summary},
           'seller_account':0. if mode=='declined' else 10.,'provider_account':0. if mode=='declined' else 13.}
    bound=CompletedPredictionBinding(R8_MODEL,scope,SOURCE,digest(cfg),digest(value),GATE,7,1827,
             (entity('buyer',buyer,buyer_summary),entity('target',target,target_summary)))
    return cfg,value,bound


def prediction(value,cfg=None,bound=None,**changes):
    args={'parameters':{'demand':1.},'world_seed':7,'key':'synthetic-control',
          'gate_receipt_sha256':GATE,'model_source_sha256':SOURCE,
          'config_sha256':digest(cfg) if cfg is not None else 'd'*64}
    if bound is not None:args.update(config=cfg,binding=bound)
    args.update(changes)
    return checked_prediction(value,**args)


class PredictionAdapterTests(unittest.TestCase):
    def test_legacy_v2_packet_abi_and_amounts_are_unchanged(self):
        old={'model_version':V2_MODEL,'days_completed':1917,'active_members_final':30,
             'enterprise_operating_state_final':{'delivered_revenue':100.},
             'operating_resource_units':50.,'payroll_resource_units':25.}
        row=prediction(old)
        self.assertEqual(row['values'],{'reported_full_time_and_part_time_employees':30,
            'operating_margin_model_units':.5,'settled_revenue_model_resource_units':100.})
        self.assertEqual(set(row),{'parameters','world_seed','key','gate_receipt_sha256',
            'model_source_sha256','config_sha256','complete_days','values'})

    def test_complete_r7_calendar_units_and_explicit_nonclaims(self):
        cfg=config();value=summary(cfg);bound=binding(cfg,value)
        result=extract_supported_observables(value,cfg,bound)
        self.assertEqual(prediction(value,cfg,bound)['values']['reported_full_time_and_part_time_employees'],35)
        self.assertEqual(result.complete_days,1827)
        self.assertEqual(result.units['settled_revenue_model_resource_units'],'model_resource_units')
        self.assertFalse(result.full_raw_validation_performed)
        self.assertFalse(result.empirical_calibration_completed)
        self.assertFalse(result.historical_blindness_certified)
        self.assertFalse(result.cross_enterprise_holdout_completed)

    def test_unknown_version_and_pin_only_new_model_cannot_pass(self):
        cfg=config();value=summary(cfg)
        with self.assertRaises(ValueError):prediction(value)
        for unknown in ('other','longitudinal-enterprise-joint-integration-999'):
            bad=summary(cfg,unknown);bound=binding(cfg,bad,unknown)
            with self.subTest(unknown=unknown),self.assertRaises(ValueError):prediction(bad,cfg,bound)
        with self.assertRaises(ValueError):extract_supported_observables(value,cfg,{'status':'PASS','sha256':GATE})

    def test_caller_source_config_gate_and_seed_changes_fail(self):
        cfg=config();value=summary(cfg);bound=binding(cfg,value)
        for change in ({'model_source_sha256':'e'*64},{'config_sha256':'e'*64},
                       {'gate_receipt_sha256':'e'*64},{'world_seed':9},{'world_seed':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):prediction(value,cfg,bound,**change)
        for change in ({'model_source_sha256':'e'*64},{'config_sha256':'e'*64},
                       {'summary_sha256':'e'*64},{'seed':9},{'world':101}):
            bad=replace(bound,entities=(replace(bound.entities[0],**change),))
            with self.subTest(entity_change=change),self.assertRaises(ValueError):prediction(value,cfg,bad)

    def test_partial_calendar_or_summary_config_identity_cannot_be_resealed_to_complete(self):
        cfg=config()
        for field,bad_value in (('days_completed',1826),('n',31),('world',101),
                               ('seed',9),('calendar_start','2021-01-01'),
                               ('calendar_end_exclusive','2024-12-31')):
            value=summary(cfg);value[field]=bad_value;bound=binding(cfg,value)
            with self.subTest(field=field),self.assertRaises(ValueError):prediction(value,cfg,bound)
        value=summary(cfg);bound=binding(cfg,value)
        with self.assertRaises(ValueError):prediction(value,cfg,replace(bound,expected_days=1826))

    def test_boolean_calendar_and_finite_input_overflow_fail_closed(self):
        cfg=config(days=1);value=summary(cfg);cfg['days']=True
        bound=CompletedPredictionBinding(R7_MODEL,'single-enterprise',SOURCE,digest(cfg),digest(value),GATE,7,1,
            (replace(entity('single',cfg,value),expected_days=1,completed_days=1),))
        with self.assertRaises(ValueError):extract_supported_observables(value,cfg,bound)
        for changes in ({'revenue':1e-308,'cost':1e308}, {'revenue':1.,'cost':1e308}):
            cfg=config();value=summary(cfg,**changes)
            if changes['revenue']==1.:value['recruitment_resource_units']=1e308
            with self.subTest(changes=changes),self.assertRaises(ValueError):extract_supported_observables(value,cfg,binding(cfg,value))

    def test_joint_corporate_stock_counts_both_physical_units_once(self):
        cfg,value,bound=joint()
        extracted=extract_supported_observables(value,cfg,bound)
        self.assertEqual(extracted.values,{'reported_full_time_and_part_time_employees':60,
                                          'corporate_cash_model_resource_units':400.})
        self.assertNotIn('settled_revenue_model_resource_units',extracted.values)
        self.assertNotIn('operating_margin_model_units',extracted.values)
        self.assertFalse(extracted.full_raw_validation_performed)

    def test_two_firm_system_flows_do_not_double_count_internal_cash_or_external_accounts(self):
        cfg,value,bound=joint(scope='two-firm-system')
        extracted=extract_supported_observables(value,cfg,bound)
        self.assertEqual(extracted.values['two_firm_system_settled_revenue_model_resource_units'],300.)
        self.assertAlmostEqual(extracted.values['two_firm_system_operating_margin_model_units'],190/300)
        self.assertEqual(extracted.values['joint_transaction_and_integration_model_resource_units'],13.)
        self.assertEqual(extracted.values['joint_consideration_model_resource_units'],10.)
        self.assertEqual(extracted.values['two_firm_system_active_members'],60)
        self.assertEqual(extracted.values['two_firm_system_cash_model_resource_units'],400.)
        self.assertNotIn('reported_full_time_and_part_time_employees',extracted.values)

    def test_declined_target_is_not_buyer_employee_but_remains_two_company_system(self):
        cfg,value,bound=joint('declined')
        self.assertEqual(extract_supported_observables(value,cfg,bound).values['reported_full_time_and_part_time_employees'],40)
        system=replace(bound,observation_scope='two-firm-system')
        self.assertEqual(extract_supported_observables(value,cfg,system).values['two_firm_system_active_members'],60)
        for mode in ('integrating','failed-owned'):
            cfg,value,bound=joint(mode)
            self.assertEqual(extract_supported_observables(value,cfg,bound).values['reported_full_time_and_part_time_employees'],60)

    def test_closed_target_zero_and_zero_revenue_do_not_create_fake_employees_or_margin(self):
        cfg,value,bound=joint(closed_target=True)
        self.assertEqual(extract_supported_observables(value,cfg,bound).values['reported_full_time_and_part_time_employees'],40)
        cfg=config();value=summary(cfg,employees=0,revenue=0.,cost=10.);value['closure_day']=10
        result=extract_supported_observables(value,cfg,binding(cfg,value))
        self.assertIsNone(result.values['operating_margin_model_units'])
        value['active_members_final']=1
        with self.assertRaises(ValueError):extract_supported_observables(value,cfg,binding(cfg,value))

    def test_joint_namespace_duplicate_seed_offer_and_owner_tampering_refused(self):
        cfg,value,bound=joint()
        for bad in (replace(bound,entities=(bound.entities[0],bound.entities[0])),
                    replace(bound,entities=(bound.entities[0],replace(bound.entities[1],seed=7))),
                    replace(bound,observation_scope='implicitly-consolidated')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):extract_supported_observables(value,cfg,bad)
        for field,bad_value in (('firm_summaries',{**value['firm_summaries'],'seller':value['firm_summaries']['target']}),
                               ('owners',{'buyer':'buyer','target':'seller'}),('offer',None),
                               ('integration_status','unoffered'),('day',29),('source_sha256','e'*64)):
            bad=copy.deepcopy(value);bad[field]=bad_value
            resealed=replace(bound,summary_sha256=digest(bad))
            with self.subTest(field=field),self.assertRaises(ValueError):extract_supported_observables(bad,cfg,resealed)
        target_bad=replace(bound.entities[1],model_version=V2_MODEL)
        with self.assertRaises(ValueError):extract_supported_observables(value,cfg,replace(bound,entities=(bound.entities[0],target_bad)))

    def test_joint_cost_and_treasury_account_mismatch_or_nonfinite_amounts_rejected(self):
        for field,bad_value in (('provider_account',26.),('seller_account',20.)):
            cfg,value,bound=joint();value[field]=bad_value;bound=replace(bound,summary_sha256=digest(value))
            with self.subTest(field=field),self.assertRaises(ValueError):extract_supported_observables(value,cfg,bound)
        for field,bad_value in (('joint_treasury_net_resource_units',0.),('joint_provider_resource_units',float('nan'))):
            cfg,value,bound=joint();value['firm_summaries']['buyer'][field]=bad_value
            bound=replace(bound,summary_sha256=digest(value),entities=(entity('buyer',cfg['buyer'],value['firm_summaries']['buyer']),bound.entities[1]))
            with self.subTest(field=field),self.assertRaises(ValueError):extract_supported_observables(value,cfg,bound)

    def test_existing_protocol_fit_validation_chain_accepts_new_adapter_packets_as_synthetic_only(self):
        # A synthetic parameter-recovery control, not a new empirical protocol.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);labels=[DataLabel('c','toy','toy',('X',),'2024-12-31','calibration','already_exposed'),
                                  DataLabel('v','toy','toy',('X',),'2025-12-31','validation','not_accessed_in_this_component')]
            units={'reported_full_time_and_part_time_employees':'persons',
                   'operating_margin_model_units':'fraction','settled_revenue_model_resource_units':'model_resource_units'}
            protocol=freeze_protocol(root/'protocol.json',labels=labels,
                metrics={k:{'unit':u,'scale':1.,'weight':1.} for k,u in units.items()},
                parameter_grid={'demand':[1.,2.],'cost':[1.,2.]},world_seeds=[7,9],model_source_sha256=SOURCE)
            def packets(points,key):
                rows=[]
                for p in points:
                    for seed in [7,9]:
                        cfg=config(seed=seed);value=summary(cfg,employees=int(p['demand']),revenue=100.*p['demand'],cost=10.*p['cost'])
                        rows.append(prediction(value,cfg,binding(cfg,value),parameters=p,world_seed=seed,key=key))
                return rows
            def target(key,purpose):
                return {'key':key,'purpose':purpose,'protocol_sha256':protocol['sha256'],'source_sha256':GATE,
                    'evidence_class':'synthetic_control','values':{'reported_full_time_and_part_time_employees':2,
                        'operating_margin_model_units':.95,'settled_revenue_model_resource_units':200.}}
            fit=fit_calibration(root/'fit.json',protocol,packets(protocol['parameter_points'],'c'),[target('c','fit')])
            self.assertEqual(fit['selected_parameters'],{'demand':2.,'cost':1.})
            frozen=freeze_validation(root/'validation.json',protocol,fit,packets([fit['selected_parameters']],'v'))
            result=evaluate_validation(protocol,fit,frozen,[target('v','evaluate_frozen')])
            self.assertEqual(result['weighted_standardized_squared_error'],0.)
            self.assertFalse(result['historical_blindness_certified'])
            self.assertFalse(result['cross_enterprise_holdout_completed'])

    def test_holdout_candidate_remains_blocked_before_read_and_amazon_parser_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=freeze_protocol(Path(tmp)/'protocol.json',labels=[
                DataLabel('c','Amazon','retail',('US',),'2020-12-31','calibration','already_exposed'),
                DataLabel('h','other','other',('Y',),'2024-12-31','holdout_candidate','not_accessed_in_this_component')],
                metrics={'reported_full_time_and_part_time_employees':{'unit':'persons','scale':1.,'weight':1.}},
                parameter_grid={'demand':[1.,2.]},world_seeds=[7],model_source_sha256=SOURCE)
            with patch.object(Path,'read_bytes',side_effect=AssertionError('must not open target')):
                with self.assertRaises(ValueError):read_employee_target('not-opened',p,'h','evaluate_locked','untrusted')


if __name__=='__main__':unittest.main()
