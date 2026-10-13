"""R102 pure original-backend and projected raw-vote guard controls.

No Model init/restore/tick. Full-context projections only exercise bookkeeping
checks and never certify a Model snapshot or independent economic validation.
"""
import copy,json
import unittest
from dams_sim import governed_operating_requests as g
from tests.test_governed_request_binding_r101 import BindingControls
from tests.test_governed_operating_requests_r100 import ballot

class ReplayControls(BindingControls):
    # Parent controls are run separately; only this class's own tests form suite.
    def rejected(self):
        self.ledger.begin();self.reserve(0);self.book.propose(self.p,0);b=ballot(self.p,-1.);self.book.decide(self.p,b);self.book.settle(0,b);self.day_end(0);self.ledger.commit()
    def rejected_consumed(self):
        self.rejected();self.ledger.begin();r=self.book.consume(self.growth,day=1,cash=1000.,active=2,payroll_per_member=1.,operating=True,expansion_allowed=True)
        self.reserve(1);self.book.settle(1);self.day_end(1);self.ledger.commit();self.book.verify_committed_header(2,growth_config=self.growth.c);return r
    def reject_forged(self,edit):
        self.rejected_consumed();state=self.book.to_dict();edit(state['consumed']['value'])
        # Preserve the original actual input context; changing both raw receipt
        # and its cache is no longer sufficient to invent a backend transition.
        raw=state['consumed']['value'];self.ledger.db.execute("update journal set payload=? where kind='governed_consumed'",(json.dumps(raw),))
        self.ledger.db.execute("update journal set payload=? where kind='day_end' and day=1",(json.dumps({'governed_operating_request_state':state}),));self.ledger.commit()
        with self.assertRaises((g.normative.Refused,ValueError)):
            g.JournalRequestBook(self.ledger,state['scope'],state).verify_committed_header(2,growth_config=self.growth.c)
    def test_unfunded_noevent_cannot_fabricate_inventory(self):
        def edit(r):r['after_growth'].update(capacity=2000,target=2000,guilds=20,departments=20,sites=20)
        self.reject_forged(edit)
    def test_unfunded_cannot_fabricate_pending_hiring(self):
        self.reject_forged(lambda r:r['after_growth'].update(pending_hiring={'target':2000,'ready_day':0}))
    def test_unfunded_fake_completion_without_prior_pending_refused(self):
        def edit(r):
            fake={'capacity':2000,'guilds':20,'departments':20,'sites':20,'target':2,'requested_day':0,'ready_day':1}
            r['events']=[['infrastructure_complete',fake]];r['after_growth'].update(capacity=2000,guilds=20,departments=20,sites=20,pending_hiring={'target':2,'ready_day':15})
        self.reject_forged(edit)
    def test_rejected_noevents_exactly_preserves_original_stocks(self):
        r=self.rejected_consumed();self.assertEqual(r[0].to_dict(),self.growth.to_dict());self.assertEqual(r[2:4],(0.,[]));self.assertEqual(r[4]['status'],'rejected')
    def test_legitimate_expired_pending_completion_exactly_replayed(self):
        self.rejected();current=copy.deepcopy(self.growth)
        paid,_=current.begin_day(1,1000.,active=2,operating=True,payroll_per_member=1.,expansion_allowed=True)
        before=current.to_dict();day=current.pending_construction['ready_day'];expected=copy.deepcopy(current)
        bp,be=expected.begin_day(day,1000.-paid,active=2,operating=True,payroll_per_member=1.,expansion_allowed=False)
        self.ledger.begin();got=self.book.consume(current,day=day,cash=1000.-paid,active=2,payroll_per_member=1.,operating=True,expansion_allowed=True);self.ledger.commit()
        self.assertEqual((got[0].to_dict(),got[1],got[2],got[3]),(expected.to_dict(),1000.-paid-bp,bp,be));self.assertEqual(got[4]['status'],'expired')
        self.assertTrue(got[0].capacity>before['capacity']);self.assertIsNotNone(got[0].pending_hiring)
        g.validate_transition(got[4],self.p,self.book.state['outcomes']['value'],context=self.book.state['consumption_context'],growth_config=self.growth.c,checkpoint_day=day+1)
        fake=copy.deepcopy(got[4]);fake['after_growth']['pending_hiring']=None
        with self.assertRaises(g.normative.Refused):g.validate_transition(fake,self.p,self.book.state['outcomes']['value'],context=self.book.state['consumption_context'],growth_config=self.growth.c,checkpoint_day=day+1)
    def test_legitimate_unfunded_contraction_and_cancellation_not_erased(self):
        self.rejected();current=copy.deepcopy(self.growth);paid,_=current.begin_day(1,1000.,active=2,operating=True,payroll_per_member=1.,expansion_allowed=True)
        day=current.pending_construction['ready_day'];current.begin_day(day,1000.-paid,active=2,operating=True,payroll_per_member=1.,expansion_allowed=False)
        ready=current.pending_hiring['ready_day'];current.begin_day(ready,1000.-paid,active=2,operating=True,payroll_per_member=1.,expansion_allowed=False)
        current.cost_signal=200.;current.pending_hiring={'target':current.target,'ready_day':ready+10};before=current.to_dict();baseline=copy.deepcopy(current)
        bp,be=baseline.begin_day(ready+1,1000.-paid,active=current.target,operating=True,payroll_per_member=1.,expansion_allowed=False)
        self.ledger.begin();r=self.book.consume(current,day=ready+1,cash=1000.-paid,active=current.target,payroll_per_member=1.,operating=True,expansion_allowed=True);self.ledger.commit()
        self.assertEqual((r[0].to_dict(),r[2],r[3]),(baseline.to_dict(),bp,be));self.assertLess(r[0].target,before['target']);self.assertIsNone(r[0].pending_hiring)
    def test_actual_context_config_and_flags_cannot_be_replaced(self):
        self.rejected_consumed();r=self.book.state['consumed']['value'];out=self.book.state['outcomes']['value'];context=self.book.state['consumption_context']
        cfg=copy.deepcopy(context['config_body']);cfg['hiring_delay_days']+=1
        with self.assertRaises(g.normative.Refused):g.validate_transition(r,self.p,out,context=context,growth_config=cfg,checkpoint_day=2)
        changed=copy.deepcopy(context);changed['before_growth_sha256']='0'*64
        with self.assertRaises(g.normative.Refused):g.validate_transition(r,self.p,out,context=changed,growth_config=self.growth.c,checkpoint_day=2)
        changed=copy.deepcopy(context);changed['operating']=1
        with self.assertRaises(g.normative.Refused):g.validate_transition(r,self.p,out,context=changed,growth_config=self.growth.c,checkpoint_day=2)
    def test_missing_actual_context_record_is_refused(self):
        self.rejected_consumed();self.ledger.db.execute("delete from journal where kind='governed_consumption_context'");self.ledger.commit()
        with self.assertRaisesRegex(g.normative.Refused,'context'):self.book.verify_committed_header(2,growth_config=self.growth.c)
    def test_missing_and_failed_partial_outcomes_original_backend_refuse_funding(self):
        self.ledger.begin();self.reserve(0);self.book.propose(self.p,0)
        result=self.book.consume(self.growth,day=1,cash=1000.,active=2,payroll_per_member=1.,operating=True,expansion_allowed=True)
        self.assertEqual(result[4]['status'],'missing_outcome');self.assertEqual(result[0].to_dict(),self.growth.to_dict());self.ledger.rollback()
        # Fresh in-memory owner corresponds to the rolled-back durable prefix.
        self.book=g.JournalRequestBook(self.ledger,'world:7:firm:test');self.ledger.begin();self.reserve(0);self.book.propose(self.p,0)
        failed=g.normative.failed_outcome(self.p,reason='test_failure');self.book.norm.decide(failed,ballot=None)
        result=self.book.consume(self.growth,day=1,cash=1000.,active=2,payroll_per_member=1.,operating=True,expansion_allowed=True)
        self.assertEqual(result[4]['status'],'failed');self.assertEqual(result[2],0.);self.assertEqual(result[0].to_dict(),self.growth.to_dict());self.ledger.rollback()
    def test_proposal_exact_backend_quote_with_actual_configuration(self):
        g.validate_proposal_backend(self.p,self.growth.c)
        wrong=copy.deepcopy(self.p);wrong['request']['ready_day']+=1;wrong['request']['details']['ready_day']+=1
        wrong['request_key']=g.normative.digest({'scope':wrong['scope'],'guild':wrong['guild'],'effective_day':wrong['effective_day'],'config':wrong['config_sha256'],'request':wrong['request']})
        wrong['proposal_id']='r98-'+g.normative.digest({k:v for k,v in wrong.items() if k!='proposal_id'})
        with self.assertRaisesRegex(g.normative.Refused,'backend quote'):g.validate_proposal_backend(wrong,self.growth.c)
    def projected_vote_day(self):
        # This is a synthetic raw-fields projection, not a Model snapshot.
        self.ledger.begin()
        for i in (0,1):self.ledger.add_journal(0,'entry',f'projected:entry:{i}',{'person':i,'initial':True})
        self.ledger.add_journal(0,'enterprise_market_begin','projected:market:0',{'day':0,'cash_before':1000.,'received':0.,'borrowed':0.,'operating':True})
        self.reserve(0);self.book.propose(self.p,0);b=ballot(self.p)
        for v in b['votes']:self.ledger.add_journal(0,'operating_vote',f'projected:vote:{v["person"]}',{'proposal_id':self.p['proposal_id'],**v})
        self.book.decide(self.p,b);cost=self.book.settle(0,b)
        metrics={'operating_proposals':1.,'operating_ballots_accepted':1.,'operating_eligible_vote_rows':2.,'operating_participating_vote_rows':2.,'operating_effective_vote_mass':1.,'operating_governance_reserved_hours':cost['reserved_hours'],'operating_governance_used_hours':cost['used_hours'],'operating_governance_released_idle_hours':cost['released_idle_hours']}
        self.ledger.add_journal(0,'day_end','day:0',{'governed_operating_request_state':self.book.to_dict(),'enterprise_growth_state':self.growth.to_dict(),'cash':1000.,'metrics_cumulative':metrics});self.ledger.commit()
        self.book.verify_committed_header(1,growth_config=self.growth.c,full_model=True)
    def test_projected_actual_vote_rows_match_full_persisted_ballot(self):self.projected_vote_day()
    def test_projected_foreign_identity_weight_and_count_refused(self):
        self.projected_vote_day();raw=self.ledger.db.execute("select payload from journal where kind='operating_vote' limit 1").fetchone()[0]
        bad=json.loads(raw);bad['person']=999;bad['effective_weight']=1000.
        self.ledger.db.execute("update journal set payload=? where kind='operating_vote' and seq=(select min(seq) from journal where kind='operating_vote')",(json.dumps(bad),));self.ledger.commit()
        with self.assertRaisesRegex(g.normative.Refused,'operating_vote'):self.book.verify_committed_header(1,growth_config=self.growth.c,full_model=True)
    def test_projected_missing_vote_and_full_context_required(self):
        self.projected_vote_day();self.ledger.db.execute("delete from journal where kind='operating_vote'");self.ledger.commit()
        with self.assertRaisesRegex(g.normative.Refused,'operating_vote'):self.book.verify_committed_header(1,growth_config=self.growth.c,full_model=True)
        fresh=g.JournalRequestBook(self.ledger,self.book.state['scope'],self.book.to_dict())
        with self.assertRaisesRegex(g.normative.Refused,'configuration'):fresh.verify_committed_header(1,full_model=True)

# Avoid silently counting inherited R101 controls twice.
def load_tests(loader,tests,pattern):
    names=sorted(n for n in ReplayControls.__dict__ if n.startswith('test_'))
    return unittest.TestSuite(ReplayControls(n) for n in names)

if __name__=='__main__':unittest.main()
