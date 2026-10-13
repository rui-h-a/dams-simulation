"""R100 AST and real journal/SQLite controls. No Model init/restore/tick.

Business-state construction uses only the separately pinned original growth
helper API. This is engineering coverage, not daily Model runtime evidence.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from dams_sim.config import Config
from dams_sim.longitudinal_storage import ExactLedger
from dams_sim import governed_operating_requests as g
from dams_sim import model

ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT.parent

def growth():
    c=g.GROWTH_API.EnterpriseGrowthConfig(initial_organization_capacity=2,initial_departments=1,minimum_target_members=2,review_interval_days=1)
    obj=g.GROWTH_API.EnterpriseGrowthState(SimpleNamespace(enterprise_growth=c,guilds=1,sites=1,n=2,enterprise_operations=None))
    obj.observe(0,100.,10.,working=True);return obj

def quoted(obj):return g.quote_request(obj,scope='world:7:firm:test',guild=0,day=0,cash=1000.,active=2,payroll_per_member=1.,operating=True,expansion_allowed=True)
def ballot(p,signal=1.,participates=True):
    return {'schema':g.normative.BALLOT,'proposal_id':p['proposal_id'],'decision_day':0,'guild':0,'eligible_person_ids':[0,1],
            'votes':[{'person':i,'guild':0,'observed_signal':signal,'participates':participates,'formal_share':share,'effective_weight':share if participates else 0.,'vote':float(signal>=0)} for i,share in enumerate((.6,.4))]}

class Controls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.ledger=ExactLedger(Path(self.temp.name)/'ledger');self.ledger.commit()
        self.book=g.JournalRequestBook(self.ledger,'world:7:firm:test');self.original=growth();self.p=quoted(self.original)
        self.no_model=patch.object(model.Model,'__init__',side_effect=AssertionError('Model constructor prohibited'))
        self.no_model.start()
        self.no_tick=patch.object(model.Model,'step',side_effect=AssertionError('Model tick prohibited'));self.no_tick.start()
        self.no_restore=patch.object(model.Model,'restore_checkpoint',side_effect=AssertionError('Model restore prohibited'));self.no_restore.start()
    def tearDown(self):self.no_restore.stop();self.no_tick.stop();self.no_model.stop();self.ledger.close();self.temp.cleanup()
    def reserve(self,hours=.05):self.book.reserve(0,0,[{'person':i,'share':share,'reserved_hours':hours} for i,share in enumerate((.6,.4))])
    def stored(self,signal=1.,participates=True):
        self.ledger.begin();self.reserve();self.book.propose(self.p,0);b=ballot(self.p,signal,participates)
        out=self.book.decide(self.p,b);settlement=self.book.settle(0,b);self.ledger.commit();return out,settlement
    def consume(self,obj=None,**delta):
        args={'day':1,'cash':1000.,'active':2,'payroll_per_member':1.,'operating':True,'expansion_allowed':True};args.update(delta)
        return self.book.consume(self.original if obj is None else obj,**args)
    def test_accept_reject_link_to_original_business_transition_and_sql(self):
        self.stored();self.ledger.begin();obj,cash,paid,events,receipt=self.consume();self.ledger.commit()
        original=copy.deepcopy(self.original);bp,be=original.begin_day(1,1000.,active=2,payroll_per_member=1.,operating=True,expansion_allowed=True)
        self.assertEqual((obj.to_dict(),cash,paid,events),(original.to_dict(),1000.-bp,bp,be));self.assertTrue(receipt['new_request_funded'])
    def test_rejected_never_creates_request_and_reservation_cost_is_conserved(self):
        out,cost=self.stored(-1.);self.assertEqual(out['state'],'rejected');self.assertEqual(cost['used_hours'],.1)
        self.ledger.begin();obj,cash,paid,events,receipt=self.consume();self.ledger.commit()
        self.assertFalse(receipt['new_request_funded']);self.assertEqual(paid,0.);self.assertEqual(cash,1000.);self.assertIsNone(obj.pending_construction)
    def test_unresolved_releases_all_reserved_slot_without_free_votes(self):
        out,cost=self.stored(participates=False);self.assertEqual(out['state'],'unresolved')
        self.assertEqual((cost['reserved_hours'],cost['used_hours'],cost['released_idle_hours']),(.1,0.,.1))
        self.ledger.begin();self.assertEqual(self.consume()[2],0.);self.ledger.rollback()
    def test_actual_eligibility_full_rows_share_and_budget_guard(self):
        self.ledger.begin();self.reserve(0.);self.book.propose(self.p,0)
        with self.assertRaisesRegex(g.normative.Refused,'reserved'):self.book.decide(self.p,ballot(self.p))
        b=ballot(self.p,participates=False);b['votes'].pop()
        with self.assertRaisesRegex(g.normative.Refused,'roster'):self.book.decide(self.p,b)
        b=ballot(self.p,participates=False);b['votes'][0]['formal_share']=.5
        with self.assertRaisesRegex(g.normative.Refused,'share'):self.book.decide(self.p,b)
        b=ballot(self.p,participates=False);b['votes'][0]['person']=9
        with self.assertRaises(g.normative.Refused):self.book.decide(self.p,b)
        self.ledger.rollback()
    def test_current_cash_reserve_and_expiry_stale_negative_controls(self):
        self.stored();self.ledger.begin();self.assertEqual(self.consume(cash=149.)[4]['status'],'original_backend_refused');self.ledger.rollback()
        # SQL rollback alone does not rewind live in-memory cache: failed days require prior durable header restoration.
        state=self.book.to_dict();state['consumed']=None;state['consumption_context']=None;state['last_consumed_day']=None;state['pending_proposal_id']=self.p['proposal_id'];self.book=g.JournalRequestBook(self.ledger,'world:7:firm:test',state)
        self.ledger.begin();self.assertEqual(self.consume(day=2)[4]['status'],'expired');self.ledger.rollback()
        state['consumed']=None;state['consumption_context']=None;self.book=g.JournalRequestBook(self.ledger,'world:7:firm:test',state)
        changed=copy.deepcopy(self.original);changed.cost_signal+=1.
        self.ledger.begin();self.assertEqual(self.consume(changed)[4]['status'],'stale_or_ineligible');self.ledger.rollback()
    def test_savepoint_failure_rolls_back_request_sql_and_cache(self):
        self.stored();before=self.book.to_dict();rows=self.ledger.db.execute('select count(*) from journal').fetchone()[0]
        self.ledger.begin()
        with patch.object(self.ledger,'add_journal',side_effect=RuntimeError('injected journal write failure')):
            with self.assertRaisesRegex(RuntimeError,'journal'):self.consume()
        self.assertEqual(self.book.to_dict(),before);self.assertEqual(self.ledger.db.execute('select count(*) from journal').fetchone()[0],rows)
        self.assertTrue(self.ledger.db.in_transaction);self.ledger.rollback()
    def test_outer_rollback_removes_transition_and_new_owner_uses_durable_state(self):
        self.stored();before=self.book.to_dict();self.ledger.begin();self.consume();self.ledger.rollback()
        self.assertEqual(self.ledger.db.execute("select count(*) from journal where kind='governed_consumed'").fetchone()[0],0)
        self.book=g.JournalRequestBook(self.ledger,'world:7:firm:test',before)
        self.ledger.begin();self.assertTrue(self.consume()[4]['new_request_funded']);self.ledger.commit()
    def test_replay_before_and_after_is_no_double_debit_in_same_boundary(self):
        self.stored();self.ledger.begin();after,money,paid,events,receipt=self.consume();self.ledger.commit()
        self.assertGreater(paid,0.)
        for obj,cash in ((self.original,1000.),(after,money)):
            self.ledger.begin();again,againcash,paid2,_,_=self.consume(obj,cash=cash);self.ledger.commit()
            self.assertEqual((again.to_dict(),againcash,paid2),(after.to_dict(),money,0.))
        self.assertEqual(self.ledger.db.execute("select count(*) from journal where kind='governed_consumed'").fetchone()[0],1)
        self.ledger.begin()
        with self.assertRaises(g.normative.Refused):self.consume(after,cash=1000.)
        self.ledger.rollback()
    def test_no_request_still_honors_original_pending_obligations(self):
        self.stored();self.ledger.begin();obj,cash,_,_,_=self.consume();self.ledger.commit()
        ready=obj.pending_construction['ready_day'];baseline=copy.deepcopy(obj)
        bp,be=baseline.begin_day(ready,cash,active=2,payroll_per_member=1.,operating=False,expansion_allowed=False)
        self.ledger.begin();got,money,paid,events,receipt=self.book.consume(obj,day=ready,cash=cash,active=2,payroll_per_member=1.,operating=False,expansion_allowed=False);self.ledger.commit()
        self.assertEqual((got.to_dict(),money,paid,events),(baseline.to_dict(),cash-bp,bp,be));self.assertIsNone(receipt)
    def test_checkpoint_cache_matches_committed_day_end_and_tamper_fails(self):
        self.stored();self.ledger.begin();self.ledger.add_journal(0,'day_end','day:0',{'governed_operating_request_state':self.book.to_dict()});self.ledger.commit()
        restored=g.JournalRequestBook(self.ledger,'world:7:firm:test',self.book.to_dict());restored.verify_committed_header(1)
        restored.state['charter']['members'][0]['reserved_hours']=0.
        with self.assertRaises(g.normative.Refused):restored.verify_committed_header(1)
    def test_original_profile_roundtrip_optin_typed_version_and_scope(self):
        profile=json.loads((ROOT/'tests/fixtures/r102/original-firm-configs.json').read_bytes())
        for arm in profile['arms']:
            for name in ('buyer','target'):
                raw=arm[name+'_config'];cfg=Config.from_dict(raw);self.assertEqual(g.normative.canonical(cfg.to_dict()),g.normative.canonical(raw));self.assertEqual(g.model_version(cfg,'legacy'),'legacy')
                new=copy.deepcopy(raw);new['governed_operating_requests']={'schema_version':1,'governance_guild':0};cfg=Config.from_dict(new)
                self.assertEqual(g.normative.canonical(cfg.to_dict()),g.normative.canonical(new));self.assertEqual(g.model_version(cfg,'legacy'),g.VERSION)
                bad=copy.deepcopy(new);bad['governed_operating_requests']['schema_version']=2
                with self.assertRaises(ValueError):Config.from_dict(bad)
        self.assertNotEqual(g.scope_for(Config.from_dict(profile['arms'][0]['buyer_config'])),g.scope_for(Config.from_dict(profile['arms'][0]['target_config'])))
    def test_daily_ast_causal_order_and_all_persistence_hooks(self):
        raw=(ROOT/'dams_sim/longitudinal_model.py').read_text();tree=ast.parse(raw);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LongitudinalEngine')
        method={n.name:ast.get_source_segment(raw,n) for n in cls.body if isinstance(n,ast.FunctionDef)}
        daily=method['_step'];self.assertLess(daily.index('self.enterprise.observe'),daily.index('self._governed_end_day'));self.assertLess(daily.index('self.governed_requests.reserve'),daily.index("self.rng.normal('proposal_value'"))
        late=method['_governed_end_day'];self.assertLess(late.index('book.propose'),late.index("'operating_vote'"));self.assertLess(late.index("'operating_vote'"),late.index('book.decide'));self.assertLess(late.index('book.decide'),late.index('book.settle'));self.assertNotIn('theta',late)
        for name in ('header','restore','summary'):self.assertIn('governed',method[name])
        self.assertIn('self.governed_requests.consume',method['_enterprise_begin_day']);self.assertIn('verify_committed_header',method['validate_semantics'])
    def test_exact_normative_and_original_growth_pin_and_no_new_sql_schema(self):
        self.assertEqual(hashlib.sha256((ROOT/'dams_sim/governed_request_normative_r98.py').read_bytes()).hexdigest(),'b6d76f3f96a23f8c87a47889c104961f820d9efca93d45258ec1b05c8c711e26')
        self.assertEqual(hashlib.sha256((ROOT/'dams_sim/enterprise_growth.py').read_bytes()).hexdigest(),g.normative.BACKEND_SHA)
        actual=self.ledger.db.execute("select name from sqlite_master where type='table'").fetchall();self.assertFalse(set(('proposals','outcomes','consumed')) & {x[0] for x in actual})
        self.ledger.begin();self.reserve();self.book.propose(self.p,0);self.book.propose(self.p,0);self.ledger.commit()
        self.assertEqual(self.ledger.db.execute("select count(*) from journal where kind='governed_proposals'").fetchone()[0],1)

if __name__=='__main__':unittest.main()
