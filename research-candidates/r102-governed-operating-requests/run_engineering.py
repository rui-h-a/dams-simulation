"""Bounded, local R102 engineering only; no paid actions or formal sample claim.

Imports the model only after exact source/profile/entry preflight. Resource
checks are cooperative at model and preservation boundaries, not an OS sandbox.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import resource
import platform
import sqlite3
import stat
import sys
import time
import traceback

sys.dont_write_bytecode = True
BASE = Path(__file__).absolute().parent
SOURCE_SHA = 'aaaea8b83c21d18d3a8633188254ecb05d92d8e72bb680203418062f15d30cbc'
PROFILE_SHA = '21365d199e943a9869d5cab360ee37b3e4926f99e37972aa17616ceb2fcd0608'
MIN_FREE = 80 * 1024**3
OUTPUT_CAP = 2 * 1024**3
RSS_CAP = 1024**3
LIFETIME = 1800


class Refused(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def native(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink)


def no_alias(path):
    path = Path(path).absolute()
    require('..' not in path.parts, 'path parent traversal')
    for part in reversed((path, *path.parents)):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            require(not stat.S_ISLNK(info.st_mode), 'path ancestor alias')
    return path


def read_regular(path):
    path = no_alias(path)
    before = path.lstat()
    require(before.st_size <= 4 * 1024**2, 'publication input exceeds bounded read')
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
            'regular single-link file required')
    with path.open('rb') as stream:
        require(native(os.fstat(stream.fileno())) == native(before), 'opened file changed')
        raw = stream.read()
    require(native(path.lstat()) == native(before) and len(raw) == before.st_size,
            'file changed while reading')
    return raw, native(before)


def file_sha(path):
    before = no_alias(path).lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'output file type/link')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        require(native(os.fstat(stream.fileno())) == native(before), 'opened output changed')
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    require(native(path.lstat()) == native(before), 'output changed during hashing')
    return digest.hexdigest(), native(before)


def peak_rss_bytes(platform=None, measured=None):
    platform = sys.platform if platform is None else platform
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if measured is None else measured
    require(platform == 'darwin' or platform.startswith('linux'), 'unsupported POSIX RSS units')
    return int(value if platform == 'darwin' else value * 1024)


def scan_tree(root, hashes=False):
    root = no_alias(root)
    require(stat.S_ISDIR(root.lstat().st_mode), 'output root directory required')
    rows = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), 'tree alias')
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'tree special/hardlinked file')
        row = {'bytes': info.st_size, 'native': list(native(info))}
        if hashes:
            row['sha256'], identity = file_sha(path)
            require(list(identity) == row['native'], 'tree inventory changed')
        rows[str(path.relative_to(root))] = row
    return rows


def load_inputs(base=BASE):
    base = no_alias(base)
    manifest_raw, manifest_native = read_regular(base / 'publication-manifest.json')
    manifest = json.loads(manifest_raw)
    require(manifest['source_sha256'] == SOURCE_SHA, 'source declaration differs')
    files = manifest['files']
    require(isinstance(files, dict) and 'run_engineering.py' in files,
            'entry absent from publication roster')
    pins = {base / 'publication-manifest.json': (manifest_native, sha(manifest_raw))}
    for name, pin in files.items():
        part = Path(name)
        require(not part.is_absolute() and part.parts and '..' not in part.parts,
                'unsafe manifest path')
        path = base / part
        raw, identity = read_regular(path)
        require(len(raw) == pin['bytes'] and sha(raw) == pin['sha256'],
                'publication file SHA/size differs: ' + name)
        pins[path] = (identity, sha(raw))
    declared = {name for name in files if name.startswith('source/')}
    actual = {'source/' + name for name in scan_tree(base / 'source')}
    require(actual == declared, 'complete source roster differs')
    core = sorted((base / 'source/dams_sim').glob('*.py'))
    require(len(core) == 35 and sha(b''.join(p.name.encode() + b'\0' + p.read_bytes()
                                          for p in core)) == SOURCE_SHA,
            '35-module model source differs')
    raw, _ = read_regular(base / 'profiles/engineering-pair.json')
    require(sha(raw) == PROFILE_SHA, 'fixed engineering profile differs')
    profile = json.loads(raw)
    require([a['name'] for a in profile['arms']] == ['hierarchy', 'dams'], 'paired arm roster')
    require(sum(a[k]['n'] for a in profile['arms']
                for k in ('buyer_config', 'target_config')) == 8, 'fresh-person bound')
    for arm in profile['arms']:
        for name in ('buyer_config', 'target_config'):
            c = arm[name]
            require(c['n'] == 2 and c['days'] == 1947 and c['max_wall_seconds'] == 600.0,
                    'original engineering configuration differs')
    return profile, pins, sha(manifest_raw)


def fresh_output(path, base=BASE):
    path = no_alias(path)
    base = no_alias(base)
    require(not path.is_relative_to(base) and not base.is_relative_to(path),
            'output must be separate from the immutable candidate')
    require(path.parent.is_dir(), 'output parent must already exist')
    require(not path.exists(), 'fresh output required; existing paths refused')
    free = os.statvfs(path.parent)
    require(free.f_bavail * free.f_frsize >= MIN_FREE, 'physical free-space watermark')
    path.mkdir(mode=0o700, exist_ok=False)
    return path


class Guard:
    def __init__(self, out, base, pins, start=None):
        self.out, self.base, self.pins = out, base, pins
        self.start = time.monotonic() if start is None else start
        self.deadline = self.start + LIFETIME
        self.root_identity = (out.lstat().st_dev, out.lstat().st_ino)
        self.source_names = set(scan_tree(base / 'source'))
        self.first_fault = None
        self.check(full=True)

    def check(self, full=False):
        try:
            require(time.monotonic() < self.deadline, 'engineering lifetime reached')
            no_alias(self.out)
            info = self.out.lstat()
            require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == self.root_identity,
                    'output namespace identity changed')
            free = os.statvfs(self.out)
            require(free.f_bavail * free.f_frsize >= MIN_FREE, 'physical free-space watermark')
            rows = scan_tree(self.out)
            require(sum(r['bytes'] for r in rows.values()) <= OUTPUT_CAP, 'whole-output capacity')
            require(peak_rss_bytes() <= RSS_CAP, 'process peak RSS bound')
            for path, (identity, digest) in self.pins.items():
                require(native(no_alias(path).lstat()) == identity, 'frozen input native identity changed')
                if full:
                    raw, checked = read_regular(path)
                    require(checked == identity and sha(raw) == digest, 'frozen input SHA changed')
            if full:
                require(set(scan_tree(self.base / 'source')) == self.source_names,
                        'complete source namespace changed')
            require(time.monotonic() < self.deadline, 'engineering lifetime reached after guard')
        except BaseException as error:
            if self.first_fault is None:
                self.first_fault = {'type': type(error).__name__, 'message': str(error)}
            raise
        return None

    def stop_requested(self):
        self.check()
        return False


def save(path, value):
    raw = encoded(value)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    require(path.read_bytes() == raw, 'receipt full-byte readback differs')


def guarded_save(guard, path, value):
    guard.check(full=True)
    save(path, value)
    guard.check(full=True)


def trace(run):
    result = {}
    for name, model in run._all().items():
        counts, rows = Counter(), []
        for _, day, kind, event, payload in model._long.ledger.rows('journal'):
            if not kind.startswith('governed_'):
                continue
            counts[kind] += 1
            if kind in ('governed_proposals', 'governed_outcomes', 'governed_consumed'):
                rows.append({'day': day, 'event': event, 'kind': kind, 'value': json.loads(payload)})
        result[name] = {'counts': dict(counts), 'causal_rows': rows}
    return result


def environment_preflight():
    require(os.environ.get('DAMS_PHASE_STORAGE_CONFIG') is None,
            'inherited phase storage would change the original checkpoint/output scope')
    for key in ('DAMS_PACKAGED_COMMIT', 'DAMS_SOURCE_MANIFEST', 'DAMS_TRANSFER_CONFIG'):
        os.environ.pop(key, None)


def execute(mode, out, profile, guard, manifest_sha):
    # These imports are deliberately after the complete pinned preflight.
    sys.path.insert(0, str(guard.base / 'source'))
    from dams_sim.config import Config
    from dams_sim.storage import source_hash
    from dams_sim.joint_firm import JointRun, JointTerms, run_joint_world
    require(source_hash() == SOURCE_SHA, 'loaded source differs')
    results = []
    for arm in profile['arms']:
        guard.check(full=True)
        buyer, target = (Config.from_dict(arm[key]) for key in ('buyer_config', 'target_config'))
        terms = JointTerms(**arm['joint_terms'])
        root = out / arm['name']
        if mode == 'daily':
            run = JointRun(buyer, target, root, terms)
            restored = None
            try:
                while run.day < 90:
                    guard.check()
                    run.step()
                guard.check(full=True)
                checkpoint = root / 'engineering-prefix-day-000090.json'
                run.snapshot(checkpoint)
                guard.check(full=True)
                restored = JointRun.restore(checkpoint, out / (arm['name'] + '-restored'))
                require(run.day == restored.day == 90, 'restored prefix day differs')
                require([m.semantic_digest() for m in run._all().values()] ==
                        [m.semantic_digest() for m in restored._all().values()], 'restored prefix state differs')
                for _ in range(10):
                    guard.check()
                    run.step()
                    guard.check()
                    restored.step()
                    require(run.records == restored.records, 'restore tail records differ')
                    require([m.semantic_digest() for m in run._all().values()] ==
                            [m.semantic_digest() for m in restored._all().values()], 'restore tail state differs')
                run.validate_joint_metadata()
                restored.validate_joint_metadata()
                evidence = {'arm': arm['name'], 'completed_day': run.day, 'checkpoint_day': 90,
                            'actual_restore_and_10_day_differential': True,
                            'firm_summaries': {name: m.summary() for name, m in run._all().items()},
                            'governed_trace': trace(run)}
            finally:
                run.close()
                if restored is not None:
                    restored.close()
        else:
            evidence = run_joint_world(buyer, target, root, terms,
                **arm['existing_run_joint_world_options'],
                stop_requested=guard.stop_requested, preservation_guard=guard.check)
            guard.check(full=True)
            restored = JointRun.restore(root / 'joint-day-001947.json', out / (arm['name'] + '-final-restored'))
            try:
                restored.validate_joint_metadata()
                require(restored.day == 1947, 'full calendar not completed')
                summaries = {name: m.summary() for name, m in restored._all().items()}
                require(encoded(summaries) == encoded(evidence['firm_summaries']), 'final summaries differ')
                evidence = {'arm': arm['name'], 'result': evidence,
                            'final_native_journal_restore': True, 'governed_trace': trace(restored)}
            finally:
                restored.close()
        guarded_save(guard, out / (arm['name'] + '-engineering-verification.json'), evidence)
        results.append(arm['name'])
    guard.check(full=True)
    rows = scan_tree(out, hashes=True)
    guard.check(full=True)
    receipt = {'schema': 'DAMS-R102-local-engineering-receipt-1', 'mode': mode,
               'source_sha256': SOURCE_SHA, 'profile_sha256': PROFILE_SHA,
               'publication_manifest_sha256': manifest_sha,
               'input_pins': {str(p.relative_to(guard.base)): {'sha256': d, 'native': list(n)}
                              for p, (n, d) in guard.pins.items()},
               'raw_roster_excludes_this_receipt': rows,
               'all_input_SHA_and_native_rechecked': True,
               'receipt_requires_successful_process_exit_and_no_failure_sidecar': True,
               'host': {'python': sys.version, 'platform': platform.platform(),
                        'machine': platform.machine(), 'sqlite': sqlite3.sqlite_version},
               'elapsed_seconds': time.monotonic() - guard.start,
               'peak_rss_bytes': peak_rss_bytes(), 'both_arms_verified': results == ['hierarchy', 'dams'],
               'formal_samples_added': 0, 'calibration_OOS': False, 'provider_calls': 0}
    require(source_hash() == SOURCE_SHA, 'post-run model source changed')
    pending = out / 'engineering-receipt.pending.json'
    guarded_save(guard, pending, receipt)
    pending.rename(out / 'engineering-receipt.json')
    guard.check(full=True)
    return {'status': 'LOCAL_ENGINEERING_VERIFIED', 'output': str(out), 'mode': mode,
            'both_arms_verified': True, 'formal_samples_added': 0, 'calibration_OOS': False}


def failure_document(error, first_guard_fault):
    diagnostic = {'schema': 'DAMS-R102-local-engineering-failure-1',
                  'type': type(error).__name__, 'message': str(error),
                  'full_error_chain': ''.join(traceback.format_exception(error)),
                  'first_guard_fault': first_guard_fault,
                  'source_sha256': SOURCE_SHA, 'formal_samples_added': 0}
    require(len(encoded(diagnostic)) <= 64 * 1024,
            'full failure diagnostic exceeds 64 KiB; retain complete stderr')
    return diagnostic


def main():
    start = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('daily', 'full'), required=True)
    parser.add_argument('--output', type=Path, required=True, help='nonexistent directory; existing parent outside candidate')
    args = parser.parse_args()
    peak_rss_bytes()  # Fail closed on unmeasured unit conventions before creating output.
    environment_preflight()
    profile, pins, manifest_sha = load_inputs()
    out = fresh_output(args.output)
    guard = None
    try:
        guard = Guard(out, BASE, pins, start=start)
        guarded_save(guard, out / 'engineering-contract.json', {
            'schema': 'DAMS-R102-local-engineering-contract-1', 'mode': args.mode,
            'source_sha256': SOURCE_SHA, 'profile_sha256': PROFILE_SHA,
            'fresh_constructor_people_total_upper': 8, 'recovery_copies_are_not_new_worlds': True,
            'output_namespace_bytes_cap': OUTPUT_CAP, 'physical_min_free_bytes': MIN_FREE,
            'process_peak_rss_upper_bytes': RSS_CAP, 'cooperative_lifetime_seconds': LIFETIME,
            'resource_only_change': {'max_wall_seconds': {'old': 120.0, 'new': 600.0}},
            'formal_samples_added': 0, 'calibration_OOS': False, 'provider_calls': 0})
        guarded_save(guard, out / 'effective-profile.json', profile)
        outcome = execute(args.mode, out, profile, guard, manifest_sha)
        guard.check(full=True)
        print(json.dumps(outcome, sort_keys=True))
    except BaseException as error:
        # This bounded diagnostic is local preservation, never a success receipt.
        # Existing raw/pending files are retained and no denied Model write is retried.
        try:
            no_alias(out)
            require(guard is None or (out.lstat().st_dev, out.lstat().st_ino) == guard.root_identity,
                    'failure namespace identity changed')
            diagnostic = failure_document(error, guard.first_fault if guard else None)
            raw = encoded(diagnostic)
            require(sum(r['bytes'] for r in scan_tree(out).values()) + len(raw) <= OUTPUT_CAP,
                    'no output capacity for failure diagnostic')
            save(out / 'engineering-failure.json', diagnostic)
        except BaseException as preservation_error:
            print('failure diagnostic refused: ' + str(preservation_error), file=sys.stderr)
        raise


if __name__ == '__main__':
    main()
