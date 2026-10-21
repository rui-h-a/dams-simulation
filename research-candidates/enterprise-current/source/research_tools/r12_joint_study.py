"""Engineering-only staging of existing complete two-firm common checkpoints.

No generation, simulated-day advancement, policy extension, formal inference or
raw-admission authority. Existing R11 studies and their entry points are untouched.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
ARMS = ('no-acquisition', 'preserve-autonomy', 'centralize')
ROSTER_SCHEMA = 'r12-existing-common-pair-roster-1'
PLAN_SCHEMA = 'r12-joint-engineering-stage-plan-1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def bad(_):raise ValueError('nonfinite JSON constant')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)


def regular(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink evidence/output path refused')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('evidence is not a regular file')
    return path, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def observed(path):
    path, before = regular(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024):digest.update(block)
    if regular(path)[1] != before:raise ValueError('evidence changed while hashing')
    return {'bytes': before[2], 'sha256': digest.hexdigest(), 'identity': list(before)}


def read_json(path):
    path, before = regular(path)
    raw = path.read_bytes()
    if len(raw) != before[2] or regular(path)[1] != before:
        raise ValueError('JSON input changed while reading')
    return strict_json(raw), hashlib.sha256(raw).hexdigest()


def write_new(path, value):
    """No overwrite or cleanup, including on interruption during publication."""
    path = Path(path)
    if path.exists() or path.is_symlink():raise ValueError('existing output preserved')
    temporary = path.with_suffix(path.suffix+'.pending')
    data = canonical(value)+b'\n'
    fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data);stream.flush();os.fsync(fd)
        # A hard-link publication cannot replace a concurrently created result.
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:os.close(fd)


def validate_roster(value):
    if not isinstance(value, dict) or set(value) != {'schema', 'worlds'} or value['schema'] != ROSTER_SCHEMA:
        raise ValueError('explicit existing-common-pair roster required')
    if not isinstance(value['worlds'], list) or not value['worlds']:
        raise ValueError('nonempty assigned world roster required')
    worlds, parents = set(), set()
    for item in value['worlds']:
        if not isinstance(item, dict) or set(item) != {'world', 'parent_checkpoint'}:
            raise ValueError('world requires exactly an identity and original checkpoint')
        world, checkpoint = item['world'], item['parent_checkpoint']
        if type(world) is not int or not 1 <= world <= 100000000 or world in worlds:
            raise ValueError('invalid or duplicate assigned world')
        if not isinstance(checkpoint, str) or not Path(checkpoint).is_absolute() or '..' in Path(checkpoint).parts:
            raise ValueError('explicit absolute parent path without traversal required')
        if checkpoint in parents:raise ValueError('one common pair cannot add multiple worlds')
        worlds.add(world);parents.add(checkpoint)
    return copy.deepcopy(value['worlds'])


def shared_header(value, world):
    """Early rejection only; the existing core owns complete raw validation."""
    if (not isinstance(value, dict) or value.get('schema') != 'dams-two-firm-joint-checkpoint-1'
            or value.get('status') != 'unoffered' or value.get('offer') is not None
            or value.get('owners') != {'buyer': 'buyer', 'target': 'seller'}
            or set(value.get('firms', {})) != {'buyer', 'target'}
            or value.get('terms', {}).get('global_world') != world
            or value.get('day') != value.get('terms', {}).get('offer_day')):
        raise ValueError('original unoffered complete common pair required; no R11 arm/capsule')
    for firm in value['firms'].values():
        if firm.get('config', {}).get('enterprise_acquisition') is not None:
            raise ValueError('legacy acquisition capsule cannot enter joint staging')


def source_manifest():
    # Reuse the complete core/design/execution dependency inventory, then bind
    # this new adapter itself; do not equate a core digest with all execution.
    from research_tools.r11_retention import science_manifest
    return {'base_dependencies': science_manifest(ROOT),
            'adapter_sha256': observed(Path(__file__))['sha256']}


def _fresh_directory(path):
    if '..' in Path(path).parts:
        raise ValueError('output path traversal refused before reading any parent')
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or path.exists():
        raise ValueError('fresh nonsymlink output directory required; existing output preserved')
    return path


def _parent_binding(path, world):
    from dams_sim.joint_counterfactual import CounterfactualPlan
    header, header_sha = read_json(path);shared_header(header, world)
    plan = CounterfactualPlan.from_checkpoint(path)
    if plan.payload['parent_files'][Path(path).name]['sha256'] != header_sha:
        raise ValueError('parent header changed between original read and native plan binding')
    pins = {}
    for name, expected in plan.payload['parent_files'].items():
        actual = observed(Path(path).parent/name)
        if any(actual[k] != expected[k] for k in ('bytes', 'sha256')):
            raise ValueError('complete parent raw group differs from native plan')
        pins[name] = actual
    return {'world': world, 'parent_checkpoint': str(Path(path).absolute()),
            'counterfactual_plan': plan.payload, 'plan_sha256': plan.sha256,
            'firm_configs': {c: header['firms'][c]['config'] for c in ('buyer', 'target')},
            'history_terms': header['terms'], 'parent_files': pins}


def _recheck_parent(item):
    parent = Path(item['parent_checkpoint'])
    for name, pin in item['parent_files'].items():
        if observed(parent.parent/name) != pin:
            raise ValueError('original complete parent bytes/identity changed; no relabelling')


def prepare(roster_path, output):
    output = _fresh_directory(output)
    request, roster_sha = read_json(roster_path)
    assignments = validate_roster(request)
    if any(output.is_relative_to(Path(v['parent_checkpoint']).parent)
           or Path(v['parent_checkpoint']).is_relative_to(output) for v in assignments):
        raise ValueError('study output must be disjoint from all original parent groups')
    before = source_manifest()
    items = [_parent_binding(v['parent_checkpoint'], v['world']) for v in assignments]
    for item in items:_recheck_parent(item)
    if source_manifest() != before:raise ValueError('source changed during parent binding')
    plan = {'schema': PLAN_SCHEMA, 'study_directory': str(output), 'roster_sha256': roster_sha,
            'assigned_worlds': [v['world'] for v in assignments], 'arms': list(ARMS),
            'source_manifest': before, 'worlds': items, 'engineering_only': True,
            'raw_admitted': False, 'formal_worlds_added': 0, 'inference_authorized': False}
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_new(output/'plan.json', plan)
    return {'status': 'EXISTING_COMMON_PAIRS_BOUND_FOR_ENGINEERING_STAGING',
            'plan_sha256': observed(output/'plan.json')['sha256'], 'raw_admitted': False,
            'formal_worlds_added': 0, 'inference_authorized': False}


def admitted_plan(study, expected_sha, world, arm):
    if '..' in Path(study).parts:
        raise ValueError('study path traversal refused before reading the plan')
    study = Path(study).absolute()
    value, actual = read_json(study/'plan.json')
    if actual != expected_sha:raise ValueError('externally fixed complete plan SHA differs')
    if (value.get('schema') != PLAN_SCHEMA or value.get('study_directory') != str(study)
            or value.get('arms') != list(ARMS) or value.get('engineering_only') is not True
            or value.get('raw_admitted') is not False or value.get('inference_authorized') is not False
            or type(value.get('formal_worlds_added')) is not int or value['formal_worlds_added'] != 0):
        raise ValueError('engineering-only adapter plan required')
    if type(world) is not int or arm not in ARMS:raise ValueError('invalid assigned action')
    rows = validate_roster({'schema': ROSTER_SCHEMA, 'worlds': [
        {'world': row['world'], 'parent_checkpoint': row['parent_checkpoint']} for row in value['worlds']]})
    if value['assigned_worlds'] != [v['world'] for v in rows] or world not in value['assigned_worlds']:
        raise ValueError('world outside the fixed original roster')
    for output in (study, study/f'world-{world}'/arm):
        if any(output.is_relative_to(Path(v['parent_checkpoint']).parent)
               or Path(v['parent_checkpoint']).is_relative_to(output) for v in rows):
            raise ValueError('study and branch outputs must be disjoint from every original parent group')
    item = next(v for v in value['worlds'] if v['world'] == world)
    return value, item


def stage(study, expected_sha, world, arm):
    study = Path(study).absolute()
    # Refuse altered/foreign manifests before any Model import or output write.
    plan, item = admitted_plan(study, expected_sha, world, arm)
    if source_manifest() != plan['source_manifest']:raise ValueError('complete execution source changed')
    destination = _fresh_directory(study/f'world-{world}'/arm)
    lock_fd = os.open(study/'stage.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Recheck after taking the single-writer lock; no implicit continuation.
        admitted_plan(study, expected_sha, world, arm);_fresh_directory(destination)
        _recheck_parent(item)
        bound = _parent_binding(item['parent_checkpoint'], world)
        if bound != item:raise ValueError('original terms/config/lineage changed')
        from dams_sim.joint_counterfactual import CounterfactualPlan
        from dams_sim.joint_firm import JointRun
        fixed = CounterfactualPlan(canonical(item['counterfactual_plan']).decode())
        if fixed.sha256 != item['plan_sha256']:raise ValueError('counterfactual plan binding differs')
        run = JointRun.fork_counterfactual(item['parent_checkpoint'], destination, plan=fixed, arm=arm)
        try:
            if run.day != fixed.payload['parent_day']:raise ValueError('staging advanced the original day')
            checkpoint = destination/'staged-common.json'
            run.snapshot(checkpoint)  # existing full joint/native raw verifier
        finally:run.close()
        _recheck_parent(item)
        if source_manifest() != plan['source_manifest']:raise ValueError('source changed during staging')
        receipt = {'status': 'ENGINEERING_BRANCH_STAGED_NOT_RAW_ADMITTED', 'world': world, 'arm': arm,
                   'study_plan_sha256': expected_sha, 'counterfactual_plan_sha256': fixed.sha256,
                   'staged_day': fixed.payload['parent_day'], 'simulated_days_added': 0,
                   'checkpoint': checkpoint.name, 'checkpoint_pin': observed(checkpoint),
                   'raw_admitted': False, 'formal_worlds_added': 0, 'inference_authorized': False,
                   'remaining_validation': ['complete branch group recovery', 'full-horizon per-firm daily raw validation',
                       'independent scalar accounts', 'whole-roster preservation and external validity']}
        write_new(destination/'stage.json', receipt)
        return receipt
    finally:os.close(lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare');p.add_argument('--roster', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    s = commands.add_parser('stage');s.add_argument('--study', required=True, type=Path)
    s.add_argument('--plan-sha256', required=True);s.add_argument('--world', required=True, type=int)
    s.add_argument('--arm', required=True, choices=ARMS)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.roster, args.output) if args.command == 'prepare' else stage(
            args.study, args.plan_sha256, args.world, args.arm)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'{type(error).__name__}: {error}; existing evidence retained', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2));return 0


if __name__ == '__main__':raise SystemExit(main())
