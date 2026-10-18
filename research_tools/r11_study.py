"""R11 paired-world study driver.

prehistory: run one world from day 0 to the adoption boundary and write an
immutable checkpoint. arm: restore that checkpoint, fork the declared
governance arm, run to the common horizon, write the final state and run every
independent raw validator. batch: schedule prehistories and arms over worker
processes; one independent world (all its arms) is the inferential unit.

Retention: after a validated arm, the full compressed journal is either kept
(audit worlds) or released. Released journals are reproducible by deterministic
replay from the recorded config, source digest, seed and world; the receipt
retains the ledger semantic digest that a replay must reproduce.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import resource
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dams_sim.config import Config  # noqa: E402
from dams_sim.model import Model  # noqa: E402
from dams_sim.storage import canonical, atomic_csv, source_hash, file_digest  # noqa: E402
from research_tools.r11_design import base_config, arm_config, ARMS  # noqa: E402

CHUNK = 4_194_304


def _json(path, value):
    tmp = Path(str(path)+'.tmp')
    tmp.write_bytes(canonical(value)+b'\n')
    os.replace(tmp, path)


def _rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r/1048576 if sys.platform == 'darwin' else r/1024


def world_key(scale, context, growth, world, pre_years, post_years):
    return f'n{scale}-{context}-{growth}-pre{pre_years}-post{post_years}-w{world}'


def _segments(m, target, cpdir, config, root, *, every=365):
    """Run to target with yearly resumable checkpoints; keep only the latest one."""
    cpdir.mkdir(parents=True, exist_ok=True)
    while m._long.day < target:
        stop = min(target, m._long.day+every)
        m.run(stop)
        if stop < target:
            cp = cpdir/f'checkpoint-day-{stop:06d}.json'
            m.write_checkpoint(cp)
            for old in cpdir.glob('checkpoint-day-*'):
                if not old.name.startswith(f'checkpoint-day-{stop:06d}'):
                    old.unlink()
    return m


def _resume(cpdir, config, root):
    cps = sorted(cpdir.glob('checkpoint-day-*.json')) if cpdir.exists() else []
    if not cps:
        return None
    k = len(list(root.glob('work-r*')))
    return Model.restore_checkpoint(cps[-1], expected_config=config, storage_dir=root/f'work-r{k}')


def _retire_partial(out, names):
    """An interrupted attempt that never reached a resumable checkpoint leaves
    scratch directories; they are deterministic and are removed before the
    attempt is repeated. A previous failure receipt is kept under a new name."""
    import shutil
    for n in names:
        p = out/n
        if p.exists():
            shutil.rmtree(p)
    f = out/'failure.json'
    if f.exists():
        k = len(list(out.glob('failure-attempt-*.json')))
        f.rename(out/f'failure-attempt-{k}.json')


def prehistory(args):
    out = Path(args['out'])
    out.mkdir(parents=True, exist_ok=True)
    done = out/'prehistory.json'
    if done.exists():
        return json.loads(done.read_text())
    d, branch = base_config(scale=args['scale'], context=args['context'], growth=args['growth'], world=args['world'],
                            seed=args['seed'], pre_years=args['pre_years'], post_years=args['post_years'], variant=args.get('variant','baseline'))
    config = Config.from_dict(d)
    t, cpu = time.monotonic(), time.process_time()
    m = _resume(out/'progress', config, out)
    if m is None:
        _retire_partial(out, ('work',))
        m = Model(config, storage_dir=out/'work', journal_chunk_bytes=CHUNK)
    _segments(m, branch, out/'progress', config, out)
    cp = out/f'checkpoint-day-{branch:06d}.json'
    m.write_checkpoint(cp)
    m._long.ledger.close()
    for p in [out/'progress', *out.glob('work*')]:
        if p.exists():
            for f in sorted(p.rglob('*'), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            p.rmdir()
    receipt = {'kind': 'r11-prehistory', 'python': sys.version, 'source_sha256': source_hash(), 'config': d, 'branch_day': branch,
               'checkpoint': cp.name, 'checkpoint_sha256': file_digest(cp),
               'semantic_sha256': m.semantic_digest() if False else None,
               'wall_seconds': time.monotonic()-t, 'cpu_seconds': time.process_time()-cpu, 'peak_rss_mb': _rss_mb(),
               'active_members': len(m._long.active), 'ever_people': len(m._long.agents)}
    _json(done, receipt)
    return receipt


def _validate(model, final_json, parent_config, branch, summary):
    from dams_sim.longitudinal_model import verify_snapshot
    from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence
    from research_tools.validate_acquisition_accounts import verify_acquisition_accounts
    from research_tools.validate_organization_tasks import verify_organization_accounts
    env, db = verify_snapshot(final_json, expected_config=model.config)
    csv = final_json.with_name('timeseries.csv')
    history = ((branch, parent_config),) if parent_config is not None else ()
    verify_daily_evidence(csv, model.config, env['state'], db, config_history=history)
    verify_summary_evidence(summary, model.config, env['state'], db)
    receipts = {'longitudinal': 'pass', 'summary': 'pass'}
    if model.config.enterprise_acquisition is not None:
        verify_acquisition_accounts(model.config, env['state'], db); receipts['acquisition'] = 'pass'
    if model.config.organization_tasks is not None:
        r = verify_organization_accounts(model.config, env['state'], db); receipts['organization'] = 'pass'
        receipts['organization_formal_worlds_added'] = r.get('formal_worlds_added')
    return env, db, receipts


def _release_journal(database):
    """Write a compact state image without journal chunks; return its digest and the released bytes."""
    compact = Path(str(database).replace('.sqlite', '.compact.sqlite'))
    src = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
    try:
        src.execute(f"VACUUM INTO '{compact}'")
    finally:
        src.close()
    db = sqlite3.connect(compact)
    try:
        db.execute('DELETE FROM journal_chunks'); db.commit(); db.execute('VACUUM')
    finally:
        db.close()
    released = Path(database).stat().st_size
    return compact.name, file_digest(compact), released


def arm(args):
    pre = Path(args['prehistory'])
    meta = json.loads((pre/'prehistory.json').read_text())
    out = Path(args['out'])
    done = out/'arm.json'
    if done.exists():
        return json.loads(done.read_text())
    out.mkdir(parents=True, exist_ok=True)
    parent_d, branch = meta['config'], meta['branch_day']
    parent = Config.from_dict(parent_d)
    child_d = arm_config(parent_d, branch, args['arm'])
    child = Config.from_dict(child_d)
    t, cpu = time.monotonic(), time.process_time()
    m = _resume(out/'progress', child, out)
    if m is None:
        _retire_partial(out, ('restored', 'work'))
        base = Model.restore_checkpoint(pre/meta['checkpoint'], expected_config=parent, storage_dir=out/'restored')
        m = base.fork(child, storage_dir=out/'work')
        base._long.ledger.close()
    _segments(m, child.days, out/'progress', child, out)
    sim_wall = time.monotonic()-t
    final = out/'final_state.json'
    m.write_final_state(final)
    atomic_csv(out/'timeseries.csv', m.history)
    summary = json.loads(canonical(m.summary()))
    _json(out/'summary.json', summary)
    semantic = m.semantic_digest()
    ledger_semantic = m._long.ledger.semantic_digest()
    m._long.ledger.close()
    tv = time.monotonic()
    env, db, receipts = _validate(m, final, parent, branch, summary)
    validation_wall = time.monotonic()-tv
    retained = 'journal' if args.get('retain_journal') else 'compact'
    files = {}
    db_path = Path(db)
    if retained == 'compact':
        name, digest, released = _release_journal(db_path)
        files['compact_state'] = {'file': name, 'sha256': digest}
        files['released_journal_bytes'] = released
        db_path.unlink()
    for p in [out/'restored', out/'progress', *out.glob('work*')]:
        if p.exists():
            for f in sorted(p.rglob('*'), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            p.rmdir()
    receipt = {'kind': 'r11-arm', 'python': sys.version, 'arm': args['arm'], 'arm_spec': ARMS[args['arm']], 'source_sha256': source_hash(),
               'parent_prehistory_sha256': file_digest(pre/'prehistory.json'), 'branch_day': branch,
               'config': child_d, 'days': child.days, 'state_semantic_sha256': semantic,
               'ledger_semantic_sha256': ledger_semantic, 'validators': receipts, 'retention': retained,
               'files': {**files, 'final_state_json': file_digest(final), 'timeseries_csv': file_digest(out/'timeseries.csv'),
                         'summary_json': file_digest(out/'summary.json')},
               'simulation_wall_seconds': sim_wall, 'validation_wall_seconds': validation_wall,
               'cpu_seconds': time.process_time()-cpu, 'peak_rss_mb': _rss_mb(),
               'final_active_members': len(m._long.active), 'ever_people': len(m._long.agents),
               'closed_day': m._long.closed_day, 'formal_status': 'validated engineering arm; inference status set by the study plan'}
    _json(done, receipt)
    return receipt


def _run(job):
    kind, payload = job
    try:
        return kind, payload, (prehistory if kind == 'prehistory' else arm)(payload), None
    except BaseException as e:  # keep the failure as evidence
        Path(payload['out']).mkdir(parents=True, exist_ok=True)
        _json(Path(payload['out'])/'failure.json', {'kind': kind, 'error': f'{type(e).__name__}: {e}'})
        return kind, payload, None, f'{type(e).__name__}: {e}'


def batch(ns):
    import multiprocessing
    multiprocessing.set_executable(sys.executable)
    # Resolve once: storage rejects symbolic links in any directory component.
    root = Path(ns.out); root.mkdir(parents=True, exist_ok=True); root = root.resolve()
    worlds = [int(w) for w in ns.worlds.split(',')]
    arms = ns.arms.split(',')
    audit = {int(w) for w in ns.audit_worlds.split(',')} if ns.audit_worlds else set()
    plan = {'scale': ns.scale, 'context': ns.context, 'growth': ns.growth, 'pre_years': ns.pre_years,
            'post_years': ns.post_years, 'seed': ns.seed, 'variant': ns.variant, 'worlds': worlds, 'arms': arms, 'audit_worlds': sorted(audit),
            'source_sha256': source_hash(), 'jobs': ns.jobs, 'python': sys.version, 'executable': sys.executable}
    plan_path = root/'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text()) != json.loads(canonical(plan)):
        raise SystemExit('existing plan differs; use a new output directory')
    _json(plan_path, plan)
    log = (root/'events.jsonl').open('a')
    def note(**kw):
        log.write(json.dumps({'t': time.time(), **kw})+'\n'); log.flush()
    pres = {w: {'scale': ns.scale, 'context': ns.context, 'growth': ns.growth, 'world': w, 'seed': ns.seed,
                'pre_years': ns.pre_years, 'post_years': ns.post_years, 'variant': ns.variant,
                'out': str(root/world_key(ns.scale, ns.context, ns.growth, w, ns.pre_years, ns.post_years)/'prehistory')}
            for w in worlds}
    with cf.ProcessPoolExecutor(max_workers=ns.jobs) as ex:
        pending = {ex.submit(_run, ('prehistory', p)): w for w, p in pres.items()}
        while pending:
            fut = next(cf.as_completed(pending)); w = pending.pop(fut)
            kind, payload, result, error = fut.result()
            note(kind=kind, world=w, error=error, result=None if result is None else {k: result.get(k) for k in ('wall_seconds', 'peak_rss_mb')})
            if error is not None:
                continue
            if kind == 'prehistory':
                for a in arms:
                    job = {'prehistory': payload['out'], 'arm': a, 'retain_journal': w in audit,
                           'out': str(Path(payload['out']).parent/a)}
                    pending[ex.submit(_run, ('arm', job))] = w
    note(kind='batch-complete')
    failed = sorted(str(f.relative_to(root)) for f in root.rglob('failure.json'))
    if failed:
        raise SystemExit(f'{len(failed)} jobs failed; receipts kept: ' + ', '.join(failed[:5]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('batch',))
    p.add_argument('--scale', type=int, required=True)
    p.add_argument('--context', default='multinational')
    p.add_argument('--growth', default='organic')
    p.add_argument('--pre-years', type=int, default=2)
    p.add_argument('--post-years', type=int, default=5)
    p.add_argument('--seed', type=int, default=20261017)
    p.add_argument('--variant', default='baseline')
    p.add_argument('--worlds', required=True)
    p.add_argument('--arms', default='control-hierarchy,control-process,dams-witness,linear-witness')
    p.add_argument('--audit-worlds', default='')
    p.add_argument('--jobs', type=int, default=os.cpu_count())
    p.add_argument('--out', required=True)
    ns = p.parse_args()
    batch(ns)


if __name__ == '__main__':
    main()
