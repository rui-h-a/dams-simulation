"""Operation-only R11 preservation checks; never regenerate missing evidence.

The engine's historical source_hash is deliberately unchanged.  The additional
manifest binds the scientific design inputs that that core-only hash omits.
Legacy plans without this binding are inspection-only until a separately
validated migration exists; this module provides no migration bypass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from dams_sim.storage import atomic_json, canonical, file_digest

MANIFEST_SCHEMA = 'r11-science-and-execution-dependencies-v1'
RETENTION_POLICY = 'full-original-journal-v1'
DESIGN_INPUTS = (
    'research_tools/r11_design.py',
    'research_tools/r11_targets.py',
    'candidate-evidence/calendar-networks-r7.json',
)
EXECUTION_INPUTS = (
    'run.sh', 'pyproject.toml', 'uv.lock',
    'research_tools/r11_study.py', 'research_tools/r11_retention.py',
    'research_tools/r11_outcomes.py', 'research_tools/r11_inference.py',
    'research_tools/r11_world_count.py', 'research_tools/r11_figure.py',
    'research_tools/validate_longitudinal.py',
    'research_tools/validate_acquisition_accounts.py',
    'research_tools/validate_organization_tasks.py',
    'research_tools/longitudinal_derived.py',
    'research_tools/r11_archive.py', 'research_tools/cloud_archive.py',
    'research_tools/cloud_control.py', 'research_tools/cloud_worker.py',
    'cloud/r11-startup.sh',
)


def science_manifest(root):
    """Bind every core module and the complete R11 design/target/template inputs.

    Driver/transport changes do not prove scientific equivalence.  The original
    core digest remains separately checked by the engine on restore.
    """
    root = Path(root)
    names = sorted({str(p.relative_to(root)) for p in (root/'dams_sim').glob('*.py')} | set(DESIGN_INPUTS))
    if not any(n.startswith('dams_sim/') for n in names):
        raise ValueError('scientific dependency manifest has no core modules')
    def hashes(required):
        result = {}
        for name in required:
            p = root/name
            if p.is_symlink() or not p.is_file():
                raise ValueError('missing or symlink bound dependency: '+name)
            result[name] = file_digest(p)
        return result
    files = hashes(names)
    execution = hashes(EXECUTION_INPUTS)
    payload = {'files_sha256': files, 'execution_files_sha256': execution}
    return {'schema': MANIFEST_SCHEMA, **payload,
            'manifest_sha256': hashlib.sha256(canonical(payload)).hexdigest()}


def bind_plan(path, requested):
    """Create a new immutable plan or verify its complete existing binding."""
    path = Path(path)
    if path.exists():
        existing = json.loads(path.read_text())
        if 'scientific_dependencies' not in existing:
            raise ValueError('legacy plan has no complete science/design binding; '
                             'migration is not implemented; original plan preserved; inspect retention only')
        if existing['scientific_dependencies'] != requested['scientific_dependencies']:
            raise ValueError('scientific dependencies changed; do not resume or relabel the original study')
        if existing != requested:
            raise ValueError('existing plan differs; original plan preserved')
        return
    # An absent plan never authorizes relabelling old outputs under a new seed,
    # variant, source or retention policy.  Legacy reconstruction is not provided.
    if any(path.parent.iterdir()):
        raise ValueError('new plan requires an empty output directory; existing artifacts preserved')
    atomic_json(path, requested)


def retire_equivalent_work(root, checkpoint_ledger, *, keep):
    """After successful restore, retire only byte-inventoried equivalent caches.

    Closed single-writer operation is required.  Newer, corrupt, unfamiliar or
    additional files are kept under interruption names, rather than being
    mistaken for a redundant copy of the verified checkpoint.
    """
    from dams_sim.longitudinal_storage import snapshot_semantics
    root, keep = Path(root), Path(keep)
    for old in [root/'restored', *sorted(root.glob('work*'))]:
        if not old.is_dir() or old == keep:
            continue
        files = [p for p in old.rglob('*') if p.is_file() or p.is_symlink()]
        db = old/'.longitudinal-working.sqlite'
        equivalent = False
        if files == [db] and not db.is_symlink():
            try:
                actual = snapshot_semantics(db)
                equivalent = all(actual[k] == checkpoint_ledger.get(k)
                                 for k in ('schema_version', 'semantic_sha256', 'row_counts'))
            except Exception:
                equivalent = False
        k = 0
        if equivalent:
            while (root/f'cache-retirement-{k}.json').exists():
                k += 1
            atomic_json(root/f'cache-retirement-{k}.json', {
                'schema': 'r11-equivalent-cache-retirement-v1', 'directory': old.name,
                'database_sha256': file_digest(db), 'checkpoint_ledger': checkpoint_ledger,
                'verified_semantics': actual, 'restoration_completed_before_retirement': True,
                'action': 'retire equivalent closed working cache',
            })
            shutil.rmtree(old)
        else:
            while (root/f'interrupted-{old.name}-{k}').exists():
                k += 1
            old.rename(root/f'interrupted-{old.name}-{k}')


def retention_report(root):
    """Report original evidence availability, without simulating or changing it.

    Recorded byte and original ledger semantic hashes are checked here.  Full
    snapshot state/recovery and independent off-VM archive are separate gates.
    """
    root = Path(root)
    from dams_sim.longitudinal_storage import snapshot_semantics
    if not root.is_dir():
        raise ValueError('retention inspection requires an existing study directory')
    records = []
    for path in sorted(root.rglob('arm.json')):
        receipt = json.loads(path.read_text())
        final = path.parent/'final_state.json'
        row = {'arm': str(path.parent.relative_to(root)), 'receipt_sha256': file_digest(path),
               'retention': receipt.get('retention'), 'status': 'missing-original-journal',
               'rerun_requested': False}
        if receipt.get('retention') == 'journal' and final.is_file():
            envelope = json.loads(final.read_text())
            side = envelope.get('ledger', {})
            name = side.get('file')
            db = path.parent/name if isinstance(name, str) and Path(name).name == name else None
            if (db is not None and not db.is_symlink() and db.is_file()
                    and db.stat().st_size == side.get('bytes') and file_digest(db) == side.get('sha256')
                    and file_digest(final) == receipt.get('files', {}).get('final_state_json')):
                try:
                    semantics = snapshot_semantics(db)
                    if semantics['semantic_sha256'] == receipt.get('ledger_semantic_sha256'):
                        row['status'] = 'original-byte-and-ledger-bound-state-recovery-pending'
                        row['database'] = name
                    else:
                        row['reason'] = 'original ledger semantic digest differs; journal may be pruned'
                except Exception:
                    row['reason'] = 'ledger schema or semantics could not be verified'
        records.append(row)
    missing = [r['arm'] for r in records if r['status'] == 'missing-original-journal']
    return {'schema': 'r11-retention-inspection-v1', 'arms': records,
            'completed_arms': len(records), 'missing_original_journal': missing,
            'complete_preservation_accepted': False,
            'acceptance_pending': ['full snapshot semantics and zero-step recovery',
                                   'complete original prehistory and interruption inventory',
                                   'generation-fixed remote archive and independent off-GCP preservation']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    args = p.parse_args()
    print(json.dumps(retention_report(args.root), indent=2))
