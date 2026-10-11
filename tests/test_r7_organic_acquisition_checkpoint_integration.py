"""One native r7 organic-growth/M&A checkpoint engineering control.

Original frozen model inputs and source are preserved. Hardware/provider actions
are absent. Same-model restart equality complements the independent financial
and organization raw accountants; this does not validate a second evolved firm,
calibration, OOS prediction, or a new formal research world.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import sys
import tempfile
import time
import traceback
import unittest

SOURCE = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / 'fixtures/r7-organic-acquisition-checkpoint-integration.json'
HERE = None
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.storage import canonical, atomic_csv, file_digest, source_hash
from dams_sim.longitudinal_model import verify_snapshot
from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence, iter_journal_rows
from research_tools.validate_acquisition_accounts import verify_acquisition_accounts
from research_tools.validate_organization_tasks import verify_organization_accounts

CORE = 'f2ce5ba098f199dd71cac4cb9ffb34e85a76d0a8e4c4396d33a31072586bc108'
MANIFEST = 'b009417cba9ede52edd8184f557baafdab284fe7be3a5de7e4b2091defa2a7db'
CHECKPOINTS = (45, 60)


def sha(raw): return hashlib.sha256(raw).hexdigest()

def save(path, value):
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n'
    with path.open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    return {'file': str(path.relative_to(HERE)), 'bytes': len(raw), 'sha256': sha(raw)}

def obs():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return {'wall': time.monotonic(), 'cpu': time.process_time(),
            'peak_rss_bytes': int(r.ru_maxrss if sys.platform == 'darwin' else r.ru_maxrss * 1024)}

def elapsed(start):
    end = obs()
    return {'wall_seconds': end['wall'] - start['wall'], 'cpu_seconds': end['cpu'] - start['cpu'],
            'peak_rss_bytes_process_lifetime_cumulative': end['peak_rss_bytes']}

def assert_source():
    assert source_hash() == CORE
    assert file_digest(SOURCE / 'source-manifest.integrated-r7.json') == MANIFEST

def journal(model):
    return [(day, kind, ident, json.loads(payload))
            for day, kind, ident, payload in iter_journal_rows(model.ledger.db)]

def checkpoint_observables(m, day):
    a = m.acquisitions.targets['target-a']
    v = {'day': m.day, 'pending_construction': m.enterprise.pending_construction,
         'pending_hiring': m.enterprise.pending_hiring, 'target_status': a['status'],
         'integration_workdays': a['integration_workdays'], 'pending_source_evidence': len(a['pending_evidence']),
         'order_backlog_units': m.operations.backlog(), 'shipment_count': len(m.operations.shipments),
         'active_people': len(m.active), 'organic_entrants': m.metrics.get('entrants', 0),
         'acquired_people': m.metrics.get('acquired_people', 0),
         'guilds': m._long.guild_count, 'sites': m._long.site_count,
         'knowledge': dict(m._long.memory), 'dependency_stocks': m.organization.to_dict()['stocks']}
    assert m.day == day and v['order_backlog_units'] > 0
    if day == 45:
        assert v['pending_hiring'] is not None and v['pending_hiring']['ready_day'] == 47
        assert v['target_status'] == 'available' and v['acquired_people'] == 0
    if day == 60:
        assert v['target_status'] == 'integrating' and 0 < v['integration_workdays'] < 3
        assert v['pending_source_evidence'] == 1 and v['organic_entrants'] >= 5
        assert v['acquired_people'] == 0
    return v

def validate_raw(m, dest):
    begin = obs(); final = dest / 'final_state.json'
    m.write_final_state(final)
    env, db = verify_snapshot(final, expected_config=m.config)
    atomic_csv(dest / 'timeseries.csv', m.history)
    summary = m.summary(); save(dest / 'summary.json', summary)
    verify_daily_evidence(dest / 'timeseries.csv', m.config, env['state'], db)
    verify_summary_evidence(json.loads((dest / 'summary.json').read_bytes()), m.config, env['state'], db)
    ma = verify_acquisition_accounts(m.config, env['state'], db)
    org = verify_organization_accounts(m.config, env['state'], db)
    c = sqlite3.connect(db.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        assert c.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        chunks = c.execute('SELECT count(*) FROM journal_chunks').fetchone()[0]
        chunk_bytes = c.execute('SELECT * FROM journal_chunk_config').fetchone()[1]
    finally: c.close()
    assert chunk_bytes == 1048576 and chunks > 0 and org['days_verified'] == 90
    assert org['independent_transition_functions'] is True and ma['conserved_acquisition_cash'] is True
    return {'resource_cost': elapsed(begin), 'acquisition_accountant': ma, 'organization_accountant': org,
            'SQLite_integrity': 'ok', 'journal_schema': 2, 'journal_chunks': chunks,
            'chunk_target_bytes': chunk_bytes,
            'final_semantic_sha256': m.semantic_digest(), 'full_Model_state_sha256': sha(canonical(m.state())),
            'final_files': {p.name: {'bytes': p.stat().st_size, 'sha256': file_digest(p)}
                            for p in dest.iterdir() if p.is_file()}}

def observables(m, p):
    rows = journal(m)
    organic = [(d, v['person']) for d, k, _, v in rows if k == 'entry' and not v['initial']]
    acquired = [(d, v['person']) for d, k, _, v in rows if k == 'acquisition_entry']
    assert organic and len(acquired) == 4 and set(i for _, i in organic).isdisjoint(i for _, i in acquired)
    assert len(organic) == m.metrics['entrants'] and len(acquired) == m.metrics['acquired_people']
    assert m.metrics['recruitment_resource_units'] == len(organic) * p.longitudinal.recruitment_resource_units_per_person
    assert m.metrics['infrastructure_setup_resource_units'] > 0
    organic_ids = set(i for _, i in organic)
    assert any(v['person'] in organic_ids and v['effort'] > 0 for _, k, _, v in rows if k == 'work')
    assert any(k == 'infrastructure_complete' and v['guilds'] > p.guilds and v['sites'] > p.sites for _, k, _, v in rows)
    assert any(k == 'organization_plan' and organic_ids.issubset(set(v['topology']['active_people'])) for _, k, _, v in rows)
    a = m.acquisitions.targets['target-a']
    assert a['status'] == 'integrated' and len(a['employee_ids']) == 4 and a['integration_paid'] > 0
    assert a['retained_evidence'] and a['resolved_evidence'] and not a['pending_evidence']
    assert m.operations.acquired_orders_per_workday == 10. and m.operations.assumed_acquisition_debt == 2.
    assert all(m._long.policy(g) == ('hierarchy', 'central') for g in a['guilds'])
    assert all(m._long.memory[g] > 0 for g in a['guilds'])
    assert m._long.guild_count > p.guilds and m._long.site_count > p.sites
    return {'initial_people': p.n, 'organic_entrants': len(organic), 'acquired_people': len(acquired),
        'organic_entry_days': sorted({d for d, _ in organic}), 'acquired_entry_days': sorted({d for d, _ in acquired}),
        'final_active': len(m.active), 'final_ever': len(m.people), 'installed_capacity': m.enterprise.capacity,
        'guilds': m._long.guild_count, 'installed_departments': m.enterprise.departments,
        'task_departments': p.organization_tasks.department_count, 'sites': m._long.site_count,
        'paid_organic_recruitment': m.metrics['recruitment_resource_units'],
        'paid_infrastructure_setup': m.metrics['infrastructure_setup_resource_units'],
        'paid_acquisition_integration': a['integration_paid'], 'acquired_source_pending_resolved': len(a['resolved_evidence']),
        'acquired_orders_per_workday': m.operations.acquired_orders_per_workday,
        'assumed_acquisition_debt': m.operations.assumed_acquisition_debt,
        'acquired_policy_preserved': True, 'distinct_organic_and_acquired_identities': True}

def round_control(index, p):
    assert_source(); path = HERE / f'final-round-{index}'; path.mkdir(); opened = []; start = obs()
    try:
        full = Model(p, storage_dir=path / 'full-working', journal_chunk_bytes=1048576); opened.append(full)
        checkpoints = []
        for day in CHECKPOINTS:
            before = obs(); full.run(day)
            cp = path / f'checkpoint-day-{day:03d}.json'; full.write_checkpoint(cp)
            env, db = verify_snapshot(cp, expected_config=p)
            checkpoints.append((day, cp, sha(canonical(full.state())), full.semantic_digest()))
            save(path / f'checkpoint-day-{day:03d}-observables.json', checkpoint_observables(full, day))
            save(path / f'checkpoint-day-{day:03d}-cost.json', elapsed(before))
        before = obs(); full.run(); baseline_run = elapsed(before)
        expected_state = canonical(full.state()); expected_semantic = full.semantic_digest()
        final_observables = observables(full, p)
        final_dir = path / 'baseline-final'; final_dir.mkdir(); base_raw = validate_raw(full, final_dir)
        restores = []
        for day, cp, state_sha, semantic in checkpoints:
            before = obs(); m = Model.restore_checkpoint(cp, expected_config=p, storage_dir=path / f'restore-{day}-working'); opened.append(m)
            load_cost = elapsed(before)
            assert sha(canonical(m.state())) == state_sha and m.semantic_digest() == semantic
            checkpoint_observables(m, day)
            before = obs(); m.run(); tail_cost = elapsed(before)
            assert canonical(m.state()) == expected_state and m.semantic_digest() == expected_semantic
            assert observables(m, p) == final_observables
            dest = path / f'restored-{day}-final'; dest.mkdir(); raw = validate_raw(m, dest)
            restores.append({'checkpoint_day': day, 'checkpoint_load_cost': load_cost, 'tail_execution_cost': tail_cost,
                'initial_full_state_exact': True, 'final_full_state_exact': True, 'final_semantic_exact': True, 'raw_validation': raw})
        assert_source()
        result = {'round': index, 'status': 'PASS', 'baseline_tail_run_cost': baseline_run,
            'total_cost': elapsed(start), 'observables': final_observables,
            'baseline_raw_validation': base_raw, 'checkpoint_restores': restores,
            'formal_worlds_added': 0, 'calibration_completed': False, 'OOS_completed': False}
        save(path / 'receipt.json', result); return result
    except BaseException as e:
        save(path / 'failure.json', {'round': index, 'error': type(e).__name__, 'message': str(e),
            'traceback': traceback.format_exc(), 'cost': elapsed(start), 'source_sha256': source_hash(),
            'formal_worlds_added': 0, 'raw_retained': True})
        raise
    finally:
        for m in opened: m.ledger.close()

class OrganicAcquisitionCheckpointIntegrationTest(unittest.TestCase):
    def test_same_declared_native_fixture_across_hiring_and_MA_checkpoints(self):
        global HERE
        assert_source()
        p = Config.from_dict(json.loads(FIXTURE.read_bytes()))
        self.assertEqual(Config.from_dict(p.to_dict()), p)
        self.assertEqual((p.n, p.days, p.seed, p.world), (30, 90, 42, 0))
        retained = os.environ.get('DAMS_R7_ENGINEERING_OUTPUT_DIR')
        if retained is not None:
            output = Path(retained)
            self.assertTrue(output.is_absolute() and output.is_dir() and not output.is_symlink(),
                            'engineering output must be an absolute existing directory')
            output = output.resolve(strict=True)
            self.assertFalse(any(output.iterdir()), 'engineering output must be empty')
            self.exercise_retained_control(p, output)
        else:
            # macOS's default /var namespace may be a symlink; native Directory
            # requires the physical ancestor path even for recreatable CI data.
            with tempfile.TemporaryDirectory(prefix='r7-organic-MA-checkpoint-') as folder:
                self.exercise_retained_control(p, Path(folder).resolve(strict=True))
        assert_source()

    def exercise_retained_control(self, p, output):
        global HERE
        HERE = output
        rounds = [round_control(i, p) for i in (1, 2)]
        self.assertEqual(rounds[0]['observables'], rounds[1]['observables'])
        for key in ('full_Model_state_sha256', 'final_semantic_sha256'):
            self.assertEqual(rounds[0]['baseline_raw_validation'][key], rounds[1]['baseline_raw_validation'][key])

if __name__ == '__main__':
    unittest.main()
