"""D1/D2 actual whole-loop regression controls; zero formal/empirical worlds."""
import dataclasses as D
import json
import hashlib
import importlib
import types
import sys
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_enterprise_acquisition as original_controls
from test_enterprise_acquisition import configuration
from dams_sim.enterprise_acquisition import AcquisitionTarget,TargetEmployee,TargetEvidence
from dams_sim.model import Model
from dams_sim.config import Config
from dams_sim.storage import canonical
from research_tools.validate_acquisition_accounts import verify_acquisition_accounts
from research_tools.validate_longitudinal import iter_journal_rows


def serial_configuration():
    a=AcquisitionTarget('A','sellerA',1,30,tuple(TargetEmployee(f'p{i}',learning=.6) for i in range(4)),4,1,1,1,
        cash_resource_units=3.,financial_debt_resource_units=2.,consideration_resource_units=10.,
        pending_evidence=(TargetEvidence('pending','p0',7.,'separate_unit',.4),),
        evidence=(TargetEvidence('old','p1',9.,'legacy_unit',.2),),history=(('sales','RU',120.),))
    # The historical one-guild B also violates D1 at its later integration.
    # A two-guild B keeps all day-8 financial terms identical and lets a new
    # complete whole-loop case obey the unchanged installed-stock contract.
    b=D.replace(a,key='B',seller='sellerB',available_day=8,guilds=2,consideration_resource_units=10000.,pending_evidence=(),evidence=())
    p=configuration(targets=(a,b),maximum_consolidated_debt_resource_units=30000.,
        maximum_acquisition_financing_resource_units=20000.,evidence_review_resource_units_per_record=1000.)
    return D.replace(p,longitudinal=D.replace(p.longitudinal,initial_cash_per_member=200.)).validate()


class CorrectionTests(unittest.TestCase):
    setUp=original_controls.AcquisitionTests.setUp
    tearDown=original_controls.AcquisitionTests.tearDown
    model=original_controls.AcquisitionTests.model
    raw=original_controls.AcquisitionTests.raw

    def test_D1_extra_departments_refused_before_payment_without_clamping(self):
        p=configuration();target=D.replace(p.enterprise_acquisition.targets[0],departments=2)
        p=D.replace(p,enterprise_acquisition=D.replace(p.enterprise_acquisition,targets=(target,))).validate()
        m=self.model(p,'D1');m.run();s=m._long.acquisitions.targets[target.key]
        self.assertEqual(s['owner'],target.seller);self.assertEqual(s['consideration_paid'],0.)
        self.assertEqual(s['status'],'available');self.assertEqual(s['target_cash_remaining'],target.cash_resource_units)
        self.assertEqual(m.config.enterprise_acquisition.targets[0].departments,2)
        self.assertEqual((m._long.enterprise.guilds,m._long.enterprise.departments),(2,1))
        events=[json.loads(payload) for _,_,kind,_,payload in m.ledger.rows('journal') if kind=='acquisition_inventory_refused']
        self.assertTrue(events);self.assertTrue(all(v['reason']=='enterprise department bounds differ' for v in events))
        self.assertTrue(all(v['money_paid']==0. and v['ownership_changed'] is False for v in events))
        m._long.validate_semantics();self.raw(m,'D1')
        state=m.state();state['enterprise_growth_state']['departments']=3
        with self.assertRaisesRegex(ValueError,'enterprise department bounds differ'):
            Model.restore(state,storage_dir=self.root/'forged-D1')

    def test_D2_same_day_prior_review_precedes_second_loan_and_exact_resume(self):
        p=serial_configuration();m=self.model(p,'D2');m.run(8)
        cp=self.root/'before-day8.json';m.write_checkpoint(cp)
        opening_cash=m._long.cash;m.run();envelope,db=self.raw(m,'D2')
        receipt=verify_acquisition_accounts(p,envelope['state'],db)
        day8=[v for v in receipt['chronological_cash_events'] if v['day']==8]
        self.assertEqual([(v['event'],v['target']) for v in day8],
            [('acquisition_evidence_quarantined','A'),('acquisition_close','B'),('acquisition_integration_paid','B')])
        self.assertAlmostEqual(day8[0]['cash_before'],opening_cash)
        self.assertAlmostEqual(day8[0]['cash_after'],opening_cash-1000.)
        self.assertAlmostEqual(day8[1]['cash_before'],opening_cash-1000.)
        self.assertAlmostEqual(day8[1]['lender_advance'],9202.391626645274)
        self.assertAlmostEqual(day8[1]['cash_after'],18.6)
        self.assertAlmostEqual(day8[2]['cash_after'],17.4)
        resumed=Model.restore_checkpoint(cp,storage_dir=self.root/'D2-resumed',expected_config=p)
        self.models.append(resumed);resumed.run()
        self.assertEqual(m.state(),resumed.state());self.assertEqual(m.semantic_digest(),resumed.semantic_digest())

    def test_D2_independent_validator_refuses_stale_cash_loan_after_prior_review(self):
        p=serial_configuration();m=self.model(p,'D2-negative');m.run();envelope,db=self.raw(m,'D2-negative')
        connection=sqlite3.connect(db)
        try:rows=list(iter_journal_rows(connection))
        finally:connection.close()
        index=next(i for i,(day,kind,_,payload) in enumerate(rows) if day==8 and kind=='acquisition_close' and json.loads(payload)['target']=='B')
        day,kind,ident,payload=rows[index];value=json.loads(payload);value['financing']-=1000.
        rows[index]=(day,kind,ident,canonical(value).decode())
        with patch('research_tools.validate_acquisition_accounts.iter_journal_rows',return_value=(v for v in rows)):
            with self.assertRaisesRegex(ValueError,'chronological lender advance'):
                verify_acquisition_accounts(p,envelope['state'],db)
    def test_original_frozen_D2_evidence_reconciles_without_model_restore(self):
        fixture=Path(__file__).resolve().parent/'fixtures/ma-r4-D2-original'
        provenance=json.loads((fixture/'provenance.json').read_text())
        for name,v in provenance['files'].items():
            data=(fixture/name).read_bytes()
            self.assertEqual({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()},v)
        envelope=json.loads((fixture/'serial-final.json').read_text())
        # Read old schema through its exact preserved source; r6 never silently adopts it.
        alias=types.ModuleType('ma_r5_config_reference')
        alias.__path__=[str(Path(__file__).resolve().parent/'fixtures/ma-r5-reference/dams_sim')]
        sys.modules[alias.__name__]=alias
        OriginalConfig=importlib.import_module(alias.__name__+'.config').Config
        p=OriginalConfig.from_dict(envelope['state']['config'])
        with self.assertRaises(ValueError):Config.from_dict(envelope['state']['config'])
        self.assertEqual(envelope['source_sha256'],provenance['source_core_sha256'])
        self.assertEqual(envelope['state']['model_version'],'longitudinal-enterprise-ma-candidate-1')
        receipt=verify_acquisition_accounts(p,envelope['state'],fixture/'serial-final.sqlite')
        oracle=json.loads((fixture/'chronological-cash-oracle.json').read_text())
        financial=[v for v in receipt['chronological_cash_events'] if v['day']==oracle['day']]
        self.assertEqual([(v['event'],v['target']) for v in financial],[(v['event'],v['target']) for v in oracle['steps']])
        for actual,expected in zip(financial,oracle['steps']):
            self.assertAlmostEqual(actual['cash_before'],expected['cash_before'])
            self.assertAlmostEqual(actual['cash_after'],expected['cash_after'])
        self.assertAlmostEqual(financial[1]['lender_advance'],oracle['correct_lender_advance'])
        self.assertEqual(receipt['days'],11)


if __name__=='__main__':unittest.main()
