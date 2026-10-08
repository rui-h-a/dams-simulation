"""Strict private execution limits, separate from scientific experiment factors."""
from __future__ import annotations
import dataclasses
from datetime import datetime, timezone, timedelta
import math
import os
from pathlib import Path
import shutil


def available_memory():
    try:
        memory = os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        if Path('/proc/meminfo').exists():
            lines = Path('/proc/meminfo').read_text().splitlines()
            memory = next(int(x.split()[1])*1024 for x in lines if x.startswith('MemAvailable:'))
        cgroup = Path('/sys/fs/cgroup/memory.max')
        if cgroup.exists() and cgroup.read_text().strip() != 'max':
            remaining = int(cgroup.read_text())-int(Path('/sys/fs/cgroup/memory.current').read_text())
            memory = min(memory, remaining)
        return memory
    except (OSError, ValueError, StopIteration, AttributeError):
        raise RuntimeError('available memory cannot be determined; explicit memory budget required')


def parse_deadline(value):
    try: dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError, AttributeError): raise ValueError('deadline_utc must be an ISO UTC time')
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError('deadline_utc must include UTC timezone')
    return dt.timestamp()

@dataclasses.dataclass(frozen=True)
class RuntimeLimits:
    version: int = 1
    workers_auto: bool = True
    max_workers: int = 384
    cpu_budget: int = 1
    memory_budget_bytes: int = 1_000_000_000
    per_world_rss_bytes: int | None = None
    max_events: int = 2_000_000
    max_output_bytes: int = 100_000_000
    batch_max_output_bytes: int = 8_000_000_000
    batch_max_output_files: int = 0
    per_world_output_files: int = 0
    min_free_disk_bytes: int = 100_000_000
    world_timeout_seconds: float = 300.
    deadline_utc: str = ''
    checkpoint_interval_days: int = 5
    checkpoint_interval_seconds: float = 60.
    max_retries: int = 1
    cooperative_stop_grace_seconds: float = 10.
    stop_cutoff_utc: str = ''
    provenance: dict = dataclasses.field(default_factory=dict)

    def to_dict(self): return dataclasses.asdict(self)
    @property
    def deadline(self): return parse_deadline(self.deadline_utc)
    @property
    def stop_cutoff(self):
        return parse_deadline(self.stop_cutoff_utc) if self.stop_cutoff_utc else self.deadline+self.cooperative_stop_grace_seconds

    @classmethod
    def from_dict(cls, value, *, deadline=None):
        if not isinstance(value,dict): raise ValueError('runtime limits must be a JSON object')
        unknown = set(value)-{f.name for f in dataclasses.fields(cls)}
        if unknown: raise ValueError('unknown runtime limits: '+str(sorted(unknown)))
        cpus = len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else (os.cpu_count() or 1)
        defaults = dict(cpu_budget=cpus, memory_budget_bytes=int(available_memory()*.65),
                        deadline_utc=deadline or (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        p = cls(**(defaults|value))
        integer_ranges = {'version':(1,1),'max_workers':(1,384),'cpu_budget':(1,cpus),
                          'memory_budget_bytes':(1_000_000,4_000_000_000_000),
                          'max_events':(1,1_000_000_000_000),'max_output_bytes':(100_000,10_000_000_000_000),
                          'batch_max_output_bytes':(100_000,100_000_000_000_000),
                          'batch_max_output_files':(0,1_000_000_000),'per_world_output_files':(0,1_000_000),
                          'min_free_disk_bytes':(0,10_000_000_000_000),
                          'checkpoint_interval_days':(1,3650),'max_retries':(0,10)}
        for name,(lo,hi) in integer_ranges.items():
            x=getattr(p,name)
            if type(x) is not int or not lo<=x<=hi: raise ValueError(name+' outside permitted integer range')
        if type(p.workers_auto) is not bool: raise ValueError('workers_auto must be boolean')
        if not p.workers_auto and p.max_workers>p.cpu_budget:
            raise ValueError('explicit workers exceed CPU budget')
        if p.per_world_rss_bytes is not None and (type(p.per_world_rss_bytes) is not int or not 1_000_000<=p.per_world_rss_bytes<=p.memory_budget_bytes):
            raise ValueError('per_world_rss_bytes exceeds whole-machine budget')
        for name in ('world_timeout_seconds','checkpoint_interval_seconds','cooperative_stop_grace_seconds'):
            x=getattr(p,name)
            if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not .1<=x<=86400:
                raise ValueError(name+' outside permitted finite range')
        if not isinstance(p.provenance,dict) or set(p.provenance)-{'instance_id','machine_type','zone','project_hash','packaged_commit','image_digest','environment','task_hash'}:
            raise ValueError('runtime provenance contains unsupported or private fields')
        if any(not isinstance(v,str) or len(v)>512 for v in p.provenance.values()):
            raise ValueError('runtime provenance values must be bounded strings')
        p.deadline
        if p.stop_cutoff<p.deadline:
            raise ValueError('stop_cutoff_utc precedes the scientific deadline')
        if p.max_output_bytes>p.batch_max_output_bytes: raise ValueError('world output bound exceeds batch output bound')
        if bool(p.batch_max_output_files)!=bool(p.per_world_output_files) or p.per_world_output_files>p.batch_max_output_files:
            raise ValueError('prospective file controls require matching nonzero batch/world bounds')
        return p
