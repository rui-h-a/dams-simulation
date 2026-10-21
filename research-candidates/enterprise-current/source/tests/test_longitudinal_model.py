"""Mechanism and failure tests for the opt-in longitudinal extension."""
import dataclasses
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig, GregorianClock, anniversary, interval_probability, retention, estimate_longitudinal
from dams_sim.model import Model, Claim
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.storage import canonical


class LongitudinalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.counter=0;self.models=[]
    def tearDown(self):
        for model in self.models:model.ledger.close()
        self.temp.cleanup()
    def directory(self):
        self.counter+=1;return self.root/str(self.counter)
    def config(self,days=40,**long):
        params={'annual_exit_probability':0.,'adoption_mode':'never','annual_vacancy_fill_probability':0.}
        params.update(long)
        return Config(n=8,days=days,guilds=2,team_size=2,trace_every_days=1,max_output_mb=100,
                      longitudinal=LongitudinalConfig(**params)).validate()
    def model(self,p):
        model=Model(p,storage_dir=self.directory());self.models.append(model);return model

    def test_calendar_anniversary_and_hazard_composition(self):
        clock=GregorianClock(LongitudinalConfig(calendar_start='2020-02-28',holidays=('2020-03-02',)))
        self.assertEqual(clock.at(1),date(2020,2,29));self.assertEqual(clock.anniversary_day(1,5),1827)
        self.assertFalse(clock.is_workday(3));self.assertEqual(clock.days_in_year(0),366)
        self.assertAlmostEqual(1-(1-interval_probability(.2,1,366))**366,.2)
        self.assertEqual(interval_probability(1,0,366),0.);self.assertEqual(interval_probability(1,1,366),1.)
        self.assertAlmostEqual(retention(730,365)**2,.5)

    def test_legacy_opt_in_is_absent_and_long_unknown_rejected(self):
        self.assertNotIn('longitudinal',Config().to_dict())
        self.assertEqual(Config.from_dict(Config().to_dict()),Config())
        with self.assertRaises(ValueError):Config.from_dict({'longitudinal':{'unknown':1}})
        with self.assertRaises(ValueError):self.config(days=40,max_active_members=100,max_people_ever=101).validate().__class__(n=8,days=40,guilds=2,max_events=1,longitudinal=LongitudinalConfig()).validate()

    def test_weekend_rest_and_cost_not_removed_from_denominator(self):
        m=self.model(self.config(days=7,calendar_start='2020-01-06',adoption_mode='fixed',training_hours_per_workday=.2)).run()
        self.assertEqual(m.summary()['active_member_workdays'],40)
        self.assertEqual(m.summary()['present_member_workdays'],40)
        self.assertEqual(m.summary()['work_events'],40)
        self.assertEqual([r['output_work_units'] for r in m.history[-2:]],[0.,0.])
        self.assertAlmostEqual(m.summary()['available_member_work_hours'],5*sum(1-a.care_hours for a in m.agents))
        self.assertGreater(m.summary()['training_hours'],0)

    def test_unfunded_or_insufficient_capacity_cannot_create_free_governance(self):
        # Both zero funding and positive funding too small for the indivisible
        # .05 vote/admin cost must keep attempted decisions unresolved.
        for initial_cash in (0.,.001):
            m=self.model(self.config(days=1,initial_cash_per_member=initial_cash,
                                    revenue_resource_units_per_work_unit=0.))
            m.run();summary=m.summary()
            votes=[json.loads(r[4]) for r in m.ledger.rows('journal') if r[2]=='vote']
            self.assertTrue(votes)
            self.assertTrue(all(not v['participates'] for v in votes))
            self.assertEqual(summary.get('decisions_completed',0),0)
            self.assertEqual(summary['decisions_unresolved'],2)
            self.assertEqual(summary['governance_hours'],0)
            self.assertEqual(summary['present_member_workdays'],8)
            self.assertLessEqual(summary['max_individual_time_booked_hours'],1)
            if initial_cash==0:
                self.assertEqual(summary.get('work_events',0),0)
                self.assertFalse(any(r[2]=='work' for r in m.ledger.rows('journal')))

    def test_exit_preserves_pending_credit_and_identity_never_reused(self):
        m=self.model(self.config(days=6,exit_schedule=((1,0,'exit'),),annual_vacancy_fill_probability=1.,historical_verified_credit_per_member=10.))
        m.step();m.ledger.push('commit',0,Claim(1,'manual:retired',0,0,5.,False,False));m.ledger.commit();m.run()
        self.assertIsNotNone(m.people[0]['exited_day']);self.assertEqual(m.agents[0].share,0.)
        self.assertGreater(m.ledger.credit(0,0),10.)
        new=m.people[8];self.assertEqual(new['slot'],0);self.assertNotEqual(new['token'],m.people[0]['token'])
        self.assertEqual(new['entered_day'],1)
        self.assertEqual(m.people[0]['active_calendar_days'],1)
        self.assertTrue(m.ledger.db.execute("SELECT 1 FROM seen WHERE event='manual:retired'").fetchone())

    def test_retirement_and_expansion_are_real_lifecycle_events(self):
        m=self.model(self.config(days=5,initial_age_min_years=65.,initial_age_max_years=65.,retirement_age_years=65.,annual_vacancy_fill_probability=1.,max_active_members=10,max_people_ever=30,workforce_targets=((2,10),))).run()
        self.assertEqual(m.summary()['retirements'],8)
        self.assertEqual(len(m.active),10);self.assertEqual(len(m.agents),18)
        self.assertTrue(all(a.confirmed==0 for a in m.agents if m.people[a.id]['entered_day']==2))

    def test_initial_retirement_age_on_leap_day_is_not_delayed(self):
        m=self.model(self.config(days=2,calendar_start='2020-02-29',initial_age_min_years=65.,initial_age_max_years=65.,retirement_age_years=65.)).run()
        self.assertEqual(m.summary()['retirements'],8)
        self.assertTrue(all(r['exited_day']==0 and r['birth_date']=='1955-02-28' for r in m.people))
        self.assertEqual(m.summary()['active_member_calendar_days'],0)

    def test_domain_move_and_merger_do_not_mint_credit(self):
        m=self.model(self.config(days=5,guild_moves=((1,0,1),),guild_mergers=((2,0,1),),historical_verified_credit_per_member=10.)).run(2)
        self.assertEqual(m.agents[0].guild,1)
        self.assertEqual(m.ledger.credit(0,1),0.)
        self.assertGreater(m.ledger.credit(0,0),0.)
        m.run();self.assertEqual(set(m.members),{1})
        self.assertTrue(m.ledger.db.execute("SELECT 1 FROM journal WHERE kind='guild_merger'").fetchone())

    def test_adoption_cost_preserves_history_and_is_funded(self):
        no=self.model(self.config(days=12));yes=self.model(self.config(days=12,adoption_mode='fixed',adoption_day=5,training_hours_per_workday=.2))
        no.run(5);yes.run(5)
        self.assertEqual([dataclasses.asdict(a) for a in no.agents],[dataclasses.asdict(a) for a in yes.agents])
        self.assertEqual(no.ledger.semantic_digest(),yes.ledger.semantic_digest())
        no.run();yes.run();self.assertEqual(yes.adoption,{0:5,1:5})
        self.assertEqual(yes.summary()['migration_resource_units'],16.)
        self.assertLess(yes.summary()['produced_work_units'],no.summary()['produced_work_units'])
        empty=self.model(self.config(days=3,adoption_mode='fixed',initial_cash_per_member=0.,revenue_resource_units_per_work_unit=0.)).run()
        self.assertTrue(all(v is None for v in empty.adoption.values()))

    def test_observable_trigger_cannot_use_future_truth(self):
        with self.assertRaises(ValueError):self.config(observable_trigger='future_regret')
        m=self.model(self.config(days=20,adoption_mode='observable',observable_trigger='active_members',trigger_threshold=8,trigger_not_before_day=4)).run()
        self.assertEqual(m.adoption,{0:4,1:4})

    def test_tail_does_not_create_new_records_or_votes(self):
        m=self.model(self.config(days=15,work_creation_stop_day=5)).run(5);events=m.summary()['work_events'];decisions=m.summary()['decisions_completed'];seen=m.ledger.seen_count()
        m.run();self.assertEqual(m.summary()['work_events'],events);self.assertEqual(m.summary()['decisions_completed'],decisions)
        self.assertGreater(m.ledger.seen_count(),seen)
        self.assertTrue(all(r['output_work_units']==0 for r in m.history[5:]))
        self.assertGreater(m.summary()['tail_available_member_work_hours'],0)

    def test_closure_not_filtered_and_zero_future_exposure(self):
        m=self.model(self.config(days=15,closure_day=5)).run()
        self.assertEqual(m.day,15);self.assertEqual(m.summary()['closure_day'],5)
        self.assertEqual(m.summary()['active_member_calendar_days'],40)
        self.assertTrue(all(r['active_members']==0 for r in m.history[5:]))
        self.assertTrue(all(a.share==0 for a in m.agents))

    def test_full_sidecar_checkpoint_resume_exact(self):
        p=dataclasses.replace(self.config(days=70,exit_schedule=((12,0,'exit'),),annual_vacancy_fill_probability=1.),update_interval_days=7)
        a=self.model(p).run(29);descriptor=a.write_checkpoint(self.root/'cp.json')
        self.assertEqual(len(descriptor['files']),2)
        b=Model.restore_checkpoint(self.root/'cp.json',storage_dir=self.directory(),expected_config=p)
        self.models.append(b)
        self.assertEqual(a.state(),b.state());a.run();b.run();self.assertEqual(a.state(),b.state())
        self.assertEqual(canonical(a.summary()),canonical(b.summary()))
        with self.assertRaises(ValueError):a.write_checkpoint(self.root/'cp.json')

    def test_corrupt_sidecar_and_partial_day_are_not_restartable(self):
        p=self.config(days=12);m=self.model(p).run(3);m.write_checkpoint(self.root/'cp.json')
        with (self.root/'cp.sqlite').open('ab') as f:f.write(b'broken')
        with self.assertRaises(ValueError):verify_snapshot(self.root/'cp.json')
        bad=self.model(dataclasses.replace(p,attack='forge',attack_budget_hours_per_day=8,attack_start_day=0,attack_stop_day=10))
        with self.assertRaises(ValueError):bad.step()
        with self.assertRaises(ValueError):bad.write_checkpoint(self.root/'partial.json')
        with self.assertRaises(RuntimeError):bad.step()

    def test_branch_full_prestate_and_future_schedule_boundary(self):
        p=self.config(days=10);parent=self.model(p).run()
        new=dataclasses.replace(p,days=40,regime='equal',review_capacity_per_member_day=1.1,
              longitudinal=dataclasses.replace(p.longitudinal,adoption_mode='fixed',adoption_day=10,workforce_targets=((20,6),),holidays=('2020-02-03',)))
        child=parent.fork(new,storage_dir=self.directory())
        self.models.append(child)
        self.assertEqual(parent.ledger.semantic_digest(),child.ledger.semantic_digest())
        self.assertEqual(parent.people,child.people);self.assertEqual(parent.day,child.day)
        child.run();self.assertEqual(child.branch_origin['parent_day'],10)
        with self.assertRaises(ValueError):parent.fork(dataclasses.replace(new,seed=999),storage_dir=self.directory())
        with self.assertRaises(ValueError):parent.fork(dataclasses.replace(new,longitudinal=dataclasses.replace(new.longitudinal,exit_schedule=((1,0,'exit'),))),storage_dir=self.directory())
        with self.assertRaises(ValueError):parent.fork(dataclasses.replace(new,longitudinal=dataclasses.replace(new.longitudinal,holidays=('2020-01-02',))),storage_dir=self.directory())

    def test_complete_five_year_calendar_lifecycle_run(self):
        days=(anniversary(date(2020,1,1),5)-date(2020,1,1)).days
        p=self.config(days=days,organization_initial_age_years=20,annual_exit_probability=.15,annual_vacancy_fill_probability=.999,adoption_mode='fixed',adoption_day=365,revenue_resource_units_per_work_unit=2.)
        p=dataclasses.replace(p,trace_every_days=30)
        m=self.model(p).run();m._long.validate_semantics()
        self.assertEqual(m.summary()['calendar_end_exclusive'],'2025-01-01');self.assertEqual(m.day,1827)
        self.assertGreater(m.summary()['exits'],0);self.assertGreater(m.summary()['entrants'],0)
        self.assertEqual(m.summary()['work_events'],sum(r['present_workdays'] for r in m.people))
        self.assertLessEqual(m.summary()['work_events'],estimate_longitudinal(p)['work_events'])
        self.assertGreater(m.ledger.db.execute('SELECT count(*) FROM journal').fetchone()[0],m.summary()['work_events'])
        self.assertLessEqual(len(m.history),63)


if __name__=='__main__':unittest.main()
