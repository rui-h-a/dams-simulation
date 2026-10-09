"""Bounded runtime identity and actual SQLite capability observations."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
KIT = Path(__file__).resolve().parent


def verify_pins():
    pins = json.loads((KIT / 'runtime-pins.json').read_text())
    for name, expected in pins['runtime_files'].items():
        path = ROOT / name
        raw = path.read_bytes()
        mode = '100755' if path.stat().st_mode & stat.S_IXUSR else '100644'
        if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256'] or mode != expected['mode']:
            raise ValueError('runtime file bytes/mode differs: ' + name)
    core = hashlib.sha256()
    for path in sorted((ROOT / 'dams_sim').glob('*.py')):
        core.update(path.name.encode() + b'\0' + path.read_bytes())
    names = ('longitudinal_pipeline.py', 'longitudinal_design.py', 'longitudinal_outputs.py',
             'longitudinal_statistics.py', 'spec.py', 'scheduler.py', 'runtime.py', 'cli.py', 'report.py')
    driver = hashlib.sha256(b''.join(n.encode() + b'\0' + (ROOT / 'dams_sim' / n).read_bytes() for n in names)).hexdigest()
    if core.hexdigest() != pins['core_sha256'] or driver != pins['driver_sha256']:
        raise ValueError('core/driver identity differs')
    fixture = KIT / 'fixtures' / 'legacy_v1_longitudinal_storage.py'
    if hashlib.sha256(fixture.read_bytes()).hexdigest() != pins['legacy_fixture_sha256']:
        raise ValueError('legacy fixture identity differs')
    return {'base_commit': pins['base_commit'], 'runtime_file_count': len(pins['runtime_files']),
            'runtime_bytes_and_modes_exact': True, 'core_sha256': core.hexdigest(), 'driver_sha256': driver}


def observe(label, required=False):
    result = {'runtime_label': label, 'python_version': sys.version.split()[0],
              'python_executable_basename': Path(sys.executable).name, 'platform': sys.platform,
              'sqlite_version': sqlite3.sqlite_version, 'engineering_controls_only': True,
              'GCP_guest_verified': False, 'accepted_scientific_worlds_added': 0,
              'identity': verify_pins()}
    db = sqlite3.connect(':memory:')
    try:
        try:
            result['octet_length_capability'] = db.execute("SELECT octet_length('臺灣'),octet_length(X'00')").fetchone() == (6, 1)
        except sqlite3.OperationalError:
            result['octet_length_capability'] = False
    finally:
        db.close()
    # The runtime supports Python >=3.11; older host system interpreters are
    # observations only and are not relabelled as runtime validation.
    if sys.version_info >= (3, 11):
        sys.path.insert(0, str(ROOT))
        from dams_sim.longitudinal_storage import ExactLedger
        temporary_root = ROOT / 'runs' / 'journal-chunks' / 'capabilities'
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            flat = ExactLedger(Path(temporary) / 'flat')
            try:
                flat.begin(); flat.add_journal(0, 'work', 'capability', {'value': '臺灣'}); flat.commit()
                assert len(list(flat.rows('journal'))) == 1
                result['schema1_actual_control'] = 'PASS'
            finally:
                flat.close()
            try:
                chunk = ExactLedger(Path(temporary) / 'chunk', journal_chunk_bytes=128)
            except ValueError as error:
                if result['octet_length_capability'] or 'octet_length capability' not in str(error):
                    raise
                result['schema2_actual_control'] = 'CAPABILITY_REFUSAL_PASS'
            else:
                try:
                    assert result['octet_length_capability']
                    chunk.begin(); chunk.add_journal(0, 'work', 'capability', {'value': '臺灣'}); chunk.commit()
                    assert len(list(chunk.rows('journal'))) == 1
                    result['schema2_actual_control'] = 'PASS'
                finally:
                    chunk.close()
    else:
        result['schema1_actual_control'] = 'NOT_RUN_UNSUPPORTED_PYTHON'
        result['schema2_actual_control'] = 'NOT_RUN_UNSUPPORTED_PYTHON'
    if required and (sys.version_info[:3] != (3, 14, 2) or not result['octet_length_capability'] or result['schema2_actual_control'] != 'PASS'):
        print(json.dumps(result, sort_keys=True), flush=True)
        raise ValueError('managed Python3.14.2/schema2 capability requirement not satisfied')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--required-managed', action='store_true')
    arguments = parser.parse_args()
    print(json.dumps(observe(arguments.label, arguments.required_managed), sort_keys=True), flush=True)
