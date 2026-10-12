"""Retained R10 metadata controls: no Model, SQL, new sample or calibration.

Read the existing four full-period engineering summaries. Negative controls
copy metadata into private temporaries and never edit native original raw.
"""
from dataclasses import asdict, replace
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from research_tools import enterprise_validation as v

EVIDENCE_PACKET=os.environ.get('DAMS_R10_ENGINEERING_EVIDENCE_PACKET')

def ref(path):
    return v.ObservableEvidenceFile(str(path),hashlib.sha256(path.read_bytes()).hexdigest())

def original_evidence():
    # Actual retained receipt locations are supplied privately at test invocation;
    # no developer path, private profile or machine custody layout is published.
    packet=json.loads(Path(EVIDENCE_PACKET).read_bytes())
    return tuple(v.R10EngineeringEvidence(**{k:(v.ObservableEvidenceFile(**value) if k!='arm' else value)
        for k,value in item.items()}) for item in packet['evidence'])

@unittest.skipUnless(EVIDENCE_PACKET,'retained engineering evidence packet must be supplied explicitly')
class ExistingR10ObservableTests(unittest.TestCase):
    def setUp(self):
        self.evidence=original_evidence()
        self.temp=tempfile.TemporaryDirectory(prefix='r10-metadata-negative-')
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def changed_ref(self, evidence, name, mutate):
        original=getattr(evidence,name);value=json.loads(Path(original.path).read_bytes())
        mutate(value);path=self.root/(name+'.json');path.write_bytes(v.canonical(value)+b'\n')
        return replace(evidence,**{name:ref(path)})
    def entity(self,company='buyer',evidence=None):
        item=evidence or self.evidence[0]
        scenario=json.loads(Path(item.scenario.path).read_bytes())
        summary=json.loads(Path(item.joint_result.path).read_bytes())['firm_summaries'][company]
        config=scenario[company+'_config'];source=scenario['source_sha256'];horizon=config['days']
        bound=v.EntityPredictionBinding(company,v.R10_MODEL,source,v.digest(config),v.digest(summary),
            item.narrow_review_receipt.sha256,config['seed'],config['world'],horizon,summary['days_completed'],item)
        return summary,config,bound,source,horizon,company
    def test_existing_four_r10_summaries_project_without_relabeling_or_empirical_upgrade(self):
        before={r.path:r.sha256 for e in self.evidence for r in (e.profile,e.scenario,e.joint_result,
            e.execution_receipt,e.supervisor_terminal,e.narrow_review_receipt,e.mechanism_review)}
        packet=v.project_r10_engineering_observables(self.evidence)
        self.assertIs(type(packet),v.EngineeringObservablePacket)
        self.assertEqual(packet.model_version,v.R10_MODEL);self.assertEqual(packet.outer_joint_model_version,v.R8_MODEL)
        self.assertEqual(packet.formal_worlds_added,0)
        for flag in ('full_raw_validation_performed','complete_independent_model_validation',
                     'empirical_calibration_completed','historical_blindness_certified','cross_enterprise_holdout_completed'):
            self.assertIs(getattr(packet,flag),False)
        self.assertEqual(len(packet.arms),2)
        for arm in packet.arms:
            self.assertEqual(set(arm['firms']),{'buyer','target'})
            for company,firm in arm['firms'].items():
                self.assertEqual(firm['model_version'],v.R10_MODEL)
                self.assertEqual(firm['complete_days'],1947)
                self.assertEqual(firm['initial_population'],2)
                self.assertEqual(firm['values']['reported_full_time_and_part_time_employees'],2)
                self.assertEqual(firm['units']['settled_revenue_model_resource_units'],'model_resource_units')
            self.assertEqual(arm['scopes']['buyer-corporate-group']['values']['reported_full_time_and_part_time_employees'],4)
        self.assertEqual(before,{path:ref(Path(path)).sha256 for path in before})
    def test_actual_dynamic_final3_task_departments_need_not_equal_initial2(self):
        args=self.entity();summary,config,*_=args
        self.assertEqual(config['organization_tasks']['department_count'],2)
        self.assertEqual(summary['task_department_count_final'],3)
        self.assertEqual(v._entity(*args)['reported_full_time_and_part_time_employees'],2)
    def test_engineering_claims_cannot_be_promoted_by_constructor_or_replace(self):
        packet=v.project_r10_engineering_observables(self.evidence)
        for field in ('formal_worlds_added','full_raw_validation_performed','complete_independent_model_validation',
                      'empirical_calibration_completed','historical_blindness_certified','cross_enterprise_holdout_completed'):
            # dataclasses.replace reports init=False as ValueError on older
            # supported Python and TypeError on Python 3.14; both refuse it.
            with self.subTest(field=field),self.assertRaises((ValueError,TypeError)):replace(packet,**{field:1})
    def test_nested_packet_mutation_is_refused_by_checked_export(self):
        packet=v.project_r10_engineering_observables(self.evidence)
        original=v.export_r10_engineering_packet(packet)
        self.assertEqual(original['formal_worlds_added'],0)
        packet.arms[0]['firms']['buyer']['values']['reported_full_time_and_part_time_employees']=10000
        with self.assertRaisesRegex(ValueError,'nested content changed'):packet.to_dict()
    def test_new_checksum_on_forged_packet_does_not_replace_original_projection(self):
        packet=v.project_r10_engineering_observables(self.evidence)
        arms=copy.deepcopy(packet.arms)
        arms[0]['firms']['buyer']['values']['reported_full_time_and_part_time_employees']=10000
        resealed=replace(packet,arms=arms)
        with self.assertRaisesRegex(ValueError,'original retained evidence projection'):resealed.to_dict()
    def test_r10_cannot_use_hex_only_receipt_binding(self):
        args=list(self.entity());args[2]=replace(args[2],engineering_evidence=None)
        with self.assertRaisesRegex(ValueError,'typed engineering evidence'):v._entity(*args)
    def test_changed_receipt_hex_pin_cannot_enter_r10_entity(self):
        args=list(self.entity());args[2]=replace(args[2],gate_receipt_sha256='f'*64)
        with self.assertRaisesRegex(ValueError,'receipt'):v._entity(*args)
    def test_r10_schema1_or_unknown_schema_refused(self):
        for schema in (1,3,True):
            with self.subTest(schema=schema):
                summary,config,*_=self.entity();config=copy.deepcopy(config)
                config['organization_tasks']['schema_version']=schema
                with self.assertRaises(ValueError):v._r10_state_shape(summary,config)
    def test_r10_task_state_schema_shape_and_final_department_consistency(self):
        cases=(lambda s:s['organization_tasks_state_final'].update(schema_version=1),
            lambda s:s['organization_tasks_state_final'].update(completed_days=1946),
            lambda s:s['organization_tasks_state_final'].update(config={}),
            lambda s:s['organization_tasks_state_final']['stocks'].update({'foreign':0}),
            lambda s:s.update(task_department_count_final=2),
            lambda s:s['organization_tasks_state_final'].pop('structure'))
        for mutate in cases:
            with self.subTest(mutate=mutate):
                summary,config,*_=self.entity();summary=copy.deepcopy(summary);mutate(summary)
                with self.assertRaises(ValueError):v._r10_state_shape(summary,config)
    def test_r10_cannot_be_resealed_as_r7_or_unknown_model(self):
        for model in (v.R7_MODEL,'longitudinal-enterprise-task-structure-999'):
            with self.subTest(model=model):
                args=list(self.entity());args[0]=copy.deepcopy(args[0]);args[0]['model_version']=model
                args[2]=replace(args[2],model_version=model,summary_sha256=v.digest(args[0]))
                with self.assertRaises(ValueError):v._entity(*args)
    def test_unknown_or_boolean_task_schema_cannot_be_resealed_as_r7(self):
        for schema in (3,True):
            with self.subTest(schema=schema):
                args=list(self.entity());args[0]=copy.deepcopy(args[0]);args[1]=copy.deepcopy(args[1])
                args[0]['model_version']=v.R7_MODEL;args[1]['organization_tasks']['schema_version']=schema
                args[2]=replace(args[2],model_version=v.R7_MODEL,summary_sha256=v.digest(args[0]),config_sha256=v.digest(args[1]))
                with self.assertRaisesRegex(ValueError,'unknown organization'):v._entity(*args)
    def test_partial_calendar_or_foreign_config_resealed_is_not_original_evidence(self):
        for field,value in (('days',1946),('seed',1),('world',1),('n',3)):
            with self.subTest(field=field):
                args=list(self.entity());args[1]=copy.deepcopy(args[1]);args[1][field]=value
                args[2]=replace(args[2],config_sha256=v.digest(args[1]))
                with self.assertRaises(ValueError):v._entity(*args)
    def test_foreign_profile_scenario_receipt_and_source_refused(self):
        controls=(('profile',lambda x:x['model_source'].update(runtime_core_33_sha256='f'*64)),
            ('scenario',lambda x:x.update(source_sha256='f'*64)),
            ('execution_receipt',lambda x:x.update(source_sha256='f'*64)),
            ('narrow_review_receipt',lambda x:x.update(source_SHA='f'*64)),
            ('mechanism_review',lambda x:x.update(source_core_sha256='f'*64)))
        for name,mutate in controls:
            with self.subTest(name=name):
                bad=self.changed_ref(self.evidence[0],name,mutate)
                with self.assertRaises(ValueError):v.project_r10_engineering_observables((bad,self.evidence[1]))
    def test_narrow_receipt_cannot_upgrade_full_gate_formal_or_empirical_status(self):
        for field,value in (('complete_independent_model_validation',True),('formal_worlds_added',1),
                             ('empirical_calibration_or_OOS',True),('formal_worlds_added',False)):
            with self.subTest(field=field,value=value):
                bad=self.changed_ref(self.evidence[0],'narrow_review_receipt',lambda x:x.update({field:value}))
                with self.assertRaises(ValueError):v.project_r10_engineering_observables((bad,self.evidence[1]))
    def test_original_native_restore_or_closed_owner_receipt_missing_refused(self):
        controls=(('execution_receipt',lambda x:x['arms'][0].update(native_full_history_restore_and_joint_accounting_pass=False)),
            ('supervisor_terminal',lambda x:x.update(owned_pgid_absent=False)),
            ('supervisor_terminal',lambda x:x.update(producer_reaped=False)))
        for name,mutate in controls:
            with self.subTest(name=name):
                bad=self.changed_ref(self.evidence[0],name,mutate)
                with self.assertRaises(ValueError):v.project_r10_engineering_observables((bad,self.evidence[1]))
    def test_receipt_raw_byte_pin_mismatch_refused_even_valid_current_json(self):
        bad=self.changed_ref(self.evidence[0],'mechanism_review',
            lambda x:next(p for p in x['full_original_artifact_pins'] if p['path']=='hierarchy/joint-result.json').update(sha256='f'*64))
        # Parent narrow receipt still pins original review, so rejection starts
        # there; re-sealing a caller descriptor cannot invent original authority.
        with self.assertRaises(ValueError):v.project_r10_engineering_observables((bad,self.evidence[1]))
    def test_duplicate_or_extra_operating_entity_and_arm_refused(self):
        for firms in ({'buyer':{},'target':{},'seller':{}},{'buyer':{}}):
            bad=self.changed_ref(self.evidence[0],'joint_result',lambda x:x.update(firm_summaries=firms))
            with self.assertRaises(ValueError):v.project_r10_engineering_observables((bad,self.evidence[1]))
        with self.assertRaises(ValueError):v.project_r10_engineering_observables((self.evidence[0],self.evidence[0]))
    def test_r10_engineering_cannot_enter_empirical_prediction_packet(self):
        item=self.evidence[0];values,config,source,horizon=v._r10_evidence(item);summary=values['joint_result']
        entities=tuple(self.entity(c)[2] for c in ('buyer','target'))
        bound=v.CompletedPredictionBinding(v.R8_MODEL,'two-firm-system',source,v.digest(config),v.digest(summary),
            item.narrow_review_receipt.sha256,config['buyer']['seed'],horizon,entities)
        with self.assertRaisesRegex(ValueError,'cannot enter calibration'):
            v.checked_prediction(summary,parameters={},world_seed=config['buyer']['seed'],key='engineering',
                gate_receipt_sha256=item.narrow_review_receipt.sha256,config_sha256=v.digest(config),
                model_source_sha256=source,config=config,binding=bound)
    def test_r10_outer_joint_owner_and_terms_cannot_be_resealed_around_original_firms(self):
        item=self.evidence[0];values,config,source,horizon=v._r10_evidence(item);summary=values['joint_result']
        entities=tuple(self.entity(c)[2] for c in ('buyer','target'))
        bound=v.CompletedPredictionBinding(v.R8_MODEL,'buyer-corporate-group',source,v.digest(config),
            v.digest(summary),item.narrow_review_receipt.sha256,config['buyer']['seed'],horizon,entities)
        bad_summary=copy.deepcopy(summary);bad_summary['owners']['target']='seller';bad_summary['integration_status']='declined'
        with self.assertRaisesRegex(ValueError,'outer joint result/terms'):
            v.extract_supported_observables(bad_summary,config,replace(bound,summary_sha256=v.digest(bad_summary)))
        for field,value in (('integration_mode','centralized-takeover'),('consideration',20.)):
            with self.subTest(field=field):
                bad_config=copy.deepcopy(config);bad_config['terms'][field]=value
                with self.assertRaisesRegex(ValueError,'outer joint result/terms'):
                    v.extract_supported_observables(summary,bad_config,replace(bound,config_sha256=v.digest(bad_config)))

if __name__ == '__main__':unittest.main(verbosity=2)
