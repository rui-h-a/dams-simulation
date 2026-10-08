import dataclasses
from datetime import datetime,timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dams_sim.runtime import RuntimeLimits
from dams_sim.scheduler import Scheduler


class Clock:
    def __init__(self):self.now=1000.
    def wall(self):return self.now
    def monotonic(self):return self.now-1000


class Process:
    def __init__(self,clock,finish_at=None,ignore_kill=False):
        self.clock,self.finish_at,self.ignore_kill=clock,finish_at,ignore_kill
        self.terminated=0;self.killed=0;self.waits=[];self.dead=False
    def is_alive(self):return not self.dead and (self.finish_at is None or self.clock.now<self.finish_at)
    def terminate(self):self.terminated+=1
    def join(self,seconds):
        self.waits.append(seconds)
        remaining=max(0,self.finish_at-self.clock.now) if self.finish_at is not None else seconds
        if self.is_alive():self.clock.now+=min(seconds,remaining)
    def kill(self):
        self.killed+=1
        if not self.ignore_kill:self.dead=True


class StopGraceTests(unittest.TestCase):
    def scheduler(self,clock,*,grace=300,cutoff=1400):
        scheduler=object.__new__(Scheduler)
        scheduler.limits=SimpleNamespace(cooperative_stop_grace_seconds=grace,stop_cutoff=cutoff)
        return scheduler

    def test_checkpoint_can_finish_after_old_ten_seconds_without_kill(self):
        clock=Clock();process=Process(clock,finish_at=1120);scheduler=self.scheduler(clock)
        with patch('dams_sim.scheduler.time.time',clock.wall),patch('dams_sim.scheduler.time.monotonic',clock.monotonic):
            scheduler._reap_cooperatively([process])
        self.assertEqual(process.terminated,1);self.assertEqual(process.killed,0)
        self.assertEqual(clock.now,1120)

    def test_shared_grace_is_not_multiplied_by_number_of_workers(self):
        clock=Clock();processes=[Process(clock),Process(clock)];scheduler=self.scheduler(clock)
        with patch('dams_sim.scheduler.time.time',clock.wall),patch('dams_sim.scheduler.time.monotonic',clock.monotonic):
            scheduler._reap_cooperatively(processes)
        self.assertEqual(clock.now,1300)
        self.assertEqual([p.killed for p in processes],[1,1])
        self.assertEqual(processes[1].waits[0],0)

    def test_absolute_cutoff_clips_configured_grace(self):
        clock=Clock();process=Process(clock);scheduler=self.scheduler(clock,cutoff=1012)
        with patch('dams_sim.scheduler.time.time',clock.wall),patch('dams_sim.scheduler.time.monotonic',clock.monotonic):
            scheduler._reap_cooperatively([process])
        self.assertEqual(clock.now,1012);self.assertEqual(process.killed,1)
        self.assertEqual(process.waits[0],12)

    def test_unreaped_owned_worker_fails_closed_at_cutoff(self):
        clock=Clock();process=Process(clock,ignore_kill=True);scheduler=self.scheduler(clock,cutoff=1012)
        with patch('dams_sim.scheduler.time.time',clock.wall),patch('dams_sim.scheduler.time.monotonic',clock.monotonic):
            with self.assertRaisesRegex(RuntimeError,'reaping is unverified'):scheduler._reap_cooperatively([process])
        self.assertEqual(clock.now,1012)

    def test_runtime_fields_are_finite_and_cutoff_cannot_precede_deadline(self):
        value={'deadline_utc':datetime.fromtimestamp(1000,timezone.utc).isoformat(),
               'cooperative_stop_grace_seconds':300,'stop_cutoff_utc':datetime.fromtimestamp(1360,timezone.utc).isoformat()}
        limits=RuntimeLimits.from_dict(value)
        self.assertEqual(limits.stop_cutoff,1360)
        for invalid in (True,float('inf'),0,-1):
            with self.assertRaises(ValueError):RuntimeLimits.from_dict(value|{'cooperative_stop_grace_seconds':invalid})
        with self.assertRaises(ValueError):RuntimeLimits.from_dict(value|{'stop_cutoff_utc':datetime.fromtimestamp(999,timezone.utc).isoformat()})


if __name__=='__main__':unittest.main()
