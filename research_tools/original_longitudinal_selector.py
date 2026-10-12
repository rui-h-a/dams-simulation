"""One original, ordered N1000 assignment; no new scientific specification.

The original Scheduler/worker and raw reader remain unchanged. A truthful GCP
result is NOT currently importable by the old pipeline's macOS provenance
expectation. Execution retains this explicit RETURN_IMPORT_BLOCKED condition;
this module never installs results or increments the original census.
Root alone supplies the handoff/lease and performs any later return admission.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from research_tools.frozen_science_cloud import CORE, DRIVER, ENTRY, canonical, digest, require

CID = '04954c96af41440c6fa8a4b4759b46049ffd285678d07dfbd9b1243a187530f6'
PARENT = 'a2fedfd7c76e1ca1f7e75bf8625b783d7cf3642f7a04673de8eeb3d2284fc79e'
CONFIG = 'ee9941d29662f609f4f84e8767f9beb83edd339e45a90906b93f974647bca77f'
SPEC = '40eb532978a81a9aa0e7ee82acf7ae477622fbe7f84b2a2d8ef00d5a4107353e'
INVENTORY = '3c971b2650311612146eaf8d7fac5fc2064a1e707c8c286a3516a0a094d48706'
STATUSES = '252c86531307ff9a7d0c741f24904cb27e9b91ccb0ad6d76511e5f87fed33d89'
RUNTIME = '5bb350b3400482da8a9e5f27eb6eac5b75a673f25b36b7d68219b5e9e8bb2c8c'
RUNTIME_VALUE = '6fa03b49b467a0b85408196678c18e490a1d2ffd9cbf4ece097cf4adc56025c3'
PARENT_MANIFEST = '2f89a869ec0e04a6a13969a93501b40c447b60120696886f8ad094765f41a0cb'
PARENT_INDEX = 'de8a6b7300d2f2d08722a67c4caf578a4bc0a8ac8553ea18f48435081725457c'
COMMIT = 'ed2279a9957dc6849f34a57014fc377b1339f46f'
SCHEMA = 'DAMS-original-longitudinal-selected-1'
BOUND = 4 * 1024**2
INPUTS = {'original-inventory.json': INVENTORY, 'original-statuses.json': STATUSES,
          'original-runtime-limits.json': RUNTIME, 'selected-parent/manifest.json': PARENT_MANIFEST,
          'selected-parent/checkpoint-index.json': PARENT_INDEX}
ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_PIPELINE_LOCK_PATH_SHA256 = '2b411f70136f4b73ce824d7acb19522d0a42598f093eaa2445b292891c1f20d6'


def native(s):
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink]


def safe_path(path):
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        s = current.lstat()
        require(not stat.S_ISLNK(s.st_mode), 'selected input has symlink')
        if current != path:
            require(stat.S_ISDIR(s.st_mode), 'selected input ancestor is not directory')
    return path


def read_pin(path, expected, *, bound=BOUND):
    """Anchored parent, nonblocking regular leaf, full EOF and native fence."""
    path = safe_path(path)
    pfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent = os.fstat(pfd)
    fd = None
    try:
        before = path.lstat()
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
        held = os.fstat(fd)
        require(stat.S_ISREG(held.st_mode) and held.st_nlink == 1
                and 0 < held.st_size <= bound and native(held) == native(before),
                'selected input is not bounded stable regular file')
        raw = bytearray()
        while True:
            block = os.read(fd, min(65536, bound + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            require(len(raw) <= bound, 'selected input exceeds bound')
        require(native(held) == native(os.fstat(fd)) == native(path.lstat())
                and len(raw) == held.st_size, 'selected input changed')
        safe_path(path)
        require((parent.st_dev, parent.st_ino) == (path.parent.lstat().st_dev, path.parent.lstat().st_ino),
                'selected parent namespace changed')
        require(digest(raw) == expected, 'selected input full SHA differs')
        return bytes(raw), native(held)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(pfd)


def parse(raw):
    def pairs(items):
        d = {}
        for k, v in items:
            require(k not in d, 'duplicate selected metadata key')
            d[k] = v
        return d
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, 'nonfinite selected metadata'))


def first_ready(inventory, statuses):
    """Same sorted case-key ready set as original execute_stage/Scheduler.run."""
    from dams_sim.config import Config
    from dams_sim.spec import case_key
    require(isinstance(inventory, list) and len(inventory) == 144, 'original inventory must contain 144 cases')
    by_id = {r['case_id']: r for r in inventory}
    require(len(by_id) == 144 and all(case_key(Config.from_dict(r['config'])) == r['case_id']
                                    for r in inventory), 'original case-key or uniqueness differs')
    require(isinstance(statuses, list) and all(r['case_id'] in by_id for r in statuses), 'unknown original case status')
    complete = {r['case_id'] for r in statuses if r['status'] == 'complete'}
    require(len(complete) == 35, 'dated original complete set is not 35')
    ready = sorted(k for k, r in by_id.items() if k not in complete
                   and (r['tags']['parent_case_id'] is None or r['tags']['parent_case_id'] in complete))
    require(ready and ready[0] == CID and not any(r['case_id'] == CID for r in statuses),
            'next original ordered case differs or was already attempted')
    row = by_id[CID]
    require(digest(canonical(row['config'])) == CONFIG
            and row['config']['n'] == 1000 and row['config']['days'] == 3743
            and row['config']['world'] == 20003 and row['config']['seed'] == 20261008
            and row['tags']['parent_case_id'] == PARENT and row['tags']['branch_day'] == 366,
            'selected original scientific identity differs')
    return row


def check_science():
    from dams_sim.storage import source_hash
    from dams_sim.longitudinal_pipeline import driver_hash
    require(source_hash() == CORE and driver_hash() == DRIVER
            and digest(read_pin(ROOT / 'run.sh', ENTRY, bound=65536)[0]) == ENTRY,
            'original core/driver/entry differs')


def prepare(work, assignment):
    require(isinstance(assignment, dict) and set(assignment) == {
        'schema', 'case_id', 'config_sha256', 'source_sha256', 'driver_sha256', 'spec_sha256',
        'input_sha256', 'parent_files', 'root_handoff_sha256'}
        and assignment['schema'] == SCHEMA, 'invalid selected assignment schema')
    require((assignment['case_id'], assignment['config_sha256'], assignment['source_sha256'],
             assignment['driver_sha256'], assignment['spec_sha256']) == (CID, CONFIG, CORE, DRIVER, SPEC)
            and assignment['input_sha256'] == INPUTS, 'selected assignment overrides frozen input')
    loaded = {}; fences = {}
    for name, expected in INPUTS.items():
        raw, fences[name] = read_pin(Path(work) / name, expected)
        loaded[name] = parse(raw)
    row = first_ready(loaded['original-inventory.json'], loaded['original-statuses.json'])
    m = loaded['selected-parent/manifest.json']; desc = m['final_state_descriptor']
    from dams_sim.spec import case_key
    from dams_sim.config import Config
    require(m['status'] == 'complete' and type(m['exit_code']) is int and m['exit_code'] == 0
            and m['source_sha256'] == CORE and m['pipeline_driver_sha256'] == DRIVER
            and m['scientific_case_id'] == PARENT and case_key(Config.from_dict(m['config'])) == PARENT
            and desc['day'] == 366 and desc['source_sha256'] == CORE
            and desc['config_sha256'] == m['config_sha256'] == digest(canonical(m['config']))
            and desc['file'] == 'final_state.json', 'original full366 parent identity differs')
    index = loaded['selected-parent/checkpoint-index.json']
    require(index['snapshots'][0]['day'] == 366, 'latest parent checkpoint is not original366')
    roster = assignment['parent_files']
    require(isinstance(roster, dict) and len(roster) == 13 and set(roster) == set(m['output_sha256']) | {'manifest.json'},
            'parent full13 roster missing/extra')
    for name, pin in roster.items():
        require('/' not in name and name not in ('', '.', '..') and set(pin) == {'sha256', 'bytes'}
                and re.fullmatch('[a-f0-9]{64}', pin['sha256']) and type(pin['bytes']) is int
                and 0 < pin['bytes'] <= 2**31, 'unsafe parent file pin')
        require(pin['sha256'] == (PARENT_MANIFEST if name == 'manifest.json' else m['output_sha256'][name]),
                'parent file SHA differs from original manifest')
    origin = {'parent_checkpoint': str(Path(work) / 'selected-parent' / desc['file']),
              'parent_case_id': PARENT, 'parent_source_sha256': CORE,
              'parent_config_sha256': m['config_sha256'], 'parent_day': 366,
              'parent_state_semantic_sha256': desc['state_semantic_sha256']}
    identity = {'case_id': CID, 'config_sha256': CONFIG, 'source_sha256': CORE,
                'driver_sha256': DRIVER, 'spec_sha256': SPEC,
                'branch_identity': {k: v for k, v in origin.items() if k != 'parent_checkpoint'},
                'original_runtime_limits_sha256': RUNTIME, 'input_sha256': INPUTS}
    return {'row': row, 'original_limits': loaded['original-runtime-limits.json'], 'origin': origin,
            'input_native': fences, 'selection_identity_sha256': digest(canonical(identity)),
            'return_import_status': 'RETURN_IMPORT_BLOCKED',
            'return_reason': 'unchanged original Scheduler._inspect expects legacy macOS environment',
            'original_scientific_case_count_increment': 0}


def validate_root_handoff(h, prepared, *, now=None):
    """Root-sealed external ownership evidence; this function is not an issuer.

    The local coordinator must hold the native .pipeline.lock continuously until
    remote closure/return. The guest additionally depends on the existing live
    controller lease; a historical JSON assertion cannot prove a live OS lock.
    """
    from dams_sim.runtime import parse_deadline
    keys = {'schema', 'status', 'selection_identity_sha256', 'original_runtime_limits_sha256',
            'source_sha256', 'driver_sha256', 'pipeline_lock', 'writer_absence_receipt_sha256',
            'parent_full_raw_gate_sha256', 'deadline_utc', 'observed_utc', 'lease_timeout_seconds',
            'selected_case_unstarted', 'return_import_status'}
    require(isinstance(h, dict) and set(h) == keys and h['schema'] == 'DAMS-original-selected-handoff-1'
            and h['status'] == 'ROOT_EXCLUSIVE_ORIGINAL_PIPELINE_HANDOFF', 'root exclusive handoff missing')
    require(h['selection_identity_sha256'] == prepared['selection_identity_sha256']
            and h['original_runtime_limits_sha256'] == RUNTIME
            and (h['source_sha256'], h['driver_sha256']) == (CORE, DRIVER)
            and h['selected_case_unstarted'] is True
            and h['return_import_status'] == 'RETURN_IMPORT_BLOCKED', 'handoff assignment or honest return boundary differs')
    lock = h['pipeline_lock']
    require(isinstance(lock, dict) and set(lock) == {'path', 'dev', 'ino', 'owner_pid', 'owner_birth', 'exclusive_held'}
            and isinstance(lock['path'], str)
            and digest(lock['path'].encode()) == ORIGINAL_PIPELINE_LOCK_PATH_SHA256
            and all(type(lock[k]) is int and lock[k] > 0 for k in ('dev', 'ino', 'owner_pid'))
            and isinstance(lock['owner_birth'], str) and lock['owner_birth']
            and lock['exclusive_held'] is True, 'native pipeline lock ownership is incomplete')
    for k in ('writer_absence_receipt_sha256', 'parent_full_raw_gate_sha256'):
        require(isinstance(h[k], str) and re.fullmatch('[a-f0-9]{64}', h[k]), 'root raw/absence gate pin missing')
    require(h['deadline_utc'] == prepared['original_limits']['deadline_utc']
            and type(h['lease_timeout_seconds']) is int and 1 <= h['lease_timeout_seconds'] <= 300,
            'root handoff deadline or lease differs')
    now = time.time() if now is None else now
    require(parse_deadline(h['observed_utc']) <= now + 5
            and now - parse_deadline(h['observed_utc']) <= h['lease_timeout_seconds']
            and now < parse_deadline(h['deadline_utc']), 'root handoff lease expired/future or deadline elapsed')
    return h


def validate_handoff_evaluation(evaluation,handoff,prepared,assignment_sha,handoff_sha):
    """Read a Root-issued historical observation without pretending it is live."""
    from dams_sim.runtime import parse_deadline
    fields={'schema','evaluated_utc','original_root_handoff_sha256','selected_assignment_sha256',
            'case_id','source_sha256','driver_sha256','original_runtime_limits_sha256','result'}
    require(isinstance(evaluation,dict) and set(evaluation)==fields
            and evaluation['schema']=='DAMS-root-original-before-start-handoff-evaluation-1'
            and evaluation['result']=='ROOT_BEFORE_START_ORIGINAL_HANDOFF_VALIDATED'
            and (evaluation['original_root_handoff_sha256'],evaluation['selected_assignment_sha256'],
                 evaluation['case_id'],evaluation['source_sha256'],evaluation['driver_sha256'],
                 evaluation['original_runtime_limits_sha256'])
                ==(handoff_sha,assignment_sha,CID,CORE,DRIVER,RUNTIME),
            'Root historical handoff evaluation differs from immutable selection')
    evaluated=parse_deadline(evaluation['evaluated_utc'])
    require(parse_deadline(handoff['observed_utc'])<=evaluated<=time.time()+5,
            'handoff evaluation precedes observation or invents future assessment')
    validate_root_handoff(handoff,prepared,now=evaluated)
    return evaluated

def validate_activation_handoff(current,original,prepared,*,now=None):
    """H1 is a fresh same-scope observation; it is never a new paid grant."""
    from dams_sim.runtime import parse_deadline
    changed={'observed_utc','writer_absence_receipt_sha256'}
    require(isinstance(current,dict) and set(current)==set(original)
            and canonical({k:v for k,v in current.items() if k not in changed})
                ==canonical({k:v for k,v in original.items() if k not in changed})
            and parse_deadline(current['observed_utc'])>parse_deadline(original['observed_utc'])
            and current['writer_absence_receipt_sha256']!=original['writer_absence_receipt_sha256'],
            'activation observation changes immutable selection/lock/deadline or reuses H0')
    return validate_root_handoff(current,prepared,now=now)

def local_lock_preflight(lock, *, owner_matches, writers_absent):
    """Root-side read-only gate, not an issuer and not a persistent lock holder.

    Root has already acquired the ORIGINAL pipeline lock. The supplied native
    owner/birth and limited writer predicates must be verified by Root against
    its actual process observations; lock contention alone identifies no owner.
    An unoccupied lock is immediately released and rejected. Nothing is written.
    """
    import fcntl
    require(digest(lock['path'].encode()) == ORIGINAL_PIPELINE_LOCK_PATH_SHA256,
            'other output lock cannot authorize the original assignment')
    require(callable(owner_matches) and callable(writers_absent), 'fresh root process predicates missing')
    path = safe_path(lock['path'])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1
                and (s.st_dev, s.st_ino) == (lock['dev'], lock['ino']), 'original lock native identity differs')
        occupied = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            occupied = True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        require(occupied and owner_matches(lock['owner_pid'], lock['owner_birth'], path) is True
                and writers_absent(CID, PARENT) is True, 'root lock owner or original writer absence is unproved')
        require(native(s) == native(os.fstat(fd)) == native(path.lstat()), 'original lock changed during root preflight')
        return {'exclusive_lock_occupied': True, 'root_owner_and_writer_predicates_verified': True,
                'authority_issued': False, 'lock_native': native(s)}
    finally:
        os.close(fd)


def truthful_limits(original, verification, operational_commit):
    require(isinstance(verification, dict) and all(isinstance(verification.get(k), str) and verification[k]
            for k in ('instance_id', 'zone', 'machine_type'))
            and re.fullmatch('[a-f0-9]{40}', operational_commit), 'real guest provenance missing')
    result = copy.deepcopy(original)
    require(original['provenance'] == {'environment': 'local-macos-formal-longitudinal', 'packaged_commit': COMMIT},
            'legacy runtime provenance differs')
    # Exact original RuntimeLimits provenance whitelist. Operational release,
    # provider hardware facts and retained-runtime pin belong in the separately
    # bound execution record, not unsupported fields passed into frozen core.
    result['provenance'] = {k: verification[k] for k in ('instance_id', 'zone', 'machine_type')}
    result['provenance'].update(environment='GCP', packaged_commit=COMMIT)
    require({k: v for k, v in result.items() if k != 'provenance'}
            == {k: v for k, v in original.items() if k != 'provenance'}, 'non-provenance runtime override')
    return result


def verify_parent_payload(work, assignment, *, guard):
    """Actual execution gate only; never used against original raw in PREP tests."""
    initial = {}; total = 0; started = time.monotonic()
    base = safe_path(Path(work) / 'selected-parent')
    require({p.name for p in base.iterdir()} == set(assignment['parent_files']), 'parent raw roster changed')
    for name, pin in assignment['parent_files'].items():
        guard(); path = safe_path(base / name); s = path.lstat()
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_size == pin['bytes'], 'parent raw size/type differs')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(native(s) == native(os.fstat(fd)), 'parent raw open changed')
            hasher = hashlib.sha256(); size = 0
            while True:
                guard(); require(time.monotonic() - started < 1800, 'parent raw gate wall exhausted')
                block = os.read(fd, 1024**2)
                if not block: break
                hasher.update(block); size += len(block); total += len(block)
                require(size <= pin['bytes'] and total <= 2**32, 'parent raw read bound exhausted')
            require(size == pin['bytes'] and hasher.hexdigest() == pin['sha256']
                    and native(s) == native(os.fstat(fd)) == native(path.lstat()), 'parent raw EOF/SHA/native differs')
            initial[name] = native(s)
        finally:
            os.close(fd)
    return initial


def run_selected(work, output, assignment, verification, operational_commit, *, scheduler_factory=None, guard=None,
                 activation_handoff=None,prior_evaluation=None,activation_evaluation=None,
                 assignment_sha256=None,activation_handoff_sha256=None):
    check_science(); prepared = prepare(work, assignment)
    require(re.fullmatch('[a-f0-9]{64}', assignment.get('root_handoff_sha256') or ''), 'root handoff was not sealed')
    h = parse(read_pin(Path(work) / 'selected-root-handoff.json', assignment['root_handoff_sha256'])[0])
    active=activation_handoff
    if active is None:
        require(prior_evaluation is None and activation_evaluation is None
                and assignment_sha256 is None and activation_handoff_sha256 is None,'activation role cannot omit its H1')
        validate_root_handoff(h,prepared)
    else:
        require(all(isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v)
                    for v in (assignment_sha256,activation_handoff_sha256)), 'activation full-byte pins missing')
        require(parse(read_pin(Path(work)/'selected-original-case.json',assignment_sha256)[0])==assignment
                and parse(read_pin(Path(work)/'selected-activation-handoff.json',activation_handoff_sha256)[0])==active,
                'activation objects differ from actual retained full-byte inputs')
        ash=assignment_sha256
        evaluated0=validate_handoff_evaluation(prior_evaluation,h,prepared,ash,assignment['root_handoff_sha256'])
        evaluated1=validate_handoff_evaluation(activation_evaluation,active,prepared,ash,activation_handoff_sha256)
        require(evaluated0<=evaluated1,'activation assessment precedes original budget-scope assessment')
        validate_activation_handoff(active,h,prepared)
    require(callable(guard), 'live root controller/ownership guard is required')
    guard()
    parent_native = verify_parent_payload(work, assignment, guard=guard)
    if active is not None:validate_activation_handoff(active,h,prepared)
    from dams_sim.runtime import RuntimeLimits
    from dams_sim.config import Config
    from dams_sim.scheduler import Scheduler
    output = safe_path(output)
    require(stat.S_ISDIR(output.lstat().st_mode) and not os.path.lexists(output / 'cases'),
            'selected execution cannot overwrite or reset original attempts')
    limits = RuntimeLimits.from_dict(truthful_limits(prepared['original_limits'], verification, operational_commit))
    scheduler = (scheduler_factory or Scheduler)(output, limits, DRIVER)
    guard()
    results = scheduler.run([Config.from_dict(prepared['row']['config'])], {CID: prepared['origin']})
    require(scheduler.owned_workers_absent is True, 'selected workers were not reaped')
    guard(); check_science()
    for name, observed in parent_native.items():
        require(native((Path(work) / 'selected-parent' / name).lstat()) == observed, 'parent raw changed during execution')
    prepare(work, assignment)
    return {'schema': 'DAMS-original-selected-execution-result-1', 'case_id': CID,
            'results': results, 'owned_workers_absent': True, 'return_import_status': 'RETURN_IMPORT_BLOCKED',
            'original_scientific_case_count_increment': 0,
            'original_runtime_limits_sha256': RUNTIME, 'runtime_amendment_fields': ['provenance'],
            'operational_package_commit': operational_commit,
            'truthful_guest_execution_provenance': limits.provenance,
            'original_core_and_driver_unchanged': True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--assignment-sha256', required=True)
    p.add_argument('--validate-only', action='store_true')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--compute-runtime-sha256')
    p.add_argument('--execution-sha256')
    p.add_argument('--activation-handoff-sha256')
    p.add_argument('--prior-evaluation-sha256')
    p.add_argument('--activation-evaluation-sha256')
    args = p.parse_args(argv)
    require(args.validate_only != args.execute, 'choose validation or leased execution')
    assignment = parse(read_pin(args.work / 'selected-original-case.json', args.assignment_sha256)[0])
    check_science(); result = prepare(args.work, assignment)
    if args.execute:
        require(all(isinstance(x, str) and re.fullmatch('[a-f0-9]{64}', x)
                    for x in (args.compute_runtime_sha256, args.execution_sha256)),
                'leased execution pins missing')
        # The supervisor supplied these exact immutable inputs. It continues to
        # monitor source, controller lease, capacity and its owned process group
        # throughout the unchanged Scheduler.run call, not just at startup.
        import sys
        sys.path.insert(0, str(ROOT / 'research_tools'))
        from research_tools import compute_only_worker as cw
        with cw.directory(args.work) as work, cw.directory(ROOT) as root:
            raw, initial = work.read('compute-runtime.json')
            require(digest(raw) == args.compute_runtime_sha256, 'leased compute runtime differs')
            runtime = cw.validate_runtime(cw.parse(raw))
            require(runtime['original_selected_case']['assignment_sha256'] == args.assignment_sha256,
                    'supervisor assignment differs')
            observations = cw.validate_package(root, runtime)
            clock = cw.Clock(); control = None; activation_fences=[]
            def guard():
                nonlocal control
                cw.check_files(root, observations); work.check()
                require(work.read('compute-runtime.json') == (raw, initial), 'leased runtime changed')
                for name,sha,observed in activation_fences:
                    require(read_pin(args.work/name,sha)[1]==observed,'immutable activation observation changed')
                control = cw.lease(work, args.compute_runtime_sha256,
                                   runtime['controller_lease_timeout_seconds'], clock.now(), control)
                require(clock.now() < cw.utc(runtime['preservation_profile']['operational_soft_stop_utc']).timestamp(),
                        'operational soft cutoff elapsed')
            execution = parse(read_pin(args.work / 'output/cloud-execution.json', args.execution_sha256)[0])
            require(execution['runtime_sha256'] == args.compute_runtime_sha256
                    and execution['source_commit'] == runtime['source_commit'], 'supervisor execution binding differs')
            verification = execution['guest_hardware_verification']
            require(verification['hardware_frozen_verified'] is True, 'actual verified guest identity missing')
            activation={}
            if 'original_selected_activation' in runtime:
                value=runtime['original_selected_activation']
                require((args.activation_handoff_sha256,args.prior_evaluation_sha256,args.activation_evaluation_sha256)
                        ==(value['handoff_sha256'],value['prior_evaluation_sha256'],value['evaluation_sha256']),
                        'argv activation proofs differ from actual sealed runtime')
                for key,name,sha in [('activation_handoff','selected-activation-handoff.json',args.activation_handoff_sha256),
                                     ('prior_evaluation','selected-before-provider-evaluation.json',args.prior_evaluation_sha256),
                                     ('activation_evaluation','selected-activation-evaluation.json',args.activation_evaluation_sha256)]:
                    proof_raw,proof_native=read_pin(args.work/name,sha)
                    activation[key]=parse(proof_raw);activation_fences.append((name,sha,proof_native))
                require(execution.get('original_activation_handoff_sha256')==args.activation_handoff_sha256
                        and execution.get('original_before_provider_evaluation_sha256')==args.prior_evaluation_sha256
                        and execution.get('original_activation_evaluation_sha256')==args.activation_evaluation_sha256,
                        'actual BEGIN omitted activation assessment binding')
                activation.update(assignment_sha256=args.assignment_sha256,
                                  activation_handoff_sha256=args.activation_handoff_sha256)
            else:
                require(all(v is None for v in (args.activation_handoff_sha256,args.prior_evaluation_sha256,
                                                args.activation_evaluation_sha256)), 'unadmitted activation argv')
            completed = run_selected(args.work, args.work / 'output', assignment, verification,
                                     runtime['source_commit'], guard=guard,**activation)
            work.write('selected-execution-result.json', completed, exclusive=True)
            return 0
    print(json.dumps({'case_id': CID, 'selection_identity_sha256': result['selection_identity_sha256'],
                      'return_import_status': result['return_import_status'], 'root_authority_issued': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
