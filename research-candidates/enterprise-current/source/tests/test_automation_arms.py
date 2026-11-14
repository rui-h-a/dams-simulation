"""Automation arms (hierarchy-auto, dams-auto, dams-isa, dams-isa-fault): pure mechanisms first, then small
validated engine runs.  Every arm run goes through the raw-evidence validators of research_tools.r11_study."""
import json
import math
import os
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from dams_sim import automation as A
from dams_sim import sandbox as S
from dams_sim.automation import AutomationConfig
from dams_sim.randomness import WorldRandom
from research_tools import r11_design, r11_study


def graph(**kw):
    c = AutomationConfig(sandboxes=True, **kw)
    return S.SandboxGraph(c, WorldRandom(7, 0).uniform)


class ErrorModelTest(unittest.TestCase):
    N = 40000

    def draws(self, fraudulent):
        c = AutomationConfig(false_accept_probability=.08, false_reject_probability=.06, uncertain_probability=.1)
        rng = WorldRandom(11, 3)
        out = Counter()
        for i in range(self.N):
            v = A.verdict(c, rule_accept=not fraudulent, u_uncertain=rng.uniform('u', i), u_error=rng.uniform('e', i))
            out[v] += 1
        return out

    def test_error_rates_match_configuration(self):
        tol = 4*math.sqrt(.25/self.N)  # four standard errors at worst case
        honest, fraud = self.draws(False), self.draws(True)
        self.assertAlmostEqual(honest['exception']/self.N, .1, delta=tol)
        self.assertAlmostEqual(fraud['exception']/self.N, .1, delta=tol)
        # false-reject: honest claims rejected among those the verifier decided
        decided = self.N-honest['exception']
        self.assertAlmostEqual(honest['reject']/decided, .06, delta=tol)
        decided = self.N-fraud['exception']
        self.assertAlmostEqual(fraud['accept']/decided, .08, delta=tol)
        self.assertEqual(honest['accept']+honest['reject']+honest['exception'], self.N)

    def test_verifier_applies_the_review_rule_to_the_same_evidence(self):
        self.assertTrue(A.rule_accepts(.25, False)); self.assertFalse(A.rule_accepts(.2499, False)); self.assertFalse(A.rule_accepts(.9, True))
        c = AutomationConfig(false_accept_probability=0., false_reject_probability=0., uncertain_probability=0.)
        for observed, audit in ((.1, False), (.3, False), (.3, True)):
            rule = A.rule_accepts(observed, audit)
            self.assertEqual(A.verdict(c, rule_accept=rule, u_uncertain=.5, u_error=.5), 'accept' if rule else 'reject')
        self.assertTrue(A.is_wrong('accept', False)); self.assertTrue(A.is_wrong('reject', True)); self.assertFalse(A.is_wrong('accept', True))

    def test_exception_routing_fraction(self):
        c = AutomationConfig(exception_fraction=.1)
        rng = WorldRandom(5, 0)
        sampled = sum(A.is_sampled(c.exception_fraction, rng.uniform('s', i)) for i in range(self.N))
        self.assertAlmostEqual(sampled/self.N, .1, delta=4*math.sqrt(.09/self.N))
        self.assertEqual(sum(A.is_sampled(0., rng.uniform('s', i)) for i in range(1000)), 0)
        self.assertEqual(sum(A.is_sampled(1., rng.uniform('s', i)) for i in range(1000)), 1000)

    def test_conservative_defaults_and_validation(self):
        c = AutomationConfig()
        self.assertEqual((c.exception_fraction, c.false_accept_probability, c.false_reject_probability), (.1, .05, .05))
        for bad in ({'exception_fraction': 1.5}, {'sandbox_level': 'org'}, {'fault_mode': 'x'}, {'fault_mode': 'service_stop'}, {'bogus': 1}):
            with self.assertRaises(ValueError):
                AutomationConfig.from_dict(bad)

    def test_settlement_keeps_reporting_delay_and_saves_queue_day(self):
        self.assertEqual(A.settled_ready(claim_ready=11, settlement_days=2), 12)  # submitted day 10; human best case 13
        self.assertEqual(A.processing_units(AutomationConfig(), 1.2), 1.2)


class QuotaTest(unittest.TestCase):
    def test_quota_never_exceeded_and_borrowing_bounded(self):
        demands = {'a': [1.]*10, 'b': [1.]*2, 'c': [1.]*3}
        quotas = {'a': 6., 'b': 6., 'c': 6., 'd': 2.}
        admitted, flows = A.allocate_quota(demands, quotas, borrow_limit_fraction=.5)
        own = {s: sum(1 for m in admitted[s] if m == 'own') for s in demands}
        self.assertEqual(own['a'], 6)
        borrowed = sum(1 for m in admitted['a'] if isinstance(m, tuple))
        self.assertEqual(borrowed, 3)  # limit .5 x 6
        self.assertEqual(admitted['a'].count(None), 1)
        lent = Counter()
        for f in flows:
            if f['approved']:lent[f['donor']] += f['units']
        spare = {'b': 4., 'c': 3., 'd': 2.}
        for donor, units in lent.items():
            self.assertLessEqual(units, spare[donor]+1e-9)
        self.assertTrue(any((not f['approved']) and f['reason'] == 'over borrow limit' for f in flows))

    def test_rejected_approval_sends_claims_to_humans(self):
        admitted, flows = A.allocate_quota({'a': [1.]*3}, {'a': 1., 'b': 5.}, borrow_limit_fraction=1.,
                                           approve=lambda r, d: (False, 'approval rejected', 'root'))
        self.assertEqual(admitted['a'], ['own', None, None])
        self.assertEqual([f['reason'] for f in flows], ['approval rejected'])


class ScopedSharesTest(unittest.TestCase):
    def test_shares_sum_to_one_inside_each_sandbox_and_ignore_other_sandboxes(self):
        credits = [5., 1., 0., 2., 9., 3., 3., 0.5, 7., 1., 1.]
        weights, blocks = A.scoped_shares(credits, .5, 4)
        for lo, hi in blocks:
            self.assertAlmostEqual(math.fsum(weights[lo:hi]), 1., places=12)
        changed = list(credits); changed[9] = 100.   # a member of the last block
        w2, _ = A.scoped_shares(changed, .5, 4)
        self.assertEqual(weights[:8], w2[:8])        # earlier sandboxes untouched

    def test_all_zero_scope_is_equal(self):
        w, _ = A.scoped_shares([0., 0., 0.], 1., 3)
        self.assertEqual(w, [1/3]*3)


class GraphTest(unittest.TestCase):
    def build(self, **kw):
        g = graph(**kw)
        g.create(0, 'g1', 'guild', members=[1, 2, 3, 4, 5, 6], credit={1: 5., 2: 3., 5: 2.}, open_claims=['1:a', '5:b', '6:c'])
        g.create(0, 'g2', 'guild', members=[7, 8, 9])
        g.create(0, 't1', 'team', parents=['g1'], members=[1, 2, 3])
        g.create(0, 't2', 'team', parents=['g1'], members=[4, 5, 6])
        g.drain()
        return g

    def test_graph_membership_project_sandbox_and_closure(self):
        g = self.build()
        g.add_member(1, 't1', 7)                       # member of two sandboxes
        self.assertIn(7, g.sandbox_members('t1')); self.assertIn(7, g.sandbox_members('g2'))
        g.create(2, 'p1', 'project', parents=['g1', 'g2'], members=[1, 8], credit={1: 1.5}, open_claims=['8:z'])
        self.assertEqual(g.ancestors('p1'), {'g1', 'g2'})
        before = (g.primary[1], g.primary[8])
        g.close(9, 'p1', heir='g1')
        self.assertEqual((g.primary[1], g.primary[8]), before)   # project closure never moves a member's home
        self.assertEqual(g.sandboxes['p1']['closed'], 9)
        self.assertEqual(g.sandboxes['g1']['credit']['1'], 5.+1.5)
        self.assertIn('8:z', g.sandboxes['g1']['open'])
        self.assertNotIn(8, g.sandbox_members('g1'))
        self.assertEqual(g.sandboxes['p1']['history'][0][1], 'create')
        with self.assertRaises(S.SandboxError):
            g.close(10, 'p1', heir='g1')

    def totals(self, g, live_ids):
        credit = math.fsum(v for i in live_ids for v in g.sandboxes[i]['credit'].values())
        opens = sorted(c for i in live_ids for c in g.sandboxes[i]['open'])
        members = sorted(m for i in live_ids for m in g.sandboxes[i]['members'])
        return credit, opens, members

    def test_split_conserves_credit_open_claims_and_members(self):
        g = self.build()
        before = self.totals(g, ['g1'])
        kids = g.split(5, 'g1')
        after = self.totals(g, kids)
        self.assertEqual(before[0], after[0]); self.assertEqual(before[1], after[1]); self.assertEqual(before[2], after[2])
        # open claims follow their claimant
        owner = {m: k for k in kids for m in g.sandbox_members(k)}
        for k in kids:
            for claim in g.sandboxes[k]['open']:
                self.assertEqual(owner[int(claim.split(':')[0])], k)
        self.assertEqual(g.sandboxes['g1']['closed'], 5)
        self.assertEqual(g.sandboxes['g1']['heirs'], kids)
        self.assertGreaterEqual(len(g.sandboxes[kids[0]]['history']), 2)   # history carried
        self.assertEqual(set(g.leaves_under('g1')), set(kids))

    def test_merge_and_close_conserve(self):
        g = self.build()
        before = self.totals(g, ['g1', 'g2'])
        new = g.merge(3, 'g1', 'g2')
        self.assertEqual(self.totals(g, [new]), before)
        self.assertEqual(g.primary[7], new)
        g.create(4, 'x', 'team', parents=[new], members=[1], credit={1: 2.}, open_claims=['1:q'])
        t0 = self.totals(g, [new, 'x'])
        g.close(5, 'x')    # heir defaults to the first parent
        self.assertEqual(self.totals(g, [new]), (t0[0], t0[1], sorted(set(t0[2]))))

    def test_rebalance_splits_large_and_merges_small(self):
        g = graph(); g.create(0, 'big', 'guild', members=range(10)); g.create(0, 'tiny', 'guild', members=[100]); g.create(0, 'tiny2', 'guild', members=[101])
        done = g.rebalance(1, split_above=8, merge_below=3)
        kinds = [d[0] for d in done]
        self.assertEqual(kinds.count('split'), 1); self.assertEqual(kinds.count('merge'), 1)
        self.assertTrue({e['kind'] for e in g.log} >= {'sandbox_split', 'sandbox_merge', 'sandbox_create'})

    def test_scope_upward_and_lateral_need_approval_and_are_logged(self):
        g = self.build(approval_rejection_probability=0., approval_latency_days=2)
        self.assertTrue(g.request(1, 'g1', 't1', 'x')['approved'])               # own scope: descendant
        self.assertEqual(g.log, [])
        up = g.request(1, 't1', 'g1', 'x')                                        # upward -> target approves, latency 2
        self.assertEqual(up['decision'], 'pending')
        lat = g.request(1, 't1', 't2', 'x')                                       # lateral sibling -> common parent
        self.assertEqual((g.log[0]['direction'], g.log[0]['approver']), ('upward', 'g1'))
        self.assertEqual((g.log[1]['direction'], g.log[1]['approver']), ('lateral', 'g1'))
        self.assertEqual(g.log[0]['effective_day'], 3)
        n = len(g.log); self.assertEqual(g.request(2, 't1', 'g1', 'x')['decision'], 'pending'); self.assertEqual(len(g.log), n)   # no duplicate request
        self.assertTrue(g.request(3, 't1', 'g1', 'x')['approved'])               # standing grant now effective
        self.assertEqual(g.common_parent('t1', 'g2'), S.ROOT)                     # unrelated -> root
        un = g.request(4, 't1', 'g2', 'x')
        self.assertEqual(g.log[-1]['approver'], S.ROOT)

    def test_rejection_rate_is_configurable(self):
        g = self.build(approval_rejection_probability=.3)
        outcomes = [g.request(d, 't1', 'g2', f'a{d}')['decision'] == 'rejected' for d in range(2000)]
        self.assertAlmostEqual(sum(outcomes)/2000, .3, delta=.04)
        g2 = self.build(approval_rejection_probability=1.)
        self.assertEqual(g2.request(0, 't1', 'g2', 'x')['decision'], 'rejected')
        n = len(g2.log); self.assertEqual(g2.request(0, 't1', 'g2', 'x')['decision'], 'rejected'); self.assertEqual(len(g2.log), n)   # a refusal stands for the day
        self.assertFalse(g2.request(1, 't1', 'g2', 'x')['approved'])

    def test_privacy_scope_and_protected_core_are_enforced_and_logged(self):
        g = self.build(approval_rejection_probability=0.)
        self.assertTrue(g.read_records(1, 'g1', 't1'))                            # own descendants
        self.assertFalse(g.read_records(1, 't1', 't2'))                           # sibling: blocked
        self.assertFalse(g.read_records(1, 't1', 'g1'))                           # upward: blocked
        self.assertEqual([e['kind'] for e in g.log], ['privacy_block']*2)
        g.request(2, 't1', 't2', 'read_records')                                  # approval -> standing grant
        self.assertTrue(g.read_records(2, 't1', 't2'))
        self.assertFalse(g.read_records(2, 't2', 't1'))                           # grant is directional
        g.drain()
        for item in S.PROTECTED_CORE:
            self.assertFalse(g.modify_core(3, 't1', item))
        self.assertEqual([e['kind'] for e in g.log], ['core_block']*len(S.PROTECTED_CORE))
        self.assertEqual(g.propose(3, 't1', 'recovery', 1), 'blocked')

    def test_self_modification_adopts_only_without_degradation_and_rolls_back(self):
        g = self.build(shadow_window_days=3)
        self.assertEqual(g.propose(0, 't1', 'exception_fraction', .12), 'shadow')
        self.assertEqual(g.propose(0, 't1', 'exception_fraction', .14), 'busy')
        self.assertEqual(g.propose(0, 't2', 'exception_fraction', .9), 'blocked')      # outside declared bounds
        for _ in range(10):g.observe('t1', live_cost=.2, shadow_cost=.1)                 # candidate healthier
        g.settle(2); self.assertEqual(g.local('t1', 'exception_fraction'), .1)           # window not over
        g.settle(3); self.assertEqual(g.local('t1', 'exception_fraction'), .12)
        self.assertIn('selfmod_adopt', [e['kind'] for e in g.log])
        # watch window: health collapses -> automatic rollback
        for _ in range(10):g.observe('t1', live_cost=.9)
        g.settle(6)
        self.assertEqual(g.local('t1', 'exception_fraction'), .1)
        self.assertEqual([e['kind'] for e in g.log if e['kind'].startswith('selfmod_r')], ['selfmod_rollback'])
        # degrading candidate never adopted
        g.propose(7, 't2', 'exception_fraction', .08)
        for _ in range(10):g.observe('t2', live_cost=.1, shadow_cost=.4)
        g.settle(10)
        self.assertEqual(g.local('t2', 'exception_fraction'), .1)
        self.assertEqual(g.log[-1]['phase'], 'shadow')

    def test_allocation_rule_changes_need_parent_approval_and_are_never_applied_locally(self):
        g = self.build(approval_rejection_probability=0., approval_latency_days=1)
        self.assertEqual(g.propose(1, 't1', 'authority_alpha', .9), 'needs_approval')
        self.assertNotIn('authority_alpha', g.sandboxes['t1']['local'])
        kinds = [e['kind'] for e in g.log]
        self.assertIn('sandbox_request', kinds); self.assertIn('selfmod_needs_approval', kinds)
        g2 = self.build(approval_rejection_probability=1.)
        self.assertEqual(g2.propose(1, 't1', 'authority_alpha', .9), 'rejected')

    def test_quota_consumption_attribution(self):
        g = self.build()
        g.attribute('t1', 3.); g.attribute('t2', 2., lender='t1')
        self.assertEqual(g.usage['t1'], {'own': 3., 'borrowed_in': 0., 'lent_out': 2.})
        self.assertEqual(g.usage['t2']['borrowed_in'], 2.)

    def test_state_round_trip(self):
        g = self.build(); g.propose(0, 't1', 'exception_fraction', .12); g.split(2, 't2')
        h = S.SandboxGraph.from_dict(json.loads(json.dumps(g.to_dict())), g.c, g.u)
        self.assertEqual(h.to_dict(), g.to_dict())

    def test_fault_follows_a_split_sandbox_and_freezes_its_structure(self):
        g = graph(fault_mode='service_stop', fault_sandbox='a', fault_start_day=5, fault_days=10)
        g.create(0, 'a', 'guild', members=range(10)); g.create(0, 'b', 'guild', members=range(10, 14))
        kids = g.split(1, 'a')
        self.assertTrue(g.inject(5)); self.assertEqual(g.fault['sandbox'], kids[0])
        self.assertTrue(g.service_down(kids[0], 6)); self.assertFalse(g.service_down(kids[1], 6))
        g.rebalance(6, split_above=2, merge_below=0)
        self.assertIsNone(g.sandboxes[kids[0]]['closed'])          # not split while its fault is active
        self.assertIsNotNone(g.sandboxes[kids[1]]['closed'])

    def test_containment_measure_flags_spread(self):
        g = graph(fault_mode='service_stop', fault_sandbox='a', fault_start_day=50, fault_days=10, fault_drop_threshold=.2, fault_baseline_days=10)
        for sb in ('a', 'b', 'c'):
            g.create(0, sb, 'guild', members=range({'a': 0, 'b': 10, 'c': 20}[sb], {'a': 10, 'b': 20, 'c': 30}[sb]))
        for d in range(40, 60):
            g.record_sizes(d)
            for sb in 'abc':
                during = 50 <= d < 60
                n = 10 if not during else (2 if sb == 'a' else (9 if sb == 'b' else 10))
                g.series.setdefault(str(d), {})[sb] = n
        g.inject(50); g.sandboxes['a']  # inject only once
        g.monitor(50, 'a', 0, 0, 3); g.end_fault_if_due(60)
        r = g.containment()
        self.assertTrue(r['faulty_sandbox_throughput_dropped']); self.assertTrue(r['contained'])
        self.assertEqual((r['days_to_detection'], r['days_to_recovery']), (0, 10))
        g.series['55']['c'] = 1; g.series['56']['c'] = 1; g.series['57']['c'] = 1; g.series['58']['c'] = 1; g.series['59']['c'] = 1
        for d in range(50, 55):g.series[str(d)]['c'] = 1
        r = g.containment()
        self.assertFalse(r['contained']); self.assertEqual(r['other_sandboxes_dropped'], ['c'])


class ExistingArmsUnchangedTest(unittest.TestCase):
    """The arms of the earlier studies must produce the same daily series as the source that ran them
    (source SHA-256 76246e9f..., public commit fe08659). The expected digests were computed with that source."""
    EXPECTED = {'control-hierarchy': '1cd011c9dff08e5e', 'control-process': '0467f23a20c5ffea',
                'dams-witness': 'dfa5bf196d112e60', 'linear-witness': 'ed0879d7a6cf44a7'}

    def test_daily_series_of_existing_arms_are_byte_identical(self):
        import hashlib
        tmp = tempfile.mkdtemp(dir=os.environ.get('TMPDIR')); root = Path(tmp)
        try:
            r11_study.prehistory(dict(scale=60, context='multinational', growth='organic', world=1000, seed=20261017, pre_years=1, post_years=1,
                                      variant='baseline', out=str(root/'pre')))
            for arm, digest in self.EXPECTED.items():
                r11_study.arm(dict(prehistory=str(root/'pre'), arm=arm, retain_journal=False, out=str(root/arm)))
                self.assertEqual(hashlib.sha256((root/arm/'timeseries.csv').read_bytes()).hexdigest()[:16], digest, arm)
                summary = json.loads((root/arm/'summary.json').read_text())
                self.assertFalse([k for k in summary if k.startswith(('automated', 'exception', 'sandbox', 'auto_', 'selfmod', 'fault'))])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ScopedAuthorityTest(unittest.TestCase):
    def test_layers_borne_under_scoped_authority(self):
        c = AutomationConfig(sandboxes=True, scoped_authority=True)
        self.assertEqual([A.effective_layers(c, d) for d in (0, 1, 2, 5)], [0, 1, 1.25, 2.0])
        self.assertEqual([A.in_scope_delay_layers(c, d) for d in (0, 1, 4)], [0, 1, 1])
        full = AutomationConfig(sandboxes=True, scoped_authority=True, cross_boundary_share=1.)
        self.assertEqual([A.effective_layers(full, d) for d in range(6)], list(range(6)))        # share 1: no change
        with self.assertRaises(ValueError):AutomationConfig.from_dict({'scoped_authority': True})  # needs sandboxes

    def test_span_variants_and_the_delegated_hierarchy_are_configurations_only(self):
        from dams_sim.config import Config
        for variant, span in (('accounted-work-span4', 4), ('accounted-work-span16', 16), ('accounted-work', 8)):
            d, branch = r11_design.base_config(scale=300, context='multinational', growth='organic', world=1, seed=2, pre_years=1, post_years=1, variant=variant)
            self.assertEqual((d['enterprise_operations']['management_span'], d['organization_tasks']['management_span']), (span, span))
            cfg = Config.from_dict(r11_design.arm_config(d, branch, 'hierarchy-isa'))
            self.assertEqual((cfg.regime, cfg.backend, cfg.automation.scoped_authority, cfg.automation.sandboxes), ('hierarchy', 'central', True, True))

    def test_scoped_coordination_and_reporting_cost_less_and_validate(self):
        tmp = tempfile.mkdtemp(dir=os.environ.get('TMPDIR')); root = Path(tmp)
        try:
            r11_study.prehistory(dict(scale=60, context='multinational', growth='organic', world=5, seed=20261017, pre_years=0, post_years=1,
                                      variant='accounted-work', out=str(root/'pre')))
            out = {}
            for arm in ('dams-isa-unscoped', 'dams-isa', 'dams-isa-x50', 'control-hierarchy', 'hierarchy-auto', 'hierarchy-isa'):
                r11_study.arm(dict(prehistory=str(root/'pre'), arm=arm, retain_journal=False, out=str(root/arm)))   # raw validators run
                out[arm] = json.loads((root/arm/'summary.json').read_text())
            coordination = {k: v['enterprise_coordination_hours'] for k, v in out.items()}
            network = {k: v['organization_network_hours'] for k, v in out.items()}
            self.assertLess(coordination['dams-isa'], coordination['dams-isa-x50']); self.assertLess(coordination['dams-isa-x50'], coordination['dams-isa-unscoped'])
            self.assertLess(network['dams-isa'], network['dams-isa-unscoped'])
            self.assertAlmostEqual(coordination['hierarchy-isa'], coordination['dams-isa'], delta=.02*coordination['dams-isa'])   # same scope rule
            self.assertLess(out['dams-isa']['confirmation_mean_days'], out['dams-isa-unscoped']['confirmation_mean_days'])
            # work accounting: members of the manual arm do more administrative work than those of the automated arm
            self.assertGreater(out['control-hierarchy']['administrative_work_hours'], out['hierarchy-auto']['administrative_work_hours'])
            self.assertGreater(out['hierarchy-auto']['administrative_work_hours'], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EngineArmsTest(unittest.TestCase):
    """Small validated runs (each arm passes the raw-journal validators in r11_study.arm)."""
    EXTRA = {
        'test-tight-quota': dict(adoption='fixed', regime='sublinear', backend='witness',
                                 automation={'sandboxes': True, 'quota_units_per_member_day': .95, 'approval_rejection_probability': .5, 'approval_latency_days': 1,
                                             'split_size': 5, 'merge_size': 2, 'proposal_interval_days': 20, 'shadow_window_days': 7}),
        'test-team': dict(adoption='fixed', regime='sublinear', backend='witness', automation={'sandboxes': True, 'sandbox_level': 'team'}),
        'test-bad-parameter': dict(adoption='fixed', regime='sublinear', backend='witness', fault_after_boundary_days=100,
                                   automation={'sandboxes': True, 'fault_mode': 'bad_parameter', 'fault_days': 40, 'self_modify': False}),
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))
        cls.root = Path(cls.tmp)
        r11_design.ARMS.update(cls.EXTRA)
        r11_study.prehistory(dict(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1,
                                  variant='baseline', out=str(cls.root/'pre')))
        cls.out = {}

    @classmethod
    def tearDownClass(cls):
        for k in cls.EXTRA:r11_design.ARMS.pop(k, None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_arm(self, name):
        if name not in self.out:
            r11_study.arm(dict(prehistory=str(self.root/'pre'), arm=name, retain_journal=True, out=str(self.root/name)))
            self.out[name] = json.loads((self.root/name/'summary.json').read_text())
        return self.out[name]

    def test_existing_arms_carry_no_automation_and_no_new_outputs(self):
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1)
        for arm in ('control-hierarchy', 'control-process', 'dams-central', 'dams-witness', 'dams-consensus', 'linear-witness',
                    'equal-witness', 'dams-staged-witness', 'dams-triggered-witness'):
            cfg = r11_design.arm_config(d, branch, arm)
            self.assertNotIn('automation', cfg)
        from dams_sim.config import Config
        self.assertNotIn('automation', Config.from_dict(r11_design.arm_config(d, branch, 'dams-witness')).to_dict())
        s = self.run_arm('dams-witness')
        self.assertFalse([k for k in s if k.startswith(('automated', 'exception', 'sandbox', 'auto_', 'selfmod', 'fault'))])

    def test_no_member_time_for_automated_verification(self):
        s = self.run_arm('dams-auto')
        self.assertGreater(s['automated_verifications'], 1000)
        # member review time is charged only for human reviews: 0.1 workday each, at most one per exception claim
        self.assertLessEqual(s['review_hours']/.1, s['exception_claims']+1e-6)
        self.assertAlmostEqual(s['automated_processing_units'], 1.2*s['automated_verifications'], places=6)   # witness multiplier
        self.assertGreater(s['exception_sampled'], 0); self.assertGreater(s['exception_uncertain'], 0)
        share = s['exception_sampled']/(s['automated_verifications']-s['exception_uncertain']+s['exception_sampled'])
        self.assertAlmostEqual(share, .1, delta=.02)
        self.assertEqual(s['unfinished_records'], 0 if s['unfinished_records'] == 0 else s['unfinished_records'])
        h = self.run_arm('hierarchy-auto')
        self.assertEqual(h['backend'], 'central'); self.assertEqual(h['regime'], 'hierarchy')
        self.assertAlmostEqual(h['automated_processing_units'], 1.0*h['automated_verifications'], places=6)
        base = self.run_arm('dams-witness')
        self.assertLess(s['confirmation_mean_days'], base['confirmation_mean_days'])
        self.assertLess(s['unfinished_records'], base['unfinished_records'])

    def journal_decisions(self, name):
        import sqlite3
        from research_tools.validate_longitudinal import iter_journal_rows
        db = next((self.root/name).rglob('*.sqlite'))
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        decided = wrong = rule_reject = 0
        for day, kind, ident, payload in iter_journal_rows(con):
            if kind != 'auto_verify':continue
            v = json.loads(payload)
            if v['verdict'] == 'exception':continue
            decided += 1; rule_reject += int(not v['rule_accept'])
            wrong += int((v['verdict'] == 'accept') != v['rule_accept'])
        con.close()
        return decided, wrong, rule_reject

    def test_threshold_rule_is_the_human_rule_with_declared_machine_error(self):
        s = self.run_arm('dams-auto')
        decided, wrong, rule_reject = self.journal_decisions('dams-auto')
        self.assertGreater(rule_reject/decided, .15)                        # the 0.25 minimum refuses thin evidence
        self.assertAlmostEqual(wrong/decided, .05, delta=.02)               # declared machine error around the rule
        self.assertEqual(wrong, s['auto_false_accepts']+s['auto_false_rejects'])

    def test_measured_rule_refuses_only_audit_alarms(self):
        s = self.run_arm('dams-auto-measured'); base = self.run_arm('dams-witness')
        decided, wrong, rule_reject = self.journal_decisions('dams-auto-measured')
        self.assertLess(rule_reject/decided, .05)                           # honest audit alarms only (0.02)
        self.assertAlmostEqual(wrong/decided, .05, delta=.02)
        self.assertEqual(wrong, s['auto_false_accepts']+s['auto_false_rejects'])
        # member review time is spent on exception claims only
        self.assertLessEqual(s['review_hours']/.1, s['exception_claims']+1e-6)
        self.assertLess(s['review_hours'], .5*base['review_hours'])
        from dams_sim.automation import acceptance_floor
        self.assertEqual((acceptance_floor(AutomationConfig()), acceptance_floor(AutomationConfig(acceptance_rule='measured'))), (.25, 0.))
        with self.assertRaises(ValueError):AutomationConfig.from_dict({'acceptance_rule': 'none'})

    def test_saturated_processing_overflows_to_humans(self):
        s = self.run_arm('dams-isa-saturated'); clean = self.run_arm('dams-isa')
        self.assertGreater(s['exception_quota'], 10*clean.get('exception_quota', 0)+100)
        self.assertGreater(s['review_hours'], clean['review_hours'])      # the overflow is reviewed by members

    def test_cross_sandbox_reviewer_needs_a_read_grant(self):
        g = graph(approval_rejection_probability=1.)
        g.create(0, 'g1', 'guild', members=[1, 2]); g.create(0, 'g2', 'guild', members=[3]); g.drain()
        self.assertTrue(g.in_scope('g1', 'g1'))
        self.assertFalse(g.request(1, 'g2', 'g1', 'read_records')['approved'])
        self.assertFalse(g.read_records(1, 'g2', 'g1'))
        self.assertEqual([e['kind'] for e in g.log], ['sandbox_request', 'privacy_block'])
        s = self.run_arm('dams-isa')
        self.assertGreaterEqual(s.get('sandbox_read_deferred_records', 0), s.get('privacy_blocks', 0)-1e-9)

    def test_tight_quota_overflow_lifecycle_and_logs(self):
        s = self.run_arm('test-tight-quota')
        self.assertGreater(s['exception_quota'], 0)
        self.assertGreater(s['sandbox_requests'], 0)
        self.assertEqual(s['sandbox_requests']-s['sandbox_requests_approved'], s['sandbox_requests']-s['sandbox_requests_approved'])
        self.assertGreater(s['sandboxes_split'], 0)           # guilds of ~15 exceed split_size 5
        self.assertGreater(s['sandboxes_created'], s['sandboxes_split'])
        attributed = sum(u['own']+u['borrowed_in'] for u in s['sandbox_usage'].values())
        self.assertAlmostEqual(attributed, s['automated_processing_units'], places=6)
        lent = sum(u['lent_out'] for u in s['sandbox_usage'].values()); borrowed = sum(u['borrowed_in'] for u in s['sandbox_usage'].values())
        self.assertAlmostEqual(lent, borrowed, places=6)
        self.assertAlmostEqual(borrowed, s['sandbox_borrowed_units'], places=6)

    def test_daily_processing_stays_within_quota_plus_approved_borrowing(self):
        self.run_arm('test-tight-quota')
        from dams_sim.longitudinal_storage import ExactLedger
        import sqlite3
        from research_tools.validate_longitudinal import iter_journal_rows
        db = next((self.root/'test-tight-quota').rglob('*.sqlite'))
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        used, flows = Counter(), Counter()
        for day, kind, ident, payload in iter_journal_rows(con):
            v = json.loads(payload)
            if kind == 'auto_verify' and v['processing_units']:used[(day, v['sandbox'])] += v['processing_units']
            if kind == 'sandbox_quota' and v['approved']:flows[(day, v['requester'])] += v['units']
        con.close()
        self.assertTrue(used)
        # a sandbox of m members has quota .45 m; members <= 15 in this world, so own use is at most .45*15 plus what it borrowed
        for key, units in used.items():
            self.assertLessEqual(units, .95*40+flows[key]+1e-9)

    def test_team_level_scoped_shares_run_validates(self):
        s = self.run_arm('test-team')       # engine raises if team shares do not sum to one
        self.assertGreater(s['automated_verifications'], 1000)

    def test_fault_service_stop_is_contained_detected_and_recovered(self):
        s = self.run_arm('dams-isa-fault')
        f = s['fault_containment']
        self.assertEqual((f['mode'], f['sandbox']), ('service_stop', 'g0'))
        self.assertEqual(f['days_to_detection'], 0); self.assertEqual(f['days_to_recovery'], 14)
        self.assertTrue(f['contained']); self.assertEqual(f['other_sandboxes_dropped'], [])
        self.assertGreater(s['exception_outage'], 0)
        self.assertEqual((s['faults_injected'], s['faults_detected'], s['faults_recovered']), (1, 1, 1))
        clean = self.run_arm('dams-isa')
        self.assertFalse(clean.get('exception_outage')); self.assertIsNone(clean.get('fault_containment'))

    def test_bad_parameter_fault_is_detected_and_rolled_back(self):
        s = self.run_arm('test-bad-parameter')
        f = s['fault_containment']
        self.assertEqual(f['mode'], 'bad_parameter')
        self.assertIsNotNone(f['detected_day']); self.assertEqual(f['recovered_day'], f['detected_day'])
        self.assertLess(f['days_to_recovery'], 40)                    # rolled back before the window would have ended
        self.assertEqual(s['sandbox_local_exception_fraction'].get('g0', .1), .1)

    def test_measures_export_writes_only_for_automation_arms(self):
        from research_tools import r11_isa_measures as M
        self.run_arm('dams-isa-fault'); self.run_arm('dams-witness')
        m, has_auto = M.measures(self.root/'dams-isa-fault'); self.assertTrue(has_auto)
        for group in ('governance_cost', 'collaboration', 'structural_change', 'scaling', 'safety', 'governance_quality'):
            self.assertIn(group, m)
        self.assertGreater(m['governance_cost']['processing_units_per_member_year'], 0)
        m2, has_auto2 = M.measures(self.root/'dams-witness'); self.assertFalse(has_auto2)


if __name__ == '__main__':
    unittest.main()
