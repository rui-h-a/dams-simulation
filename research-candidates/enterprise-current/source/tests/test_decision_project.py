"""Decision effect on operations (mechanism A) and project sandboxes with core-protection attempts (mechanism B).

Pure mechanisms first (debt recursion, output factor, project lifecycle on the sandbox graph), then small engine
runs at scale 30 that go through the raw-evidence validators of research_tools.r11_study, a checkpoint round trip
of the new state, and tampering controls that the validators must refuse."""
import dataclasses
import hashlib
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dams_sim import automation as A
from dams_sim import decision_effect as D
from dams_sim import sandbox as S
from dams_sim.automation import AutomationConfig
from dams_sim.config import Config
from dams_sim.decision_effect import DecisionEffectConfig
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.longitudinal_model import LongitudinalEngine, verify_snapshot
from dams_sim.model import Model
from dams_sim.randomness import WorldRandom
from dams_sim.storage import atomic_csv, canonical
from research_tools import r11_design, r11_study


def graph(**kw):
    return S.SandboxGraph(AutomationConfig(sandboxes=True, **kw), WorldRandom(7, 0).uniform)


class DecisionDebtTest(unittest.TestCase):
    def test_update_follows_the_declared_recursion(self):
        c = DecisionEffectConfig(gamma=.2, memory_workdays=4)
        d = D.update_debt(c, 0., c.reference_loss)                   # a wrong decision of the reference size
        self.assertAlmostEqual(d, .25)
        d = D.update_debt(c, d, 0.)                                  # a right decision
        self.assertAlmostEqual(d, .25+(0-.25)/4)
        self.assertAlmostEqual(D.update_debt(c, 0., 10*c.reference_loss), .25)   # the share is capped at 1
        self.assertEqual(D.update_debt(c, 0., 0.), 0.)

    def test_debt_converges_to_the_loss_share_and_factor_to_one_minus_gamma(self):
        c = DecisionEffectConfig(gamma=.2, memory_workdays=20)
        d = 0.
        for _ in range(400):d = D.update_debt(c, d, c.reference_loss)
        self.assertAlmostEqual(d, 1., places=6)
        self.assertAlmostEqual(D.output_factor(c, d), .8, places=6)
        for _ in range(400):d = D.update_debt(c, d, 0.)
        self.assertAlmostEqual(d, 0., places=6)
        for _ in range(400):d = D.update_debt(c, d, .5*c.reference_loss)
        self.assertAlmostEqual(d, .5, places=6)
        self.assertEqual(D.output_factor(DecisionEffectConfig(gamma=0.), .9), 1.)   # gamma 0 never changes output
        self.assertTrue(all(0 <= D.update_debt(c, x/10, y) <= 1 for x in range(11) for y in (0., .3, 5.)))

    def test_merged_guild_takes_the_member_weighted_mean(self):
        self.assertAlmostEqual(D.merge_debt(.2, 3, .6, 1), (.2*3+.6)/4)
        self.assertAlmostEqual(D.merge_debt(.2, 0, .6, 0), .4)    # two empty guilds: plain mean, as for memory and routine

    def test_default_reference_loss_is_the_mean_absolute_unit_normal(self):
        self.assertAlmostEqual(DecisionEffectConfig().reference_loss, math.sqrt(2/math.pi), places=9)

    def test_configuration_contract(self):
        for bad in ({'gamma': 1.5}, {'gamma': -.1}, {'memory_workdays': 0}, {'memory_workdays': 2.5}, {'reference_loss': 0.}, {'gamma': float('nan')}, {'gamma': True}, {'unknown': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):DecisionEffectConfig.from_dict(bad)
        self.assertEqual(DecisionEffectConfig.from_dict({}), DecisionEffectConfig())
        plain = Config(n=8, days=5, guilds=2, longitudinal=LongitudinalConfig(adoption_mode='never'))
        self.assertNotIn('decision_effect', plain.validate().to_dict())
        with_block = dataclasses.replace(plain, decision_effect=DecisionEffectConfig(gamma=.3)).validate()
        self.assertEqual(Config.from_dict(with_block.to_dict()), with_block)
        with self.assertRaises(ValueError):dataclasses.replace(Config(), decision_effect=DecisionEffectConfig()).validate()   # needs the longitudinal engine

    def test_variants_set_the_block_and_nothing_else_differs_from_accounted_work(self):
        a, _ = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1, variant='accounted-work')
        for name, gamma in (('accounted-work-decisions', .05), ('accounted-work-decisions-g20', .2)):
            b, _ = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1, variant=name)
            self.assertEqual(Config.from_dict(b).decision_effect.gamma, gamma)
            self.assertEqual({k: v for k, v in b.items() if k != 'decision_effect'}, a)
        self.assertNotIn('decision_effect', a)


class ProjectConfigTest(unittest.TestCase):
    def test_project_fields_are_hidden_unless_the_feature_is_on_so_earlier_arms_keep_their_digest(self):
        self.assertNotIn('project_sandboxes', AutomationConfig(sandboxes=True).to_dict())
        on = AutomationConfig(sandboxes=True, project_sandboxes=True)
        self.assertEqual(on.to_dict()['project_idle_days'], 30)
        self.assertEqual(AutomationConfig.from_dict(on.to_dict()), on)
        self.assertEqual((on.core_attempt_probability, on.allocation_attempt_probability), (.05, .05))

    def test_contract(self):
        for bad in ({'project_sandboxes': True}, {'sandboxes': True, 'project_sandboxes': True, 'project_idle_days': 0},
                    {'sandboxes': True, 'project_sandboxes': True, 'core_attempt_probability': .7, 'allocation_attempt_probability': .4},
                    {'sandboxes': True, 'project_sandboxes': True, 'core_attempt_probability': 1.2},
                    {'sandboxes': True, 'project_sandboxes': True, 'self_modify': False},
                    {'sandboxes': True, 'core_attempt_probability': .2}, {'sandboxes': True, 'project_idle_days': 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):AutomationConfig.from_dict(bad)
        AutomationConfig.from_dict({'sandboxes': True, 'project_sandboxes': True, 'self_modify': False, 'core_attempt_probability': 0., 'allocation_attempt_probability': 0.})
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1)
        cfg = r11_design.arm_config(d, branch, 'dams-isa-full')
        self.assertTrue(Config.from_dict(cfg).automation.project_sandboxes)
        cfg = dict(cfg); cfg.pop('organization_tasks')
        with self.assertRaises(ValueError):Config.from_dict(cfg)       # collaboration peers exist only in the organization-tasks model

    def test_attempt_draw_partitions_the_unit_interval(self):
        c = AutomationConfig(sandboxes=True, project_sandboxes=True, core_attempt_probability=.2, allocation_attempt_probability=.1)
        self.assertEqual([A.attempt_kind(c, u) for u in (0., .19, .2, .29, .31, .99)], ['core', 'core', 'allocation', 'allocation', None, None])
        self.assertIsNone(A.attempt_kind(AutomationConfig(sandboxes=True), 0.))
        self.assertEqual(A.attempt_kind(AutomationConfig(sandboxes=True, project_sandboxes=True, core_attempt_probability=1., allocation_attempt_probability=0.), .999), 'core')

    def test_earlier_arm_configurations_keep_their_digests(self):
        expected = {'control-hierarchy': 'f19fa3086825b433', 'dams-auto': '6df586a396a9e91b', 'dams-isa': 'd265fe2c60e012ec',
                    'hierarchy-isa': '67b57b675455cf84', 'dams-isa-fault': '47435fc290da64de'}      # computed with the source before these mechanisms
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1, variant='accounted-work')
        for arm, digest in expected.items():
            cfg = Config.from_dict(r11_design.arm_config(d, branch, arm))
            self.assertEqual(hashlib.sha256(canonical(cfg.to_dict())).hexdigest()[:16], digest, arm)


class ProjectGraphTest(unittest.TestCase):
    def build(self, **kw):
        g = graph(project_sandboxes=True, **kw)
        g.create(0, 'g1', 'guild', members=[1, 2, 3, 4, 5, 6], credit={1: 5., 2: 3.}, open_claims=['1:a', '5:b'])
        g.create(0, 'g2', 'guild', members=[7, 8, 9], credit={8: 1.}, open_claims=['9:c'])
        g.create(0, 't1', 'team', parents=['g1'], members=[1, 2, 3])
        g.drain()
        return g

    def test_first_approved_collaboration_creates_the_project_and_later_pairs_join_it(self):
        g = self.build(approval_rejection_probability=0.)
        r = g.collaborate(5, 'g2', 'g1', [8, 1])                     # the pair is unordered
        self.assertEqual((r['approved'], r['decision'], r['project']), (True, 'approved', 'p:g1+g2'))
        p = g.sandboxes['p:g1+g2']
        self.assertEqual((p['kind'], p['parents'], p['members'], p['last_active']), ('project', ['g1', 'g2'], [1, 8], 5))
        self.assertEqual([e['kind'] for e in g.log], ['sandbox_request', 'sandbox_create', 'project_join'])
        self.assertEqual((g.log[0]['actor'], g.log[0]['target'], g.log[0]['action'], g.log[0]['direction']), ('g1', 'g2', 'collaborate', 'lateral'))
        self.assertEqual((g.primary[1], g.primary[8]), ('t1', 'g2'))  # a project never changes a member's home
        g.drain()
        r2 = g.collaborate(9, 'g1', 'g2', [2, 8, 7])                  # later pair of the same homes: same project, standing grant
        self.assertEqual((r2['decision'], r2['project']), ('granted', 'p:g1+g2'))
        self.assertEqual(g.sandboxes['p:g1+g2']['members'], [1, 2, 7, 8]); self.assertEqual(g.sandboxes['p:g1+g2']['last_active'], 9)
        self.assertEqual([(e['kind'], e.get('persons')) for e in g.log], [('project_join', [2, 7])])   # no new request, only the new members
        self.assertEqual(g.project_members(), {'p:g1+g2': 4})
        self.assertEqual(sum(s['kind'] == 'project' for s in g.sandboxes.values()), 1)

    def test_refusal_stands_for_the_day_and_creates_nothing(self):
        g = self.build(approval_rejection_probability=1.)
        r = g.collaborate(3, 'g1', 'g2', [1, 8])
        self.assertEqual((r['approved'], r['decision'], r['project']), (False, 'rejected', None))
        n = len(g.log); self.assertFalse(g.collaborate(3, 'g1', 'g2', [2, 7])['approved']); self.assertEqual(len(g.log), n)   # one logged refusal per pair and day
        self.assertEqual([e['kind'] for e in g.log], ['sandbox_request']); self.assertFalse(g.log[0]['approved'])
        self.assertFalse(g.collaborate(4, 'g1', 'g2', [1, 8])['approved']); self.assertEqual(len(g.log), 2)               # next day: asked again
        self.assertEqual(g.project_members(), {})
        self.assertIsNone(g.project_for('g1', 'g2'))

    def test_pending_approval_does_not_collaborate_until_effective(self):
        g = self.build(approval_rejection_probability=0., approval_latency_days=2)
        self.assertEqual(g.collaborate(1, 'g1', 'g2', [1, 8])['decision'], 'pending')
        self.assertEqual(g.collaborate(2, 'g1', 'g2', [1, 8])['decision'], 'pending')
        r = g.collaborate(3, 'g1', 'g2', [1, 8])
        self.assertEqual((r['decision'], r['approved']), ('granted', True))

    def test_only_two_homes_outside_each_others_scope_collaborate_through_a_project(self):
        g = self.build()
        with self.assertRaises(S.SandboxError):g.collaborate(1, 'g1', 't1', [1, 2])   # t1 is inside g1's scope
        with self.assertRaises(S.SandboxError):g.collaborate(1, 'g1', 'g1', [1])

    def test_grant_expiry_asks_again_and_a_new_pair_after_closure_gets_a_new_generation(self):
        g = self.build(approval_rejection_probability=0., grant_validity_days=5)
        g.collaborate(0, 'g1', 'g2', [1, 8]); g.drain()
        self.assertEqual(g.collaborate(4, 'g1', 'g2', [1, 8])['decision'], 'granted')
        r = g.collaborate(5, 'g1', 'g2', [1, 8]); self.assertEqual(r['decision'], 'approved')    # grant expired: a fresh request
        self.assertEqual(g.log[0]['kind'], 'sandbox_request')
        self.assertEqual(g.close_idle_projects(40, 30), ['p:g1+g2'])
        g.drain()
        r = g.collaborate(41, 'g1', 'g2', [1, 8]); self.assertEqual(r['project'], 'p:g1+g2#2')

    def test_idle_closure_conserves_credit_and_open_claims(self):
        g = self.build(approval_rejection_probability=0.)
        g.collaborate(5, 'g1', 'g2', [1, 8])
        p = g.sandboxes['p:g1+g2']; p['credit'] = {'1': 2.5, '8': .5}; p['open'] = ['1:z', '8:y']   # a project can hold credit and claims
        live = ['g1', 'g2', 't1', 'p:g1+g2']
        def totals():
            return (math.fsum(v for i in live for v in g.sandboxes[i]['credit'].values()),
                    sorted(c for i in live for c in g.sandboxes[i]['open']), sorted(set(m for i in ['g1', 'g2', 't1'] for m in g.sandboxes[i]['members'])))
        before = totals()
        self.assertEqual(g.close_idle_projects(34, 30), [])                    # 29 idle days: still open
        self.assertEqual(g.close_idle_projects(35, 30), ['p:g1+g2'])           # idle for exactly project_idle_days
        self.assertEqual(totals(), before)
        self.assertEqual(g.sandboxes['g1']['credit']['1'], 5.+2.5); self.assertIn('8:y', g.sandboxes['g1']['open'])
        self.assertEqual((g.sandboxes['p:g1+g2']['closed'], g.sandboxes['p:g1+g2']['heirs']), (35, ['g1']))
        self.assertEqual((g.primary[1], g.primary[8]), ('t1', 'g2'))
        self.assertEqual(g.project_members(), {})
        with self.assertRaises(S.SandboxError):g.close(36, 'p:g1+g2', heir='g1')

    def test_heir_after_a_parent_was_split_is_its_first_live_successor(self):
        g = graph(project_sandboxes=True, approval_rejection_probability=0.)
        g.create(0, 'a', 'guild', members=range(10), credit={0: 1., 9: 2.}); g.create(0, 'b', 'guild', members=[20, 21])
        g.collaborate(1, 'a', 'b', [0, 20])
        kids = g.split(2, 'a')
        g.sandboxes['p:a+b']['credit'] = {'0': 4.}
        before = math.fsum(v for i in kids+['b', 'p:a+b'] for v in g.sandboxes[i]['credit'].values())
        self.assertEqual(g.project_heir('p:a+b'), kids[0])
        g.close_idle_projects(40, 30)
        self.assertEqual(math.fsum(v for i in kids+['b'] for v in g.sandboxes[i]['credit'].values()), before)
        self.assertEqual(g.sandboxes['p:a+b']['heirs'], [kids[0]])

    def test_a_project_under_a_guild_does_not_freeze_its_restructuring(self):
        g = graph(project_sandboxes=True, approval_rejection_probability=0.)
        g.create(0, 'big', 'guild', members=range(10)); g.create(0, 'other', 'guild', members=[100, 101, 102, 103, 104])
        g.collaborate(1, 'big', 'other', [0, 100])
        done = g.rebalance(2, split_above=8, merge_below=0)
        self.assertEqual([d[0] for d in done], ['split'])
        self.assertIsNone(g.sandboxes['p:big+other']['closed'])

    def test_core_attempts_cycle_through_the_protected_core_and_are_blocked_and_logged(self):
        g = graph(project_sandboxes=True)
        items = [g.next_core_item() for _ in range(2*len(S.PROTECTED_CORE)+2)]
        self.assertEqual(items, list(S.PROTECTED_CORE)*2+list(S.PROTECTED_CORE[:2]))
        g.create(0, 'g1', 'guild', members=[1]); g.drain()
        self.assertEqual(g.propose(1, 'g1', g.next_core_item(), 0.), 'blocked')
        e = g.drain()[-1]
        self.assertEqual((e['kind'], e['item'], e['protected']), ('core_block', S.PROTECTED_CORE[(2*len(S.PROTECTED_CORE)+2) % len(S.PROTECTED_CORE)], True))
        self.assertEqual(g.propose(2, 'g1', 'authority_alpha', .5), 'needs_approval')           # parentless sandbox: the root approves, never local
        self.assertEqual(g.drain()[-1]['kind'], 'selfmod_needs_approval')
        self.assertNotIn('authority_alpha', g.sandboxes['g1']['local'])
        self.assertEqual(S.COUNTED_EVENTS['selfmod_needs_approval'], 'allocation_attempts')

    def test_state_round_trip_keeps_projects_and_the_attempt_cycle(self):
        g = self.build(approval_rejection_probability=0.)
        g.collaborate(5, 'g1', 'g2', [1, 8]); g.next_core_item(); g.next_core_item()
        h = S.SandboxGraph.from_dict(json.loads(json.dumps(g.to_dict())), g.c, g.u)
        self.assertEqual(h.to_dict(), g.to_dict()); self.assertEqual(h.core_cycle, 2)
        self.assertEqual(h.next_core_item(), S.PROTECTED_CORE[2]); self.assertEqual(h.project_for('g1', 'g2'), 'p:g1+g2')
        self.assertNotIn('core_cycle', self.build().to_dict())     # earlier graphs serialise exactly as before


class PlainEngineDecisionEffectTest(unittest.TestCase):
    """The plain production branch (no organization-tasks model): the factor enters the produced work, the
    state survives a checkpoint and a scheduled guild merger, and the raw validator re-derives everything."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')); self.root = Path(self.temp.name); self.models = []
    def tearDown(self):
        for m in self.models:m.ledger.close()
        self.temp.cleanup()

    def config(self, gamma, days=40, **kw):
        return Config(n=16, days=days, guilds=4, team_size=2, trace_every_days=1, max_output_mb=60,
                      longitudinal=LongitudinalConfig(annual_exit_probability=0., annual_vacancy_fill_probability=0., adoption_mode='never',
                                                      guild_mergers=((12, 0, 1),)),
                      decision_effect=None if gamma is None else DecisionEffectConfig(gamma=gamma, memory_workdays=5), **kw).validate()

    def model(self, cfg, name):
        m = Model(cfg, storage_dir=self.root/name); self.models.append(m); return m

    def gate(self, m, name):
        from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence
        final = self.root/(name+'.json'); m.write_final_state(final); s, db = verify_snapshot(final, expected_config=m.config)
        t = self.root/(name+'.csv'); atomic_csv(t, m.history)
        verify_daily_evidence(t, m.config, s['state'], db)
        verify_summary_evidence(json.loads(canonical(m.summary())), m.config, s['state'], db)
        return s

    def test_factor_state_merger_checkpoint_and_validators(self):
        cfg = self.config(.4)
        whole = self.model(cfg, 'whole').run(); s = self.gate(whole, 'whole')
        self.assertIn('decision_state', s['state'])
        sm = whole.summary()
        self.assertGreater(sm['decision_effect_output_factor_count'], 0)
        self.assertEqual(sm['decision_effect_output_factor_count'], sm['work_events'])
        self.assertLess(sm['decision_effect_mean_output_factor'], 1.); self.assertGreater(sm['decision_effect_mean_output_factor'], 1-.4)
        # the merged guild 0 is gone as a domain; guild 1 holds a debt that started from the member-weighted mean
        debt = {int(k): v for k, v in s['state']['decision_state'].items()}
        self.assertTrue(all(0 <= v <= 1 for v in debt.values())); self.assertGreater(debt[1], 0.)
        # checkpoint boundary: state before, during and after the merger survives a restore bit for bit
        for stop in (8, 20):
            split = self.model(cfg, f'split{stop}').run(stop); path = self.root/f'cp{stop}.json'; split.write_checkpoint(path)
            record = json.loads(path.read_text())
            self.assertIn('decision_state', record['state'])
            restored = Model.restore_checkpoint(path, storage_dir=self.root/f'restored{stop}', expected_config=cfg); self.models.append(restored)
            self.assertEqual(restored._long.decision_state, split._long.decision_state)
            restored.run()
            self.assertEqual(restored.state(), whole.state())
            self.assertEqual(restored._long.semantic_digest(), whole._long.semantic_digest())

    def test_factor_applies_to_produced_work_and_gamma_zero_changes_nothing(self):
        zero = self.model(self.config(0.), 'zero').run(); absent = self.model(self.config(None), 'absent').run()
        self.assertEqual([r['output_work_units'] for r in zero.history], [r['output_work_units'] for r in absent.history])
        self.assertEqual({k: v for k, v in zero.summary().items() if not k.startswith('decision_effect')},
                         {k: v for k, v in absent.summary().items() if not k.startswith('decision_effect')})
        self.assertEqual(zero.summary()['decision_effect_mean_output_factor'], 1.)
        self.assertNotIn('decision_effect_output_factor_sum', absent.summary())
        strong = self.model(self.config(.9), 'strong').run()
        self.assertLess(strong.summary()['decision_effect_mean_output_factor'], .999)

    def test_state_without_its_block_is_refused(self):
        m = self.model(self.config(.4), 'x').run(6); path = self.root/'x.json'; m.write_checkpoint(path)
        with self.assertRaises(ValueError):
            Model.restore_checkpoint(path, storage_dir=self.root/'y', expected_config=dataclasses.replace(self.config(.4), decision_effect=None))


class EngineTest(unittest.TestCase):
    """Small validated runs: every arm passes the raw-journal validators in r11_study.arm."""
    EXTRA = {
        # collaboration refused half of the days, small guilds that split and merge, projects closed after 5 idle days, frequent attempts
        'test-project-life': dict(adoption='fixed', regime='sublinear', backend='witness',
                                  automation={'sandboxes': True, 'scoped_authority': True, 'project_sandboxes': True, 'approval_rejection_probability': .5,
                                              'split_size': 6, 'merge_size': 3, 'project_idle_days': 5, 'proposal_interval_days': 10, 'shadow_window_days': 5,
                                              'core_attempt_probability': .3, 'allocation_attempt_probability': .3}),
        'test-project-core': dict(adoption='fixed', regime='sublinear', backend='witness',
                                  automation={'sandboxes': True, 'scoped_authority': True, 'project_sandboxes': True, 'proposal_interval_days': 10,
                                              'core_attempt_probability': 1., 'allocation_attempt_probability': 0.}),
    }
    EXTRA_VARIANTS = {'test-decisions-gamma0': {'administrative_fatigue': True, 'administrative_fatigue_reference': 0.14, 'decision_effect': {'gamma': 0.}}}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=os.environ.get('TMPDIR')); cls.root = Path(cls.tmp)
        r11_design.ARMS.update(cls.EXTRA); r11_design.VARIANTS.update(cls.EXTRA_VARIANTS)
        cls.pre = {}
        for variant in ('accounted-work-decisions', 'accounted-work-decisions-g20', 'accounted-work', 'test-decisions-gamma0'):
            r11_study.prehistory(dict(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1,
                                      variant=variant, out=str(cls.root/('pre-'+variant))))
            cls.pre[variant] = cls.root/('pre-'+variant)
        cls.out = {}; cls.models = []

    @classmethod
    def tearDownClass(cls):
        for m in cls.models:m._long.ledger.close()
        for k in cls.EXTRA:r11_design.ARMS.pop(k, None)
        for k in cls.EXTRA_VARIANTS:r11_design.VARIANTS.pop(k, None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_arm(self, arm, variant='accounted-work-decisions'):
        key = (arm, variant)
        if key not in self.out:
            out = self.root/f'{arm}@{variant}'
            r11_study.arm(dict(prehistory=str(self.pre[variant]), arm=arm, retain_journal=True, out=str(out)))
            self.out[key] = (out, json.loads((out/'summary.json').read_text()))
        return self.out[key]

    def fork(self, arm, name, days=None, variant='accounted-work-decisions'):
        pre = self.pre[variant]; meta = json.loads((pre/'prehistory.json').read_text())
        parent = Config.from_dict(meta['config'])
        child_d = r11_design.arm_config(meta['config'], meta['branch_day'], arm)
        if days is not None:child_d['days'] = meta['branch_day']+days
        child = Config.from_dict(child_d)
        base = Model.restore_checkpoint(pre/meta['checkpoint'], expected_config=parent, storage_dir=self.root/(name+'-restored'))
        m = base.fork(child, storage_dir=self.root/(name+'-work')); base._long.ledger.close(); self.models.append(m)
        return m, child, parent, meta['branch_day']

    def organization_receipt(self, out):
        from research_tools.validate_organization_tasks import verify_organization_accounts
        env, db = verify_snapshot(out/'final_state.json', expected_config=Config.from_dict(json.loads((out/'arm.json').read_text())['config']))
        return verify_organization_accounts(Config.from_dict(env['state']['config']), env['state'], db)

    def test_earlier_automation_arms_keep_their_daily_series_byte_for_byte(self):
        # digests computed with the source before these mechanisms (same world; variant accounted-work)
        for arm, digest in {'dams-isa': '68af3512efbf17aa', 'hierarchy-auto': 'e5f8976ed5ada5ab'}.items():
            out, s = self.run_arm(arm, 'accounted-work')
            self.assertEqual(hashlib.sha256((out/'timeseries.csv').read_bytes()).hexdigest()[:16], digest, arm)
            self.assertFalse([k for k in s if k.startswith(('project', 'refused', 'allocation', 'decision_effect'))])

    # ---- mechanism A ----
    def test_mean_factor_is_between_one_minus_gamma_and_one_and_differs_between_arms(self):
        manual, auto = self.run_arm('control-hierarchy')[1], self.run_arm('dams-auto')[1]
        for s in (manual, auto):
            self.assertLess(s['decision_effect_mean_output_factor'], 1.); self.assertGreater(s['decision_effect_mean_output_factor'], 1-.05)
            self.assertEqual(s['decision_effect_output_factor_count'], s['work_events'])
            self.assertAlmostEqual(s['decision_effect_mean_output_factor'], s['decision_effect_output_factor_sum']/s['decision_effect_output_factor_count'], places=12)
            self.assertGreater(s['decisions_completed']+s['decisions_unresolved'], 100)
        self.assertNotEqual(manual['decision_effect_mean_output_factor'], auto['decision_effect_mean_output_factor'])
        self.assertTrue(set(self.organization_receipt(self.run_arm('dams-auto')[0])) >= {'decision_effect_days_verified'})

    def test_stronger_decision_dependence_gives_a_lower_factor(self):
        weak = self.run_arm('control-hierarchy')[1]['decision_effect_mean_output_factor']
        strong = self.run_arm('control-hierarchy', 'accounted-work-decisions-g20')[1]['decision_effect_mean_output_factor']
        self.assertLess(strong, weak); self.assertGreater(strong, 1-.2); self.assertLess(strong, 1.)

    def test_gamma_zero_equals_the_block_absent(self):
        absent_out, absent = self.run_arm('control-hierarchy', 'accounted-work')
        zero_out, zero = self.run_arm('control-hierarchy', 'test-decisions-gamma0')
        self.assertEqual((absent_out/'timeseries.csv').read_bytes(), (zero_out/'timeseries.csv').read_bytes())
        self.assertEqual(zero['decision_effect_mean_output_factor'], 1.)
        ignore = lambda k: k.startswith('decision_effect') or k == 'branch_origin'      # the branch origin binds the parent configuration digest
        self.assertEqual({k: v for k, v in zero.items() if not ignore(k)}, {k: v for k, v in absent.items() if not ignore(k)})
        self.assertFalse([k for k in absent if k.startswith('decision_effect')])

    def test_validators_refuse_a_factor_that_does_not_follow_the_decision_history(self):
        with mock.patch.object(D, 'output_factor', lambda c, debt: 1-1.5*c.gamma*debt):
            m, *_ = self.fork('dams-auto', 'tamper-factor')
            m.run(60)
            with self.assertRaisesRegex(ValueError, 'decision output factor'):
                m.write_checkpoint(self.root/'tamper-factor.json')

    # ---- mechanism B ----
    def test_dams_isa_full_creates_a_project_and_validates_while_dams_isa_has_none(self):
        out, s = self.run_arm('dams-isa-full')
        self.assertGreaterEqual(s['project_sandboxes_created'], 1); self.assertGreater(s['project_collaboration_pair_days'], 100)
        self.assertGreater(s['project_sandbox_member_workdays'], 1000)
        base = self.run_arm('dams-isa')[1]
        self.assertFalse([k for k in base if k.startswith(('project', 'refused', 'allocation'))])
        self.assertTrue(json.loads((out/'arm.json').read_text())['config']['automation']['project_sandboxes'])
        self.assertTrue(json.loads((self.run_arm('dams-isa')[0]/'arm.json').read_text())['arm_spec']['automation'].get('project_sandboxes') is None)
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1)
        h = Config.from_dict(r11_design.arm_config(d, branch, 'hierarchy-isa-full'))      # the delegated hierarchy likewise (run in the study, not here)
        self.assertEqual((h.regime, h.backend, h.automation.sandboxes, h.automation.scoped_authority, h.automation.project_sandboxes), ('hierarchy', 'central', True, True, True))

    def test_lifecycle_refusals_attempts_and_what_the_organization_verifier_checks(self):
        out, s = self.run_arm('test-project-life')
        self.assertGreater(s['project_sandboxes_created'], 5); self.assertGreater(s['project_sandboxes_closed'], 0)
        self.assertEqual(s['project_sandboxes_created']-s['project_sandboxes_closed'], s['project_sandboxes_live'])
        self.assertGreaterEqual(s['sandboxes_created'], s['project_sandboxes_created']); self.assertGreaterEqual(s['sandboxes_closed'], s['project_sandboxes_closed'])
        self.assertGreater(s['refused_collaboration_pair_days'], 0); self.assertGreater(s['refused_collaboration_links'], 0)
        import sqlite3
        from research_tools.validate_longitudinal import iter_journal_rows
        con = sqlite3.connect(f'file:{next(out.rglob("*.sqlite"))}?mode=ro', uri=True)
        asks = [json.loads(p) for _d, k, _i, p in iter_journal_rows(con) if k == 'sandbox_request']; con.close()
        asks = [a for a in asks if a['action'] == 'collaborate']
        self.assertGreater(len(asks), 100)
        self.assertAlmostEqual(sum(not a['approved'] for a in asks)/len(asks), .5, delta=.1)    # the declared rejection probability; approvals stand for 30 days
        self.assertGreater(s['core_blocks'], 0); self.assertGreater(s['allocation_attempts'], 0)
        self.assertGreater(s['selfmod_adoptions']+s['selfmod_rollbacks'], 0)       # the remaining 40 % of proposals are ordinary
        receipt = self.organization_receipt(out)
        self.assertGreater(receipt['collaboration_links_verified'], 1000)
        self.assertGreaterEqual(receipt['collaboration_exclusions_with_unverified_home'], 0)

    def test_core_attempts_at_probability_one_replace_every_local_proposal(self):
        out, s = self.run_arm('test-project-core')
        self.assertGreater(s['core_blocks'], 0)
        self.assertEqual(s.get('allocation_attempts', 0)+s.get('selfmod_adoptions', 0)+s.get('selfmod_rollbacks', 0), 0)
        import sqlite3
        from research_tools.validate_longitudinal import iter_journal_rows
        con = sqlite3.connect(f'file:{next(out.rglob("*.sqlite"))}?mode=ro', uri=True)
        blocks = [json.loads(p) for _d, k, _i, p in iter_journal_rows(con) if k == 'core_block']; con.close()
        self.assertEqual(len(blocks), s['core_blocks'])
        self.assertEqual([b['item'] for b in blocks], [S.PROTECTED_CORE[k % 5] for k in range(len(blocks))])
        self.assertTrue(all(b['protected'] for b in blocks))

    def test_measures_export_reports_the_new_quantities(self):
        from research_tools import r11_isa_measures as M
        out, s = self.run_arm('test-project-life')
        m, has_auto = M.measures(out); self.assertTrue(has_auto)
        self.assertEqual(m['collaboration']['project_sandboxes_created'], s['project_sandboxes_created'])
        self.assertEqual(m['collaboration']['collaboration_pair_days_refused'], s['refused_collaboration_pair_days'])
        self.assertEqual(m['safety']['allocation_rule_proposals_needing_approval'], s['allocation_attempts'])
        self.assertAlmostEqual(m['governance_quality']['decision_effect_mean_output_factor'], s['decision_effect_mean_output_factor'])

    def test_arm_branching_after_a_year_of_shared_history_validates(self):
        """The shared history before the branch has no automation (no project phase) but has the decision effect; the
        validators apply the arm's configuration only from the branch day on."""
        r11_study.prehistory(dict(scale=30, context='traditional', growth='organic', world=3, seed=2, pre_years=1, post_years=1,
                                  variant='accounted-work-decisions', out=str(self.root/'pre-year')))
        meta = json.loads((self.root/'pre-year'/'prehistory.json').read_text()); self.assertGreater(meta['branch_day'], 300)
        out = self.root/'branch-year'
        r11_study.arm(dict(prehistory=str(self.root/'pre-year'), arm='test-project-life', retain_journal=True, out=str(out)))
        s = json.loads((out/'summary.json').read_text())
        self.assertGreater(s['project_sandboxes_created'], 5)
        self.assertEqual(s['decision_effect_output_factor_count'], s['work_events']); self.assertGreater(s['work_events'], 6000)     # both years, one factor per work record

    def test_validators_refuse_exclusions_that_contradict_the_recorded_pair_decision(self):
        original = LongitudinalEngine._project_collaboration
        def tampered(self, day, present, links, working):
            excluded = original(self, day, present, links, working)
            for i in sorted(excluded):          # the first member with a refused pair is given its peers' help after all
                excluded[i] = set(); break
            return excluded
        with mock.patch.object(LongitudinalEngine, '_project_collaboration', tampered):
            m, *_ = self.fork('test-project-life', 'tamper-exclusion')
            m.run(80)
            with self.assertRaisesRegex(ValueError, 'collaboration exclusion differs'):
                m.write_checkpoint(self.root/'tamper-exclusion.json')

    def test_new_state_survives_a_checkpoint_in_the_middle_of_an_arm(self):
        """Decision debts, project sandboxes (members, last activity, closed parents), standing grants and the
        attempt cycle are written to a checkpoint 150 days into the arm and restored; the resumed arm ends in the
        same state as the uninterrupted one and passes every raw validator (the arm of one year crosses no yearly
        checkpoint of its own, so the boundary is created here)."""
        whole, child, parent, branch = self.fork('test-project-life', 'ckpt-whole')
        whole.run(branch+150)
        path = self.root/'ckpt.json'; whole.write_checkpoint(path)
        record = json.loads(path.read_text())['state']
        self.assertTrue(record['decision_state']); self.assertTrue(record['sandbox_state']['core_cycle'] > 0)
        self.assertTrue([s for s in record['sandbox_state']['sandboxes'].values() if s['kind'] == 'project' and s['closed'] is None])
        self.assertTrue([s for s in record['sandbox_state']['sandboxes'].values() if s['kind'] == 'project' and s['closed'] is not None])
        resumed = Model.restore_checkpoint(path, storage_dir=self.root/'ckpt-resumed', expected_config=child)
        self.assertEqual(resumed._long.decision_state, whole._long.decision_state)
        self.assertEqual(resumed._long._sbg.to_dict(), whole._long._sbg.to_dict())
        whole.run(); resumed.run()
        self.assertEqual(resumed.state(), whole.state())
        self.assertEqual(resumed._long.semantic_digest(), whole._long.semantic_digest())
        self.assertEqual(canonical(resumed.summary()), canonical(whole.summary()))
        final = self.root/'ckpt-final.json'; resumed.write_final_state(final); atomic_csv(self.root/'timeseries.csv', resumed.history)
        summary = json.loads(canonical(resumed.summary()))
        resumed._long.ledger.close()
        r11_study._validate(resumed, final, parent, branch, summary)      # plain and organization validators over the whole run


if __name__ == '__main__':
    unittest.main()
