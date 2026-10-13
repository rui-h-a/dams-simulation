"""R101 exact prior attacks + original journal binding. Pure SQL, no Model.

Uses only the existing R100 pinned growth business-state fixture; no world is
initialized or advanced. Counterexamples are rejection assertions, not grants.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dams_sim import governed_operating_requests as g,model
from dams_sim.longitudinal_model import LongitudinalEngine
from dams_sim.longitudinal_storage import ExactLedger
from tests.test_governed_operating_requests_r100 import growth,quoted,ballot

class BindingControls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.ledger=ExactLedger(self.root/'ledger');self.ledger.commit();self.book=g.JournalRequestBook(self.ledger,'world:7:firm:test')
        self.growth=growth();self.p=quoted(self.growth)
        self.patches=[]
        for cls,names in ((model.Model,('__init__','restore_checkpoint','step','run')),(LongitudinalEngine,('__init__','restore','step','_step'))):
            for name in names:
                if hasattr(cls,name):p=patch.object(cls,name,side_effect=AssertionError('Model/Engine prohibited'));p.start();self.patches.append(p)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.ledger.close();self.tmp.cleanup()
    def reserve(self,day):self.book.reserve(day,0,[{'person':i,'share':share,'reserved_hours':.05} for i,share in enumerate((.6,.4))])
    def day_end(self,day):self.ledger.add_journal(day,'day_end',f'day:{day}',{'governed_operating_request_state':self.book.to_dict()})
    def stored(self):
        self.ledger.begin();self.reserve(0);self.book.propose(self.p,0);b=ballot(self.p);self.book.decide(self.p,b);self.book.settle(0,b);self.day_end(0);self.ledger.commit();return b
    def consumed(self):
        self.stored();self.ledger.begin();r=self.book.consume(self.growth,day=1,cash=1000.,active=2,payroll_per_member=1.,operating=True,expansion_allowed=True)
        self.reserve(1);cost=self.book.settle(1);self.assertIsNone(cost['proposal_id']);self.assertEqual(cost['used_hours'],0.)
        self.day_end(1);self.ledger.commit();self.book.verify_committed_header(2);return r
    def tamper_header_and_day_end(self,edit):
        state=self.book.to_dict();edit(state)
        payload={'governed_operating_request_state':state};self.ledger.db.execute("UPDATE journal SET payload=? WHERE kind='day_end' AND day=1",(json.dumps(payload),));self.ledger.commit()
        with self.assertRaises((g.normative.Refused,ValueError,KeyError,TypeError)):
            g.JournalRequestBook(self.ledger,state['scope'],state).verify_committed_header(2)
    def test_accepted_persisted_votes_cannot_settle_zero_participation(self):
        self.ledger.begin();self.reserve(0);self.book.propose(self.p,0);self.book.decide(self.p,ballot(self.p));before=self.book.to_dict()
        with self.assertRaisesRegex(g.normative.Refused,'persisted full ballot'):self.book.settle(0,ballot(self.p,participates=False))
        self.assertEqual(before,self.book.to_dict());self.assertEqual(self.ledger.db.execute("select count(*) from journal where kind='governed_charter_settlement'").fetchone()[0],0);self.ledger.rollback()
    def test_accepted_persisted_votes_cannot_settle_missing_ballot(self):
        self.ledger.begin();self.reserve(0);self.book.propose(self.p,0);self.book.decide(self.p,ballot(self.p))
        with self.assertRaisesRegex(g.normative.Refused,'persisted full ballot'):self.book.settle(0)
        self.ledger.rollback()
    def test_consumed_cache_future_day_cannot_survive_committed_header(self):
        self.consumed()
        def edit(state):state['consumed']['value']['day']=999;state['last_consumed_day']=999
        self.tamper_header_and_day_end(edit)
    def test_consumed_foreign_schema_negative_paid_wrong_cash_rejected(self):
        self.consumed()
        def edit(state):state['consumed']['value'].update(schema='FOREIGN',cash_after=2000.,paid=-1000.)
        self.tamper_header_and_day_end(edit)
    def test_same_day_reserve_cannot_reset_settled_charter_or_repeat_charge(self):
        self.consumed();self.ledger.begin()
        with self.assertRaisesRegex(g.normative.Refused,'already reserved'):self.reserve(1)
        with self.assertRaisesRegex(g.normative.Refused,'rolled back'):self.reserve(0)
        self.assertEqual(self.ledger.db.execute("select count(*) from journal where kind='governed_charter_settlement' and day=1").fetchone()[0],1);self.ledger.rollback()
    def test_same_day_unsettled_reserve_repeated_or_foreign_roster_rejected(self):
        self.ledger.begin();self.reserve(0)
        with self.assertRaises(g.normative.Refused):self.reserve(0)
        with self.assertRaises(g.normative.Refused):self.book.reserve(0,0,[{'person':9,'share':1.,'reserved_hours':.05}])
        self.assertEqual(self.ledger.db.execute("select count(*) from journal where kind='governed_charter_reservation'").fetchone()[0],1);self.ledger.rollback()
    def test_well_typed_fake_cash_and_day_end_cannot_replace_original_transition(self):
        self.consumed()
        def edit(state):state['consumed']['value']['cash_before']+=500.;state['consumed']['value']['cash_after']+=500.
        self.tamper_header_and_day_end(edit)
    def test_last_consumed_floor_cannot_be_cleared_after_new_proposal_cache_rotation(self):
        self.consumed();self.tamper_header_and_day_end(lambda state:state.update(consumed=None,consumption_context=None,last_consumed_day=None))
    def test_forged_settlement_or_reservation_raw_rows_rejected(self):
        self.consumed();raw=self.ledger.db.execute("select payload from journal where kind='governed_charter_settlement' and day=0").fetchone()[0];bad=json.loads(raw);bad.update(used_hours=0.,released_idle_hours=.1,participating_person_ids=[])
        self.ledger.db.execute("update journal set payload=? where kind='governed_charter_settlement' and day=0",(json.dumps(bad),));self.ledger.commit()
        with self.assertRaisesRegex(g.normative.Refused,'raw settlement'):self.book.verify_committed_header(2)
    def test_transition_intrinsic_schema_status_source_growth_cash_and_flags(self):
        self.consumed();original=self.book.state['consumed']['value'];out=self.book.state['outcomes']['value']
        attacks=[{'schema':'FOREIGN'},{'status':'foreign'},{'scope':'other'},{'request_key':'0'*64},{'config_sha256':'0'*64},{'outcome_sha256':'0'*64},{'paid':-1.},{'cash_after':float('inf')},{'new_request_funded':1},{'Model_integration_complete':True}]
        for change in attacks:
            with self.subTest(change=change):
                bad=copy.deepcopy(original);bad.update(change)
                with self.assertRaises((g.normative.Refused,ValueError)):g.validate_transition(bad,self.p,out,context=self.book.state['consumption_context'],growth_config=self.growth.c,checkpoint_day=2)
        bad=copy.deepcopy(original);bad['after_growth']['pending_construction']['target']+=1
        with self.assertRaises(g.normative.Refused):g.validate_transition(bad,self.p,out,context=self.book.state['consumption_context'],growth_config=self.growth.c,checkpoint_day=2)
        bad=copy.deepcopy(original);bad['before_growth']['revenue_signal']=-1.
        with self.assertRaises(g.normative.Refused):g.validate_transition(bad,self.p,out,context=self.book.state['consumption_context'],growth_config=self.growth.c,checkpoint_day=2)
    def test_original_engine_governed_metrics_reconcile_with_raw_rows(self):
        result=self.consumed();paid=result[2]
        stats={'operating_proposals':1.,'operating_ballots_accepted':1.,'operating_eligible_vote_rows':2.,'operating_participating_vote_rows':2.,'operating_effective_vote_mass':1.,'operating_governance_reserved_hours':.1,'operating_governance_used_hours':.1,'operating_governance_released_idle_hours':0.}
        for day in (0,1):
            if day==1:stats.update(operating_requests_consumed=1.,operating_requests_funded=1.,governed_request_paid_resource_units=paid,operating_governance_reserved_hours=.2,operating_governance_released_idle_hours=.1)
            row=json.loads(self.ledger.db.execute("select payload from journal where kind='day_end' and day=?",(day,)).fetchone()[0]);row['metrics_cumulative']=copy.deepcopy(stats)
            self.ledger.db.execute("update journal set payload=? where kind='day_end' and day=?",(json.dumps(row),day))
        self.ledger.commit();self.book.verify_committed_header(2)
        row['metrics_cumulative']['governed_request_paid_resource_units']=0.
        self.ledger.db.execute("update journal set payload=? where kind='day_end' and day=1",(json.dumps(row),));self.ledger.commit()
        with self.assertRaisesRegex(g.normative.Refused,'governed metric'):self.book.verify_committed_header(2)
    def test_original_decoded_journal_API_includes_packed_entries(self):
        self.ledger.close();self.ledger=ExactLedger(self.root/'packed-ledger',journal_chunk_bytes=256);self.ledger.commit();self.book=g.JournalRequestBook(self.ledger,'world:7:firm:test')
        self.consumed();self.assertGreater(self.ledger.db.execute('select count(*) from journal_chunks').fetchone()[0],0)
        self.assertLess(self.ledger.db.execute('select count(*) from journal').fetchone()[0],len(list(self.ledger.rows('journal'))))
        restored=g.JournalRequestBook(self.ledger,'world:7:firm:test',self.book.to_dict());restored.verify_committed_header(2,growth_config=self.growth.c)
    def test_committed_gate_refuses_outer_open_transaction_and_pending_day(self):
        self.stored();self.ledger.begin()
        with self.assertRaisesRegex(g.normative.Refused,'committed'):self.book.verify_committed_header(1)
        self.ledger.rollback();self.book.verify_committed_header(1)
        with self.assertRaises(g.normative.Refused):self.book.verify_committed_header(2)

if __name__=='__main__':unittest.main()
