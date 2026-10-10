"""Synthetic recovery/access controls; these fixtures are not public observations."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from research_tools.enterprise_observations import DataLabel
from research_tools.enterprise_validation import (freeze_protocol,fit_calibration,freeze_validation,
    evaluate_validation,authorize_target,read_employee_target,checked_prediction,load)

A='a'*64;B='b'*64

def label(key,period,role,org='toy',industry='toy-industry',country='X'):
    return DataLabel(key,org,industry,(country,),period,role,'already_exposed' if role not in ('validation','holdout_candidate') else 'not_accessed_in_this_component')

class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.n=Path(self.temp.name)
        self.metrics={'employees':{'unit':'persons','scale':1.,'weight':1.},'margin':{'unit':'fraction','scale':1.,'weight':2.}}
        self.labels=[label('c','2020-12-31','calibration'),label('v','2021-12-31','validation'),label('d','2022-12-31','diagnostic')]
    def tearDown(self):self.temp.cleanup()
    def protocol(self,**kw):
        args={'labels':self.labels,'metrics':self.metrics,'parameter_grid':{'demand':[1.,2.],'cost':[1.,2.]},'world_seeds':[7,9], 'model_source_sha256':A};args.update(kw)
        return freeze_protocol(self.n/'protocol.json',**args)
    def predictions(self,p,role,points=None,degenerate=False):
        keys=[v['key'] for v in p['labels'] if v['role']==role];out=[]
        for point in (points or p['parameter_points']):
            for seed in p['world_seeds']:
                for key in keys:
                    values={'employees':point['demand'],'margin':point['cost'] if not degenerate else point['demand']}
                    out.append({'parameters':point,'world_seed':seed,'key':key,'config_sha256':B,'gate_receipt_sha256':B,'model_source_sha256':A,'complete_days':1917,'values':values})
        return out
    def target(self,p,key,purpose,values=None):
        return {'key':key,'purpose':purpose,'protocol_sha256':p['sha256'],'source_url':'synthetic-control-only','source_sha256':B,'evidence_class':'synthetic_control','values':values or {'employees':2.,'margin':1.}}
    def fit(self,p):return fit_calibration(self.n/'fit.json',p,self.predictions(p,'calibration'),[self.target(p,'c','fit')])
    def test_declared_split_rejects_temporal_entity_industry_country_leakage(self):
        for labels in (self.labels+[label('same','2020-12-31','validation')], [label('c','2022-12-31','calibration'),label('v','2021-12-31','validation')],self.labels+[label('h','2023-12-31','holdout_candidate',org='other',industry='other',country='X')]):
            with self.subTest(labels=labels),self.assertRaises(ValueError):self.protocol(labels=labels)
    def test_protocol_is_exclusive_and_metrics_seeds_are_fixed(self):
        p=self.protocol();self.assertEqual(load(self.n/'protocol.json','enterprise-calibration-protocol-v1'),p)
        with self.assertRaises(FileExistsError):self.protocol()
        p['world_seeds'].append(11)
        with self.assertRaises(ValueError):self.predictions(p,'calibration');authorize_target(p,'c','fit')
    def test_calibration_recovers_two_identifiable_parameters(self):
        p=self.protocol();fit=self.fit(p)
        self.assertEqual(fit['selected_parameters'],{'demand':2.,'cost':1.});self.assertEqual(fit['sensitivity_rank'],2)
        self.assertEqual(len(fit['all_grid_scores']),4)
    def test_missing_seed_duplicate_seed_or_source_drift_fails_fit(self):
        p=self.protocol();base=self.predictions(p,'calibration')
        for rows in (base[:-1],base+[base[0]], [{**r,'model_source_sha256':B} for r in base]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):fit_calibration(self.n/'fit.json',p,rows,[self.target(p,'c','fit')])
    def test_validation_target_cannot_invisibly_tune(self):
        p=self.protocol()
        with self.assertRaises(ValueError):fit_calibration(self.n/'fit.json',p,self.predictions(p,'calibration'),[self.target(p,'v','evaluate_frozen')])
    def test_parameter_nonidentifiability_refuses_arbitrary_choice(self):
        p=self.protocol()
        with self.assertRaises(ValueError):fit_calibration(self.n/'fit.json',p,self.predictions(p,'calibration',degenerate=True),[self.target(p,'c','fit')])
        receipt=load(self.n/'fit.json','enterprise-calibration-fit-rejection-v1');self.assertEqual(len(receipt['all_grid_scores']),4)
    def test_unique_grid_best_but_collinear_sensitivity_is_rejected(self):
        p=self.protocol();rows=self.predictions(p,'calibration')
        for row in rows:row['values']={'employees':sum(row['parameters'].values()),'margin':2*sum(row['parameters'].values())}
        with self.assertRaises(ValueError):fit_calibration(self.n/'fit.json',p,rows,[self.target(p,'c','fit',{'employees':4.,'margin':8.})])
        receipt=load(self.n/'fit.json','enterprise-calibration-fit-rejection-v1')
        self.assertEqual(receipt['status'],'not_locally_identifiable');self.assertEqual(receipt['sensitivity_rank'],1)
    def test_holdout_guard_runs_before_target_path_open(self):
        p=self.protocol(labels=self.labels+[label('h','2024-12-31','holdout_candidate',org='other',industry='other',country='Y')])
        with patch.object(Path,'read_bytes',side_effect=AssertionError('target accessed')):
            with self.assertRaises(ValueError):read_employee_target(self.n/'not-to-open',p,'h','evaluate_locked','untrusted')
    def test_validation_requires_frozen_predictions_before_target_open(self):
        p=self.protocol()
        with patch.object(Path,'read_bytes',side_effect=AssertionError('target accessed')):
            with self.assertRaises(ValueError):read_employee_target(self.n/'not-to-open',p,'v','evaluate_frozen','untrusted')
    def test_frozen_validation_reports_bad_prediction_without_refit(self):
        p=self.protocol();fit=self.fit(p);frozen=freeze_validation(self.n/'validation.json',p,fit,self.predictions(p,'validation',points=[fit['selected_parameters']]))
        result=evaluate_validation(p,fit,frozen,[self.target(p,'v','evaluate_frozen',{'employees':3.,'margin':1.})])
        self.assertEqual(result['weighted_standardized_squared_error'],1.)
        self.assertFalse(result['diagnostics'][0]['inside_fixed_world_range']);self.assertFalse(result['cross_enterprise_holdout_completed'])
        self.assertEqual(fit['selected_parameters'],{'demand':2.,'cost':1.})
    def test_prediction_edit_after_freeze_and_fit_edit_are_refused(self):
        p=self.protocol();fit=self.fit(p);frozen=freeze_validation(self.n/'validation.json',p,fit,self.predictions(p,'validation',points=[fit['selected_parameters']]))
        frozen['predictions'][0]['values']['employees']=999
        with self.assertRaises(ValueError):evaluate_validation(p,fit,frozen,[self.target(p,'v','evaluate_frozen')])
        fit['selected_parameters']['demand']=1.
        with self.assertRaises(ValueError):freeze_validation(self.n/'other.json',p,fit,self.predictions(p,'validation'))
    def test_diagnostic_report_parser_preserves_exposure_not_calibration(self):
        labels=[label('d','2022-12-31','diagnostic',org='Amazon')]
        p=self.protocol(labels=labels,metrics={'reported_full_time_and_part_time_employees':{'unit':'persons','scale':100.,'weight':1.}})
        raw=self.n/'report.html';raw.write_text('As of December 31, 2022, we employed approximately 1,000 full-time and part-time employees')
        row=read_employee_target(raw,p,'d','inspect','https://www.sec.gov/Archives/edgar/data/1018724/123/amzn-20221231.htm')
        self.assertEqual(row['declared_exposure'],'already_exposed');self.assertTrue(row['parser_evidence']['approximately_reported'])
        with self.assertRaises(ValueError):fit_calibration(self.n/'fit.json',p,[],[row])
    def test_normalization_uses_actual_summary_observables_and_declared_units(self):
        summary={'model_version':'longitudinal-enterprise-causal-2','days_completed':1917,'active_members_final':30,
            'enterprise_operating_state_final':{'delivered_revenue':100.},'operating_resource_units':50.,'payroll_resource_units':25.}
        row=checked_prediction(summary,parameters={'demand':1.},world_seed=7,key='c',gate_receipt_sha256=B,config_sha256=B,model_source_sha256=A)
        self.assertEqual(row['values']['operating_margin_model_units'],.5);self.assertEqual(row['values']['reported_full_time_and_part_time_employees'],30)
        summary['model_version']='other'
        with self.assertRaises(ValueError):checked_prediction(summary,parameters={},world_seed=7,key='c',gate_receipt_sha256=B,config_sha256=B,model_source_sha256=A)

if __name__=='__main__':unittest.main()
