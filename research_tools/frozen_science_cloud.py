"""Explicit operational compatibility; original scientific files stay unchanged.

This is a root-sealed preservation/handoff interface, not scientific admission.
Root must obtain complete original semantic receipts and cooperative local
closure before sealing it. The guest never recasts local results as GCP runs.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

CORE = '4daf690d216ad819674a232fdd3d9de66a901d4a49c56fe6f626ed1f2e351605'
DRIVER = '65dde680a01d471e35ca47a6a588e445166b94736e91dddc0bf19189ddec6095'
ENTRY = 'ec747003cbed9503470e9ccc2b56ed2ba399c8309fe68459ff0cc9d10e07bee8'
CODEC = '114b122e8bdee99ce320eb40221cf0f16dc03cf77eed17fd2f9fe890902488c9'
SCHEMA = 'DAMS-frozen-science-preservation-2'
SELECTED_SCHEMA = 'DAMS-frozen-selected-science-preservation-1'
SELECTED_METADATA_BYTES = 32 * 1024**2
SELECTED_CAPACITY_FIELDS = {'schema','case_id','config_sha256','assignment_sha256',
    'selection_identity_sha256','original_runtime_limits_sha256','attempt_raw_upper_bytes',
    'max_attempts','output_metadata_upper_bytes','parent_raw_bytes','parent_raw_files'}
FIELDS = {'schema', 'source_sha256', 'driver_sha256', 'run_sh_sha256',
          'spec_sha256', 'inventory_sha256', 'legacy_scientific_provenance',
          'raw_bytes', 'raw_files', 'min_free_bytes', 'archive_codec_sha256',
          'archive_max_raw_bytes', 'archive_max_files', 'lease_timeout_seconds',
          'global_deadline_utc', 'cleanup_deadline_utc', 'handoff_receipt_sha256',
          'operational_soft_stop_utc', 'operational_hard_stop_utc',
          'original_runtime_limits_sha256'}
HANDOFF = {'schema', 'status', 'source_sha256', 'driver_sha256', 'spec_sha256',
           'inventory_sha256', 'owner_pid', 'owner_birth', 'exclusive_case_owner',
           'prior_pipeline_manifest_sha256', 'scientific_receipt_sha256',
           'inventory_file_sha256', 'original_execution_provenance',
           'file_sha256', 'case_bindings'}

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def hexsha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None

def validate_selected_profile(selected, preservation, limits):
    """Explicit singleton selection, retaining the complete original contract.

    This does not waive any return raw gate or claim macOS guest execution.
    Whole-pipeline preservation/handoff remains a separate, unchanged path.
    """
    from research_tools.original_longitudinal_selector import SPEC, RUNTIME, RUNTIME_VALUE
    require(isinstance(selected, dict) and set(selected) == {'schema', 'assignment_sha256'}
            and selected['schema'] == 'DAMS-original-longitudinal-selected-1'
            and hexsha(selected['assignment_sha256']), 'invalid original selected profile')
    validate_profile(preservation, limits, selected=selected)
    require(preservation['spec_sha256'] == SPEC
            and preservation['original_runtime_limits_sha256'] == RUNTIME
            and digest(canonical(limits)) == RUNTIME_VALUE,
            'selected profile is not the frozen original N1000 study/runtime')
    return selected

def selected_storage_bound(selected, p, limits):
    """Physical subset envelope; original RuntimeLimits and retries are intact.

    Two attempts remain possible under max_retries=1. The immutable parent is
    a separate input under work/selected-parent and is not a new output case.
    Full parent bytes must still pass the original parent payload reader.
    """
    from dams_sim.runtime import RuntimeLimits
    from research_tools.original_longitudinal_selector import CID,CONFIG,RUNTIME,RUNTIME_VALUE,SCHEMA as ASSIGNMENT_SCHEMA
    require(isinstance(selected,dict) and set(selected)=={'schema','assignment_sha256'}
            and selected['schema']==ASSIGNMENT_SCHEMA and hexsha(selected['assignment_sha256']),
            'selected storage needs an explicit fixed singleton assignment')
    l=RuntimeLimits.from_dict(limits); b=p.get('selected_capacity')
    require(isinstance(b,dict) and set(b)==SELECTED_CAPACITY_FIELDS
            and b['schema']=='DAMS-original-single-case-storage-bound-1'
            and (b['case_id'],b['config_sha256'],b['assignment_sha256'],b['original_runtime_limits_sha256'])
                ==(CID,CONFIG,selected['assignment_sha256'],RUNTIME)
            and digest(canonical(limits))==RUNTIME_VALUE and hexsha(b['selection_identity_sha256']),
            'singleton storage identity/runtime differs')
    require(type(b['attempt_raw_upper_bytes']) is int and b['attempt_raw_upper_bytes']==l.max_output_bytes
            and type(b['max_attempts']) is int and b['max_attempts']==l.max_retries+1
            and b['output_metadata_upper_bytes']==SELECTED_METADATA_BYTES
            and type(b['output_metadata_upper_bytes']) is int,
            'singleton storage cannot omit original fullcase/retry or metadata reservation')
    require(type(b['parent_raw_bytes']) is int and b['parent_raw_bytes']>0
            and type(b['parent_raw_files']) is int and b['parent_raw_files']==13,
            'singleton complete parent input is not separately reserved')
    return {'output_raw_upper_bytes':b['max_attempts']*b['attempt_raw_upper_bytes']+SELECTED_METADATA_BYTES,
            'parent_input_upper_bytes':b['parent_raw_bytes'],'max_attempts':b['max_attempts'],
            'attempt_raw_upper_bytes':b['attempt_raw_upper_bytes'],'metadata_upper_bytes':SELECTED_METADATA_BYTES}

def validate_profile(p, limits, *, global_deadline=None, selected=None):
    from dams_sim.runtime import RuntimeLimits, parse_deadline
    subset=isinstance(p,dict) and p.get('schema')==SELECTED_SCHEMA
    require(isinstance(p, dict) and set(p) == (FIELDS|{'selected_capacity'} if subset else FIELDS)
            and p['schema'] == (SELECTED_SCHEMA if subset else SCHEMA),
            'unsupported frozen-science preservation profile')
    require((p['source_sha256'], p['driver_sha256'], p['run_sh_sha256'], p['archive_codec_sha256'])
            == (CORE, DRIVER, ENTRY, CODEC), 'original science/entry/archive pin differs')
    require(hexsha(p['spec_sha256']) and hexsha(p['inventory_sha256']), 'science assignment pins missing')
    l = RuntimeLimits.from_dict(limits)
    require(not any(k.startswith('phase_') for k in limits), 'original science cannot accept phase runtime keys')
    require(p['legacy_scientific_provenance'] == l.provenance,
            'legacy scientific provenance contract cannot be silently changed')
    for k in ('raw_bytes', 'raw_files', 'archive_max_raw_bytes', 'archive_max_files', 'lease_timeout_seconds'):
        require(type(p[k]) is int and 0 < p[k] <= 100_000_000_000_000, 'invalid preservation bound')
    require(type(p['min_free_bytes']) is int and p['min_free_bytes'] >= l.min_free_disk_bytes,
            'preservation cannot lower original free floor')
    # Zero original file caps disable prospective science file reservations;
    # they are not a zero-file output promise. Preservation keeps its own
    # positive, enforced whole-output cap without rewriting RuntimeLimits.
    bound=selected_storage_bound(selected,p,limits) if subset else None
    require(p['raw_bytes'] >= (bound['output_raw_upper_bytes'] if subset else l.batch_max_output_bytes)
            and (not l.batch_max_output_files or p['raw_files'] >= l.batch_max_output_files),
            'preservation must cover enabled original raw byte/file caps')
    require(p['archive_max_raw_bytes'] >= p['raw_bytes'] and p['archive_max_files'] >= p['raw_files'],
            'full-raw archive cannot omit declared raw capacity')
    require(1 <= p['lease_timeout_seconds'] <= 86400, 'lease must be finite')
    gd = parse_deadline(p['global_deadline_utc']); cd = parse_deadline(p['cleanup_deadline_utc'])
    require(cd <= gd, 'preservation cleanup cannot extend original global deadline')
    require('deadline_utc' in limits and 'stop_cutoff_utc' in limits,
            'original scientific maximum dates must be retained explicitly')
    require(hexsha(p['original_runtime_limits_sha256']), 'original runtime full-byte pin missing')
    operational_soft = parse_deadline(p['operational_soft_stop_utc'])
    operational_hard = parse_deadline(p['operational_hard_stop_utc'])
    require(operational_soft <= l.deadline and operational_hard <= l.stop_cutoff,
            'operational stop cannot extend original scientific maximum dates')
    require(operational_soft + l.cooperative_stop_grace_seconds <= operational_hard <= cd,
            'operational checkpoint and stop must fit cleanup without reducing original grace')
    if global_deadline is not None:
        require(gd == parse_deadline(global_deadline), 'original global deadline changed')
    require(hexsha(p['handoff_receipt_sha256']),
            'handoff must have an exact retained receipt pin')
    return p

def validate_selected_stage(stage, *, now=None, require_fresh_handoff=False):
    """Before launch, bind actual full assignment bytes to the existing stage.

    This validates declared Root handoff metadata; the Root actor must also
    observe its lock ownership/writer absence. It issues no paid authority.
    The generic collector assignment_sha256 retains its own separate schema.
    """
    selected=stage.get('original_selected_case')
    if selected is None:
        require('selected_assignment' not in stage and 'selection_identity_sha256' not in stage
                and 'selected_handoff_evaluation' not in stage,
                'singleton stage cannot hide its selector marker')
        return None
    from research_tools.original_longitudinal_selector import prepare,validate_root_handoff,read_pin,parse,SPEC,CID,CONFIG
    require((stage['spec'],stage['scale'])==('longitudinal-adoption-5y',1000),
            'singleton stage changes original scientific study')
    require(stage.get('preservation_profile',{}).get('schema')==SELECTED_SCHEMA,
            'singleton stage requires separately proven physical subset profile')
    validate_selected_profile(selected,stage['preservation_profile'],stage['runtime_limits'])
    ref=stage.get('selected_assignment')
    require(isinstance(ref,dict) and set(ref)=={'path','sha256'} and hexsha(ref['sha256'])
            and ref['sha256']==selected['assignment_sha256'], 'stage singleton assignment pin differs')
    path=Path(ref['path']);work=Path(stage.get('root_handoff_work',''))
    require(path.is_absolute() and work.is_absolute() and path==work/'selected-original-case.json',
            'stage singleton assignment is outside the fixed input work')
    raw,_=read_pin(path,ref['sha256']); assignment=parse(raw); prepared=prepare(work,assignment)
    require(assignment['root_handoff_sha256']==stage['preservation_profile']['handoff_receipt_sha256'],
            'stage selected handoff differs from retained assignment')
    handoff_raw,_=read_pin(work/'selected-root-handoff.json',assignment['root_handoff_sha256'])
    # H0 and its actual before-provider evaluation are immutable funding
    # scope. Bootstrap/collection may outlive H0's observation lease. Only a
    # true pre-create check requests current H0; science start uses separate
    # same-scope H1, freshly checked by the original validator in the selector.
    h=parse(handoff_raw); evaluation_ref=stage.get('selected_handoff_evaluation')
    require(isinstance(evaluation_ref,dict) and set(evaluation_ref)=={'path','sha256'}
            and Path(evaluation_ref['path'])==work/'selected-before-provider-evaluation.json',
            'stage prior handoff evaluation is not explicitly fixed')
    evaluation_raw,_=read_pin(evaluation_ref['path'],evaluation_ref['sha256'])
    from research_tools.original_longitudinal_selector import validate_handoff_evaluation
    evaluated=validate_handoff_evaluation(parse(evaluation_raw),h,prepared,ref['sha256'],
                                         assignment['root_handoff_sha256'])
    validate_root_handoff(h,prepared,now=now if require_fresh_handoff else evaluated)
    b=stage['preservation_profile']['selected_capacity']
    require(b['selection_identity_sha256']==stage.get('selection_identity_sha256')==prepared['selection_identity_sha256']
            and b['attempt_raw_upper_bytes']==int(prepared['row']['config']['max_output_mb']*1_000_000)
            and b['parent_raw_bytes']==sum(v['bytes'] for v in assignment['parent_files'].values())
            and b['parent_raw_files']==len(assignment['parent_files'])==13
            and (b['case_id'],b['config_sha256'])==(CID,CONFIG),
            'stage physical bound is not the exact prepared singleton/complete parent')
    return prepared

def validate_handoff(p, h):
    require(isinstance(h, dict) and set(h) == HANDOFF and h['schema'] == 'DAMS-original-science-handoff-1'
            and h['status'] == 'COOPERATIVELY_CLOSED_ORIGINAL_EVIDENCE', 'handoff is not a closed original owner')
    for k in ('source_sha256', 'driver_sha256', 'spec_sha256', 'inventory_sha256'):
        require(h[k] == p[k], 'handoff science assignment differs')
    require(type(h['owner_pid']) is int and h['owner_pid'] > 1 and isinstance(h['owner_birth'], str)
            and 0 < len(h['owner_birth']) <= 128 and hexsha(h['exclusive_case_owner']), 'exclusive owner identity missing')
    for k in ('prior_pipeline_manifest_sha256', 'scientific_receipt_sha256', 'inventory_file_sha256'):
        require(hexsha(h[k]), 'original preimage/scientific gate receipt missing')
    require(h['original_execution_provenance'] == p['legacy_scientific_provenance'],
            'original local provenance cannot be relabeled as GCP')
    require(isinstance(h['file_sha256'], dict) and h['file_sha256'] and isinstance(h['case_bindings'], dict),
            'handoff must bind the full original raw roster and cases')
    return h

def require_local_owner_closed(p, h, *, owner_present=None):
    """Root-side final admission only. Presence/unknown conservatively refuses.

    A PID reused by another local process also refuses; this never signals it.
    Guest PID namespaces cannot replace this root-side proof.
    """
    validate_handoff(p, h)
    if owner_present is None:
        def owner_present(pid, birth):
            try: os.kill(pid, 0)
            except ProcessLookupError: return False
            except PermissionError: return True
            return True
    require(owner_present(h['owner_pid'], h['owner_birth']) is False,
            'local owner still exists or absence is unproved; duplicate case handoff refused')
    return h

def read_json(path, expected, *, bound=4*1024**2):
    raw = checked_bytes(path, bound)
    require(digest(raw) == expected, 'retained migration reference bytes differ')
    def pairs(items):
        value = {}
        for k, v in items:
            require(k not in value, 'duplicate migration JSON key'); value[k] = v
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False, 'nonfinite migration JSON'))

def checked_bytes(path, bound):
    path = Path(path)
    for parent in (path.parent, *path.parent.parents):
        require(stat.S_ISDIR(parent.lstat().st_mode), 'unsafe migration ancestor')
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= bound,
            'migration reference is not an owned bounded regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    key = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns, x.st_nlink)
    try:
        require(key(os.fstat(fd)) == key(before), 'migration open identity changed')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(bound + 1)
        require(key(os.fstat(fd)) == key(before) == key(path.lstat()) and len(raw) == before.st_size,
                'migration reference changed')
    finally:
        os.close(fd)
    return raw

def prepare_existing_output(work, p):
    """Verify a preinstalled root-approved migration; never rewrite any raw leaf.

    Root retains the old pipeline manifest outside new output, because original
    runtime locking correctly forbids an implicit operation-control migration.
    Full scientific semantics are separately pinned to root's actual receipt.
    """
    work = Path(work); output = work/'output'
    require(p['handoff_receipt_sha256'] is not None, 'existing output needs exact root handoff')
    h = validate_handoff(p, read_json(work/'handoff-receipt.json', p['handoff_receipt_sha256']))
    prior = read_json(work/'prior-pipeline-manifest.json', h['prior_pipeline_manifest_sha256'])
    require(prior['source_sha256'] == CORE and prior['pipeline_driver_sha256'] == DRIVER
            and prior['spec_sha256'] == p['spec_sha256'], 'preserved original pipeline preimage differs')
    inventory = read_json(work/'original-inventory.json', h['inventory_file_sha256'])
    checked_bytes(work/'scientific-receipt.json', 4*1024**2)
    require(digest(checked_bytes(work/'scientific-receipt.json', 4*1024**2)) == h['scientific_receipt_sha256'],
            'root actual scientific receipt not preserved')
    require(digest(canonical(inventory)) == p['inventory_sha256'], 'exact original inventory changed')
    require(not os.path.lexists(output/'pipeline_manifest.json') and not os.path.lexists(output/'.pipeline.lock'),
            'old locked pipeline controls must remain a preserved preimage, not be silently rewritten')
    require(stat.S_ISDIR(output.lstat().st_mode), 'migration output unsafe')
    files = {}
    for root, dirs, names in os.walk(output, followlinks=False):
        for name in dirs:
            require(stat.S_ISDIR((Path(root)/name).lstat().st_mode), 'unsafe migration directory')
        for name in names:
            path = Path(root)/name; info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe migration raw leaf')
            files[path.relative_to(output).as_posix()] = path
    require(set(files) == set(h['file_sha256']), 'missing/extra migration raw payload')
    for name, path in files.items():
        require(hexsha(h['file_sha256'][name]), 'invalid original raw SHA')
        before = path.stat(); hash_value = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(os.fstat(fd).st_ino == before.st_ino and os.fstat(fd).st_dev == before.st_dev,
                    'raw migration open identity changed')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                for block in iter(lambda: stream.read(1024**2), b''): hash_value.update(block)
            after = os.fstat(fd)
            require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    == (path.lstat().st_dev, path.lstat().st_ino, path.lstat().st_size,
                        path.lstat().st_mtime_ns, path.lstat().st_ctime_ns), 'raw migration namespace drift')
        finally: os.close(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and hash_value.hexdigest() == h['file_sha256'][name],
                'original raw bytes drift before resumed entry')
    by_id = {r['case_id']: r for r in inventory}
    require(len(by_id) == len(inventory), 'duplicate original scientific case')
    for cid, binding in h['case_bindings'].items():
        require(cid in by_id and set(binding) == {'manifest', 'manifest_sha256', 'config_sha256', 'world', 'seed'},
                'migration case is not assigned')
        name = binding['manifest']
        require(name in files and name.startswith('cases/'+cid+'/attempt-') and name.endswith('/manifest.json')
                and binding['manifest_sha256'] == h['file_sha256'][name], 'original case manifest binding missing')
        m = read_json(files[name], binding['manifest_sha256'])
        c = by_id[cid]['config']
        require(m['config'] == c and m['config_sha256'] == binding['config_sha256'] == digest(canonical(c))
                and c['world'] == binding['world'] and c['seed'] == binding['seed']
                and m['source_sha256'] == CORE and m['pipeline_driver_sha256'] == DRIVER,
                'original Config/source/world/seed changed')
    manifest_cases = {path.parts[1] for path in map(Path, files) if len(path.parts) >= 4
                      and path.parts[0] == 'cases' and path.name == 'manifest.json'}
    require(manifest_cases == set(h['case_bindings']), 'migration manifest case coverage incomplete')
    check_output_capacity(output, p)
    return {'handoff_receipt_sha256': p['handoff_receipt_sha256'], 'migrated_cases': sorted(h['case_bindings']),
            'legacy_scientific_provenance_is_not_guest_attestation': True,
            'scientific_receipt_sha256': h['scientific_receipt_sha256']}

def check_output_capacity(output, p, *, selected=None):
    output = Path(output); total = count = 0
    for root, dirs, names in os.walk(output, followlinks=False):
        for name in dirs: require(stat.S_ISDIR((Path(root)/name).lstat().st_mode), 'unsafe owned output directory')
        for name in names:
            path = Path(root)/name
            try: s = path.lstat()
            except FileNotFoundError: continue  # original atomic publication; internal reservations still apply
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'unsafe owned output leaf')
            total += s.st_size; count += 1
    require(total <= p['raw_bytes'] and count <= p['raw_files'], 'no-phase preservation raw budget exhausted')
    if p.get('schema')==SELECTED_SCHEMA:
        # The original Scheduler still controls its original case caps. This
        # additional supervisor enforces the explicit one-CID physical scope,
        # including failed attempts and all terminal/sidecar/log metadata.
        require(selected is not None,'singleton output guard cannot lose selection identity')
        cid=p['selected_capacity']['case_id']; metadata=0; attempts=set(); attempt_bytes={}
        for root,dirs,names in os.walk(output,followlinks=False):
            relative=Path(root).relative_to(output)
            if relative.parts[:1]==('cases',):
                require(len(relative.parts)<2 or relative.parts[1]==cid,'singleton output contains another case')
                if len(relative.parts)>=3:
                    require(re.fullmatch(r'attempt-[0-9]{3,}',relative.parts[2]) is not None,
                            'singleton case has unsupported raw namespace')
                    attempts.add(relative.parts[2])
            for name in names:
                path=Path(root)/name
                try: size=path.lstat().st_size
                except FileNotFoundError: continue
                if relative.parts[:2]!=('cases',cid) or len(relative.parts)<3: metadata+=size
                else: attempt_bytes[relative.parts[2]]=attempt_bytes.get(relative.parts[2],0)+size
        require(len(attempts)<=p['selected_capacity']['max_attempts']
                and metadata<=SELECTED_METADATA_BYTES
                and all(n<=p['selected_capacity']['attempt_raw_upper_bytes'] for n in attempt_bytes.values()),
                'singleton retries/fullcase bytes or terminal metadata exceed their separate bound')
    require(shutil.disk_usage(output).free >= p['min_free_bytes'], 'no-phase preservation free floor exhausted')
    return {'logical_bytes': total, 'files': count}
