"""Explicit operational r5 return acceptance; the original science stays frozen.

Registry expectations supplement (never replace) the ORIGINAL full raw gates.
No original module, sys.modules entry, manifest or runtime file is patched.
The new process uses a named private pipeline namespace with four declared
metadata dependency adaptations. No registry means the original default path.
Registry authority is storage/acceptance only, not provider or Model permission.
Original selected assignment preparation is reused, including all original
metadata inputs and the parent366/full13 metadata contract. No parent payload
is read by this adapter; the Root raw receipt and original readers remain needed.
"""
from __future__ import annotations
import argparse
import ast
import copy
import dataclasses
import json
from pathlib import Path
import re
import stat
import sys
import time

from research_tools import original_longitudinal_selector as s
from dams_sim import longitudinal_pipeline as original_pipeline
from dams_sim.scheduler import Scheduler, branch_identity
from dams_sim.spec import case_key
from dams_sim.storage import canonical, digest
from research_tools import validate_longitudinal as original_validator

ROOT = Path(__file__).resolve().parents[1]
LP_SHA = 'b24d7df0eb2f22eeee6de17af450b6a03a0c58c755629c871a8c953592d9d65a'
RAW_SHA = '55300a9b6850080c902d025abfa4b673794b9aac70d7aa214afab6a10b4f2214'
FSC_SHA = '81126605aca5a8ef6631b943484f0d358082957d48b74f572724f8ace71a1d38'
SELECTOR_SHA = '02cc1450e87ac5d22891d1b33718aa1cf781a719284d6b176a00c799507f8b4f'
IMPORT_SHA = digest(Path(__file__).read_bytes())
REGISTRY_KEYS = {'schema','status','source_sha256','driver_sha256','entry_sha256','spec_sha256',
                 'inventory_file_sha256','original_runtime_sha256','issued_utc','expires_utc','cases'}
CASE_KEYS = {'case_id','config_sha256','branch_identity','expected_provenance','attempt_relative',
             'manifest_pin','raw_native','raw_gate_pin','custody_pin','guest_execution_pin','source_package_pin',
             'guest_runtime_pin','selected_assignment_pin'}
ASSIGNMENT_KEYS = {'schema','case_id','config_sha256','source_sha256','driver_sha256','spec_sha256',
                   'input_sha256','parent_files','root_handoff_sha256'}
EVALUATION_KEYS = {'schema','evaluated_utc','original_root_handoff_sha256',
                   'selected_assignment_sha256','case_id','source_sha256','driver_sha256',
                   'original_runtime_limits_sha256','result'}


def require(ok, reason):
    if not ok: raise ValueError(reason)


class Registry:
    """Fixed-SHA Root-issued expectations, current custody and invalidation pins."""
    def __init__(self, root, registry_pin, authority_pin, inventory_pin, runtime_pin, *, now=None):
        self.root = s.safe_path(root)
        self.references = []
        self.metadata_read_bytes = 0
        self.registry_sha256 = registry_pin['sha256']
        self.value = self.read(registry_pin)
        self.value_fingerprint = digest(canonical(self.value))
        v = self.value
        require(set(v) == REGISTRY_KEYS and v['schema'] == 'DAMS-original-external-acceptance-registry-1'
                and v['status'] == 'ROOT_ISSUED_TRUTHFUL_EXTERNAL_CASE_ACCEPTANCE', 'registry is not Root-issued')
        require((v['source_sha256'],v['driver_sha256'],v['entry_sha256'],v['spec_sha256'],
                 v['inventory_file_sha256'],v['original_runtime_sha256'])
                == (s.CORE,s.DRIVER,s.ENTRY,s.SPEC,s.INVENTORY,s.RUNTIME), 'registry scientific identity differs')
        inventory_raw = self.read_bytes(inventory_pin)
        runtime_raw = self.read_bytes(runtime_pin)
        require(digest(inventory_raw) == s.INVENTORY and digest(runtime_raw) == s.RUNTIME,
                'registry original inventory/runtime full-byte pins differ')
        self.inventory = s.parse(inventory_raw); self.limits_value = s.parse(runtime_raw)
        self.rows = {r['case_id']: r for r in self.inventory}
        require(len(self.inventory) == len(self.rows) == 144, 'registry original inventory is not 144 unique identities')
        self.cases = {r['case_id']: r for r in v['cases']}
        require(isinstance(v['cases'],list) and 0 < len(self.cases) == len(v['cases']) <= 144,
                'registry repeats or omits accepted identities')
        authority = self.read(authority_pin)
        require(set(authority) == {'schema','status','registry_sha256','accepted_case_ids','issued_utc','expires_utc',
                                  'provider_permission','science_model_changed'}
                and authority['schema'] == 'DAMS-root-original-case-acceptance-authority-1'
                and authority['status'] == 'ROOT_ISSUED_ACCEPTANCE_ONLY'
                and authority['registry_sha256'] == self.registry_sha256
                and authority['accepted_case_ids'] == sorted(self.cases)
                and authority['provider_permission'] is False and authority['science_model_changed'] is False
                and authority['issued_utc'] == v['issued_utc'] and authority['expires_utc'] == v['expires_utc'],
                'Root acceptance authority is missing, broader, or bound to other bytes')
        self.source_native = {}
        for p in sorted((ROOT/'dams_sim').glob('*.py')):
            self.source_native[p] = s.native(p.lstat())
        for p in (ROOT/'run.sh', ROOT/'dams_sim/longitudinal_pipeline.py',
                  ROOT/'research_tools/validate_longitudinal.py', Path(__file__),
                  ROOT/'research_tools/original_longitudinal_selector.py', ROOT/'research_tools/frozen_science_cloud.py'):
            self.source_native[p] = s.native(p.lstat())
        self.proofs = {}
        self.assignment_scopes = {}
        for cid, record in self.cases.items():
            require(set(record) == CASE_KEYS and cid in self.rows, 'unassigned or malformed external case')
            row = self.rows[cid]
            require(record['config_sha256'] == digest(canonical(row['config'])), 'external config differs from original assignment')
            self.check_branch(row, record['branch_identity'])
            expected = record['expected_provenance']
            require(set(expected) == {'environment','packaged_commit','instance_id','machine_type','zone'}
                    and all(isinstance(x,str) and 0 < len(x) <= 512 for x in expected.values())
                    and expected['environment'] == 'GCP' and expected['packaged_commit'] == s.COMMIT,
                    'truthful external provenance is incomplete or mislabels legacy environment')
            relative = Path(record['attempt_relative'])
            require(not relative.is_absolute() and relative.parts[:2] == ('cases',cid)
                    and len(relative.parts) == 3 and re.fullmatch(r'attempt-[0-9]{3,}',relative.name),
                    'external attempt escapes its original case')
            gate = self.read(record['raw_gate_pin']); owner = self.read(record['custody_pin'])
            execution = self.read(record['guest_execution_pin']); package = self.read(record['source_package_pin'])
            guest_runtime = self.read(record['guest_runtime_pin'])
            assignment = self.read(record['selected_assignment_pin'])
            self.assignment_scopes[cid] = self.prepare_assignment(record, assignment, gate, execution)
            scope = self.assignment_scopes[cid]
            require(guest_runtime['schema'] == 'DAMS-compute-only-guest-1'
                    and guest_runtime['spec'] == 'longitudinal-adoption-5y' and guest_runtime['scale'] == 1000
                    and guest_runtime['runtime_limits'] == self.limits_value
                    and guest_runtime['preservation_profile']['original_runtime_limits_sha256'] == s.RUNTIME
                    and guest_runtime['original_selected_case'] == {'schema':s.SCHEMA,
                        'assignment_sha256':record['selected_assignment_pin']['sha256']}
                    and guest_runtime['original_selected_activation'] == {
                        'schema':'DAMS-original-selected-activation-1',
                        'handoff_sha256':scope['activation_handoff_sha256'],
                        'prior_evaluation_sha256':scope['before_provider_evaluation_sha256'],
                        'evaluation_sha256':scope['activation_evaluation_sha256']}
                    and assignment['schema'] == s.SCHEMA
                    and assignment['case_id'] == cid and assignment['config_sha256'] == record['config_sha256']
                    and assignment['source_sha256'] == s.CORE and assignment['driver_sha256'] == s.DRIVER
                    and assignment['spec_sha256'] == s.SPEC,
                    'true guest runtime/selected assignment does not preserve original identity/resources')
            require(gate['schema'] == 'DAMS-root-original-external-full-raw-gate-1'
                    and gate['status'] == 'ROOT_FULL_ORIGINAL_CASE_RAW_VALIDATED'
                    and gate['case_id'] == cid and gate['config_sha256'] == record['config_sha256']
                    and (gate['source_sha256'],gate['driver_sha256'],gate['original_runtime_sha256'],gate['validator_sha256'])
                    == (s.CORE,s.DRIVER,s.RUNTIME,RAW_SHA)
                    and gate['branch_identity'] == record['branch_identity']
                    and gate['expected_provenance'] == expected
                    and gate['manifest_sha256'] == record['manifest_pin']['sha256']
                    and gate['source_package_sha256'] == record['source_package_pin']['sha256']
                    and gate['guest_execution_sha256'] == record['guest_execution_pin']['sha256'],
                    'full raw/source/branch/execution gate is not the accepted original case')
            require(owner['schema'] == 'DAMS-root-closed-original-case-custody-1'
                    and owner['status'] == 'ROOT_CLOSED_CURRENT_RAW_CUSTODY'
                    and owner['case_id'] == cid and owner['manifest_sha256'] == record['manifest_pin']['sha256']
                    and owner['raw_native'] == record['raw_native'] and owner['active_writers'] == []
                    and owner['required_hot_consumers'] == [] and type(owner['producer_exit_code']) is int
                    and owner['producer_exit_code'] == 0 and owner['producer_reaped'] is True
                    and owner['guest_execution_sha256'] == record['guest_execution_pin']['sha256']
                    and owner['original_activation_handoff_sha256'] == scope['activation_handoff_sha256'],
                    'current closed producer/raw custody is missing or still active')
            require(execution['schema'] == 'DAMS-compute-only-execution-1'
                    and execution['selected_case_id'] == cid
                    and execution['source_manifest_sha256'] == record['source_package_pin']['sha256']
                    and execution['source_commit'] == package['commit']
                    and execution['runtime_sha256'] == record['guest_runtime_pin']['sha256']
                    and execution['original_runtime_limits_sha256'] == s.RUNTIME
                    and execution['original_activation_handoff_sha256'] == scope['activation_handoff_sha256']
                    and execution['original_activation_evaluation_sha256'] == scope['activation_evaluation_sha256']
                    and execution['original_before_provider_evaluation_sha256'] == scope['before_provider_evaluation_sha256']
                    and type(execution['exit_code']) is int and execution['exit_code'] == 0
                    and execution['owned_pipeline_group_verification']['owned_pipeline_group_absent'] is True
                    and execution['inputs_unchanged'] is True,
                    'true guest execution/package/runtime binding differs')
            from dams_sim.runtime import parse_deadline
            observed = parse_deadline(owner['observed_utc'])
            issued = parse_deadline(self.value['issued_utc'])
            require(parse_deadline(execution['start_utc']) <= parse_deadline(execution['end_utc'])
                    <= observed <= issued and issued-observed <= 300,
                    'completion custody is stale, precedes terminal exit, or follows acceptance')
            guest = execution['guest_hardware_verification']
            require(guest['hardware_frozen_verified'] is True
                    and all(guest.get(k) == expected[k] for k in ('instance_id','machine_type','zone')),
                    'actual guest identity differs from raw expectation')
            files = package['source_files_sha256']
            require(set(package) == {'commit','source_files_sha256'}
                    and isinstance(package['commit'],str) and re.fullmatch('[a-f0-9]{40}',package['commit'])
                    and {n for n in files if n.startswith('dams_sim/') and n.endswith('.py')}
                    == {p.relative_to(ROOT).as_posix() for p in (ROOT/'dams_sim').glob('*.py')},
                    'guest source package invents release or changes core module roster')
            for p in sorted((ROOT/'dams_sim').glob('*.py')):
                require(files.get(p.relative_to(ROOT).as_posix()) == digest(p.read_bytes()),
                        'guest source package changes original science')
            require(files.get('run.sh') == s.ENTRY, 'guest source package changes original entry')
            self.proofs[cid] = (gate,owner,execution,package)
        self.proof_fingerprint = digest(canonical(self.proofs))
        self.assignment_scope_fingerprint = digest(canonical(self.assignment_scopes))
        self.guard(now=now)

    def prepare_assignment(self, record, assignment, gate, execution):
        """Reuse the actual frozen selector without reading parent payload.

        The selector prepares deliberately one exact original assignment,
        not a newly generalized guest selection API. Five named small metadata
        files are read with their original SHA/native fences; prepare checks
        their complete identity and the parent366/full13 metadata contract.
        H0 retains its SHA-bound historical before-provider evaluation. H1 has
        a separate fresh-before-Model evaluation, with unchanged H0 authority
        fields except observation UTC and writer-absence receipt. Neither old
        evaluation is presented as current/live lock ownership at return time.
        """
        require(isinstance(assignment,dict) and set(assignment) == ASSIGNMENT_KEYS,
                'external assignment must use the original complete nine-key schema')
        path = s.safe_path(record['selected_assignment_pin']['path'])
        require(path.name == 'selected-original-case.json', 'original assignment filename differs')
        work = path.parent
        total = 0
        for relative, sha in s.INPUTS.items():
            source = s.safe_path(work/relative)
            size = source.lstat().st_size
            self.read_bytes({'path':str(source),'sha256':sha,'bytes':size})
            total += size
        # The original function reads the same five files again. Include those
        # actual reads in the same finite metadata budget, rather than hiding
        # them behind the reused validator.
        require(self.metadata_read_bytes + total <= 16*1024**2,
                'finite original preparation metadata budget exceeded')
        prepared = s.prepare(work,assignment)
        self.metadata_read_bytes += total
        require(prepared['row']['case_id'] == record['case_id']
                and canonical(prepared['row']) == canonical(self.rows[record['case_id']])
                and branch_identity(prepared['origin']) == record['branch_identity']
                and prepared['original_limits'] == self.limits_value,
                'original selected preparation differs from registered case/parent/runtime')
        for relative,native in prepared['input_native'].items():
            self.references.append((work/relative,native))
        require(isinstance(assignment['root_handoff_sha256'],str)
                and re.fullmatch('[a-f0-9]{64}',assignment['root_handoff_sha256']),
                'original selected Root handoff was not sealed')
        handoff_path = s.safe_path(work/'selected-root-handoff.json')
        handoff = self.read({'path':str(handoff_path),
                             'sha256':assignment['root_handoff_sha256'],
                             'bytes':handoff_path.lstat().st_size})
        require(isinstance(gate,dict) and {'original_handoff_evaluation_pin',
                'original_handoff_evaluated_utc','selected_assignment_sha256',
                'original_root_handoff_sha256','original_activation_evaluation_pin',
                'original_activation_evaluated_utc','original_activation_handoff_sha256'}.issubset(gate)
                and isinstance(execution,dict) and {'start_utc','end_utc'}.issubset(execution),
                'Root before-start evaluation and actual guest BEGIN evidence are missing')
        require(s.safe_path(gate['original_handoff_evaluation_pin']['path'])
                == work/'selected-before-provider-evaluation.json',
                'before-provider evaluation is outside its fixed selected-work filename')
        evaluation = self.read(gate['original_handoff_evaluation_pin'])
        require(isinstance(evaluation,dict) and set(evaluation) == EVALUATION_KEYS
                and evaluation['schema'] == 'DAMS-root-original-before-start-handoff-evaluation-1'
                and evaluation['result'] == 'ROOT_BEFORE_START_ORIGINAL_HANDOFF_VALIDATED'
                and evaluation['evaluated_utc'] == gate['original_handoff_evaluated_utc']
                and evaluation['case_id'] == record['case_id']
                and (evaluation['source_sha256'],evaluation['driver_sha256'],
                     evaluation['original_runtime_limits_sha256']) == (s.CORE,s.DRIVER,s.RUNTIME)
                and evaluation['selected_assignment_sha256']
                    == gate['selected_assignment_sha256'] == record['selected_assignment_pin']['sha256']
                and evaluation['original_root_handoff_sha256']
                    == gate['original_root_handoff_sha256'] == assignment['root_handoff_sha256'],
                'original before-start evaluation receipt does not bind the full assignment/handoff')
        from dams_sim.runtime import parse_deadline
        evaluated = parse_deadline(evaluation['evaluated_utc'])
        require(parse_deadline(handoff['observed_utc']) <= evaluated,
                'original before-provider evaluation precedes H0 observation')
        # Every original H0 identity, parent, runtime, Root-lock shape and lease
        # predicate is evaluated at its pinned actual before-provider time.
        s.validate_handoff_evaluation(evaluation,handoff,prepared,
                                      record['selected_assignment_pin']['sha256'],assignment['root_handoff_sha256'])
        activation_path = s.safe_path(work/'selected-activation-handoff.json')
        activation_sha = gate['original_activation_handoff_sha256']
        activation = self.read({'path':str(activation_path),'sha256':activation_sha,
                                'bytes':activation_path.lstat().st_size})
        activation_pin = gate['original_activation_evaluation_pin']
        require(s.safe_path(activation_pin['path']) == work/'selected-activation-evaluation.json'
                and activation_sha != assignment['root_handoff_sha256']
                and activation_pin['sha256'] != gate['original_handoff_evaluation_pin']['sha256'],
                'activation H1/evaluation must be separate fixed-name evidence, not H0 reused')
        activation_evaluation = self.read(activation_pin)
        require(isinstance(activation_evaluation,dict) and set(activation_evaluation) == EVALUATION_KEYS
                and activation_evaluation['schema'] == 'DAMS-root-original-before-start-handoff-evaluation-1'
                and activation_evaluation['result'] == 'ROOT_BEFORE_START_ORIGINAL_HANDOFF_VALIDATED'
                and activation_evaluation['evaluated_utc'] == gate['original_activation_evaluated_utc']
                and activation_evaluation['case_id'] == record['case_id']
                and (activation_evaluation['source_sha256'],activation_evaluation['driver_sha256'],
                     activation_evaluation['original_runtime_limits_sha256']) == (s.CORE,s.DRIVER,s.RUNTIME)
                and activation_evaluation['selected_assignment_sha256'] == record['selected_assignment_pin']['sha256']
                and activation_evaluation['original_root_handoff_sha256'] == activation_sha,
                'activation evaluation does not bind the unchanged original assignment and H1')
        activation_evaluated = parse_deadline(activation_evaluation['evaluated_utc'])
        s.validate_handoff_evaluation(activation_evaluation,activation,prepared,
                                      record['selected_assignment_pin']['sha256'],activation_sha)
        require(evaluated <= activation_evaluated
                and parse_deadline(activation['observed_utc']) <= activation_evaluated
                <= parse_deadline(execution['start_utc']) <= parse_deadline(execution['end_utc'])
                <= parse_deadline(self.value['issued_utc']),
                'H0/H1 evaluation, actual science BEGIN, terminal exit and acceptance order differ')
        # The selector-owned helper preserves every whole H0 field other than
        # the two explicitly permitted fresh observations, and calls the full
        # original handoff validator at the pinned H1 before-Model evaluation.
        s.validate_activation_handoff(activation,handoff,prepared,now=activation_evaluated)
        # A valid earlier E1 does not prove that H1's unchanged lease still
        # covered the actual science BEGIN after a delayed handover.
        s.validate_activation_handoff(activation,handoff,prepared,now=parse_deadline(execution['start_utc']))
        return {'prepared':prepared,'handoff':handoff,
                'assignment_sha256':record['selected_assignment_pin']['sha256'],
                'root_handoff_sha256':assignment['root_handoff_sha256'],
                'before_start_evaluation':evaluation,'guest_start_utc':execution['start_utc'],
                'before_provider_evaluation_sha256':gate['original_handoff_evaluation_pin']['sha256'],
                'activation_handoff':activation,'activation_handoff_sha256':activation_sha,
                'activation_evaluation_sha256':activation_pin['sha256'],
                'activation_evaluation':activation_evaluation,'guest_end_utc':execution['end_utc'],
                'historical_handoff_only_not_live_lock':True}

    def read_bytes(self, pin):
        require(isinstance(pin,dict) and set(pin) in ({'path','sha256','bytes'}, {'path','sha256','bytes','native'})
                and type(pin['bytes']) is int and 0 < pin['bytes'] <= s.BOUND
                and isinstance(pin['sha256'],str) and re.fullmatch('[a-f0-9]{64}',pin['sha256']), 'invalid acceptance reference')
        require(self.metadata_read_bytes + pin['bytes'] <= 16*1024**2, 'finite acceptance metadata budget exceeded')
        raw, observed = s.read_pin(pin['path'],pin['sha256'])
        require(len(raw) == pin['bytes'] and ('native' not in pin or pin['native'] == observed),
                'acceptance reference bytes/native differ')
        self.references.append((Path(pin['path']),observed))
        self.metadata_read_bytes += len(raw)
        return raw

    def read(self, pin): return s.parse(self.read_bytes(pin))

    def check_branch(self, row, origin):
        parent = row['tags']['parent_case_id']
        if parent is None:
            require(origin is None, 'root external case invents parent'); return
        require(isinstance(origin,dict) and set(origin) == {'parent_case_id','parent_source_sha256',
                'parent_config_sha256','parent_day','parent_state_semantic_sha256'}
                and origin['parent_case_id'] == parent and origin['parent_source_sha256'] == s.CORE
                and origin['parent_config_sha256'] == digest(canonical(self.rows[parent]['config']))
                and origin['parent_day'] == row['tags']['branch_day']
                and re.fullmatch('[a-f0-9]{64}',origin['parent_state_semantic_sha256']), 'external assigned branch identity differs')

    def guard(self, *, now=None):
        from dams_sim.runtime import parse_deadline
        now = time.time() if now is None else now
        require(digest(canonical(self.value)) == self.value_fingerprint
                and digest(canonical(self.proofs)) == self.proof_fingerprint
                and digest(canonical(self.assignment_scopes)) == self.assignment_scope_fingerprint,
                'acceptance expectations/proofs changed in memory')
        require(parse_deadline(self.value['issued_utc']) <= now + 5
                and now < parse_deadline(self.value['expires_utc']) <= parse_deadline(self.limits_value['deadline_utc']),
                'acceptance authority expired/future or extends original deadline')
        for p,native in self.references:
            require(s.native(s.safe_path(p).lstat()) == native, 'acceptance reference changed; invalidate registry')
        for p,native in self.source_native.items():
            require(s.native(s.safe_path(p).lstat()) == native, 'operation/original source changed')
        require(digest(Path(__file__).read_bytes()) == IMPORT_SHA, 'loaded operation/source mismatch')
        s.check_science()
        require(digest((ROOT/'dams_sim/longitudinal_pipeline.py').read_bytes()) == LP_SHA
                and digest((ROOT/'research_tools/validate_longitudinal.py').read_bytes()) == RAW_SHA,
                'original pipeline/raw reader source changed')
        require(digest((ROOT/'research_tools/original_longitudinal_selector.py').read_bytes())
                == SELECTOR_SHA
                and digest((ROOT/'research_tools/frozen_science_cloud.py').read_bytes())
                == FSC_SHA,
                'loaded selector/preservation source differs from sealed R5')
        for module in (original_pipeline, sys.modules['dams_sim.scheduler'], original_validator, sys.modules['dams_sim.runtime']):
            require(Path(module.__file__).absolute().is_relative_to(ROOT), 'cached original code is outside this frozen package')

    def assigned(self, row):
        require(row['case_id'] in self.rows and canonical(row) == canonical(self.rows[row['case_id']]),
                'operation encounters unassigned/changed original inventory row')

    def expected(self, cid, default=None):
        self.guard(); require(cid in self.rows, 'unknown case cannot be accepted')
        return self.cases[cid]['expected_provenance'] if cid in self.cases else (default or self.limits_value['provenance'])

    def bind_case(self, row, attempt, origin=None):
        self.guard(); self.assigned(row)
        cid = row['case_id']
        if cid not in self.cases: return self.expected(cid)
        record = self.cases[cid]; attempt = s.safe_path(attempt)
        require(attempt == self.root/record['attempt_relative'], 'external raw attempt differs from current custody')
        require(origin is None or branch_identity(origin) == record['branch_identity'], 'external inspected branch differs')
        raw, _ = s.read_pin(attempt/'manifest.json',record['manifest_pin']['sha256'])
        m = s.parse(raw)
        require(m['status'] == 'complete' and type(m['exit_code']) is int and m['exit_code'] == 0
                and m['scientific_case_id'] == cid and m['config_sha256'] == record['config_sha256']
                and canonical(m['config']) == canonical(row['config'])
                and m['source_sha256'] == s.CORE and m['pipeline_driver_sha256'] == s.DRIVER
                and m.get('branch_origin') == record['branch_identity']
                and m.get('execution_provenance') == record['expected_provenance']
                and isinstance(m.get('platform'),str) and m['platform'].startswith('Linux-')
                and m['git_commit'] == self.proofs[cid][3]['commit'], 'actual external manifest is not truthful accepted evidence')
        names = set(m['output_sha256']) | {'manifest.json'}
        require(len(names) == 13 and set(record['raw_native']) == names
                and {p.name for p in attempt.iterdir()} == names,
                'external original full13 roster changed or has dynamic/WAL evidence')
        require(self.proofs[cid][0]['raw_files_sha256'] == {**m['output_sha256'],'manifest.json':record['manifest_pin']['sha256']},
                'Root full raw receipt binds other content')
        for name,native in record['raw_native'].items():
            require(isinstance(native,list) and len(native) == 7 and all(type(x) is int for x in native)
                    and stat.S_ISREG(native[5]) and native[6] == 1
                    and Path(name).name == name and s.native(s.safe_path(attempt/name).lstat()) == native,
                    'external raw current native custody changed; full gate required again')
        return record['expected_provenance']


class OperationalScheduler(Scheduler):
    def __init__(self, *args, registry, **kwargs):
        super().__init__(*args,**kwargs); self.registry = registry
        require(digest(canonical(self.limits.to_dict())) == digest(canonical(__import__('dams_sim.runtime',fromlist=['RuntimeLimits']).RuntimeLimits.from_dict(registry.limits_value).to_dict())),
                'operational scheduler changes original runtime/caps/retries')

    def _inspect(self, config, branch_origin=None):
        cid = case_key(config); self.registry.guard()
        require(cid in self.registry.rows and canonical(config.to_dict()) == canonical(self.registry.rows[cid]['config']),
                'scheduler configuration is outside original assignment')
        if cid not in self.registry.cases:
            return super()._inspect(config,branch_origin)
        record = self.registry.cases[cid]
        self.registry.bind_case(self.registry.rows[cid],self.root/record['attempt_relative'],branch_origin)
        original_limits = self.limits
        try:
            self.limits = dataclasses.replace(original_limits,provenance=self.registry.expected(cid))
            # True ORIGINAL inspection and verify_outputs; no accepted-result shortcut.
            return super()._inspect(config,branch_origin)
        finally:
            self.limits = original_limits
            self.registry.guard()
            self.registry.bind_case(self.registry.rows[cid],self.root/record['attempt_relative'],branch_origin)


def reader_classes(registry):
    class CaseReader(original_validator.CheckedLongitudinalCases):
        def validate_case(self,row,attempt,*,expected_provenance=None,config_history=()):
            expectation = registry.bind_case(row,attempt)
            try:
                return super().validate_case(row,attempt,expected_provenance=expectation,config_history=config_history)
            finally:
                registry.bind_case(row,attempt)
    class StudyReader(original_validator.CheckedLongitudinalStudy):
        def _case(self,row,reference,expected_provenance,config_history=()):
            expectation = registry.bind_case(row,self.root/reference['attempt'])
            try:
                return super()._case(row,reference,expectation,config_history)
            finally:
                registry.bind_case(row,self.root/reference['attempt'])
    return CaseReader,StudyReader


def build_namespace(registry):
    """Visible dependency injection in a NEW namespace, never a hot patch.

    Four exact AST adaptations are fenced by the original full source SHA.
    All control flow, branch/CSV/journal checks, Model worker, queue, intervals,
    budgets and locked-runtime code are left as the verified original source.
    """
    registry.guard()
    raw = s.read_pin(ROOT/'dams_sim/longitudinal_pipeline.py',LP_SHA)[0]
    tree = ast.parse(raw); original_AST = ast.dump(tree,include_attributes=False); changes = []
    class MetadataDependencies(ast.NodeTransformer):
        function = None
        def visit_FunctionDef(self,node):
            old = self.function; self.function = node.name
            node = self.generic_visit(node); self.function = old; return node
        def visit_ImportFrom(self,node):
            if node.module == 'research_tools.validate_longitudinal' and len(node.names) == 1:
                target = node.names[0].name
                if (self.function,target) == ('stopped_case_census','CheckedLongitudinalCases'):
                    changes.append('stopped_case_census.raw_reader_factory')
                    return ast.copy_location(ast.Assign([ast.Name(target,ast.Store())],ast.Name('operational_case_reader',ast.Load())),node)
                if (self.function,target) == ('run_longitudinal_pipeline','validate_longitudinal_output'):
                    changes.append('run_longitudinal_pipeline.final_raw_reader_factory')
                    return ast.copy_location(ast.Assign([ast.Name(target,ast.Store())],ast.Name('operational_validate_output',ast.Load())),node)
            return node
        def visit_Call(self,node):
            node = self.generic_visit(node)
            if self.function == 'stopped_case_census':
                if (isinstance(node.func,ast.Attribute) and node.func.attr == 'items'
                    and ast.dump(node.func.value,include_attributes=False) == ast.dump(ast.parse('(expected_provenance or {})',mode='eval').body,include_attributes=False)):
                    node.func.value = ast.Call(ast.Name('provenance_for_case',ast.Load()),[ast.Name('key',ast.Load()),ast.Name('expected_provenance',ast.Load())],[])
                    changes.append('stopped_case_census.manifest_expected_provenance')
                if isinstance(node.func,ast.Attribute) and node.func.attr == 'validate_case':
                    for k in node.keywords:
                        if k.arg == 'expected_provenance':
                            require(isinstance(k.value,ast.Name) and k.value.id == 'expected_provenance','original census ABI changed')
                            k.value = ast.Call(ast.Name('provenance_for_case',ast.Load()),[ast.Name('key',ast.Load()),ast.Name('expected_provenance',ast.Load())],[])
                            changes.append('stopped_case_census.raw_expected_provenance')
            return node
    tree = MetadataDependencies().visit(tree); ast.fix_missing_locations(tree)
    require(len(changes) == 4 and len(set(changes)) == 4,'unexpected original pipeline adaptation count')
    reversed_changes = []
    class ReverseMetadataDependencies(ast.NodeTransformer):
        def visit_Assign(self,node):
            if len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and isinstance(node.value,ast.Name):
                pair=(node.targets[0].id,node.value.id)
                if pair in (('CheckedLongitudinalCases','operational_case_reader'),
                            ('validate_longitudinal_output','operational_validate_output')):
                    reversed_changes.append(pair)
                    return ast.ImportFrom('research_tools.validate_longitudinal',
                                          [ast.alias(pair[0],None)],0)
            return self.generic_visit(node)
        def visit_Call(self,node):
            if (isinstance(node.func,ast.Attribute) and node.func.attr=='items'
                and isinstance(node.func.value,ast.Call)
                and isinstance(node.func.value.func,ast.Name) and node.func.value.func.id=='provenance_for_case'):
                require(ast.dump(node.func.value,include_attributes=False)==ast.dump(ast.parse('provenance_for_case(key, expected_provenance)',mode='eval').body,include_attributes=False),
                        'manifest metadata adaptation drift')
                node.func.value=ast.parse('(expected_provenance or {})',mode='eval').body
                reversed_changes.append('manifest expectation')
            if isinstance(node.func,ast.Attribute) and node.func.attr=='validate_case':
                for k in node.keywords:
                    if k.arg=='expected_provenance' and isinstance(k.value,ast.Call):
                        require(ast.dump(k.value,include_attributes=False)==ast.dump(ast.parse('provenance_for_case(key, expected_provenance)',mode='eval').body,include_attributes=False),
                                'raw reader metadata adaptation drift')
                        k.value=ast.Name('expected_provenance',ast.Load())
                        reversed_changes.append('raw expectation')
            return self.generic_visit(node)
    reverse=ReverseMetadataDependencies().visit(copy.deepcopy(tree))
    require(len(reversed_changes)==4 and ast.dump(reverse,include_attributes=False)==original_AST,
            'operational factory changes original AST outside four metadata dependencies')
    namespace = {'__name__':'dams_sim._explicit_operational_return_r5','__package__':'dams_sim',
                 '__file__':str(ROOT/'dams_sim/longitudinal_pipeline.py')}
    exec(compile(tree,'original_longitudinal_return_r5::verified-original-pipeline+4-metadata-adaptations','exec'),namespace)
    CaseReader,StudyReader = reader_classes(registry)
    def validate_output(output,spec=None,scale=None,**kwargs):
        registry.guard(); require(s.safe_path(output) == registry.root,'registry/final study output differs')
        checked = StudyReader(output,**kwargs)
        require((spec is None or checked.spec.name == spec) and (scale is None or checked.spec.n == scale),
                'requested original spec/scale differs')
        registry.guard(); return checked.result()
    namespace.update(Scheduler=lambda *a,**k:OperationalScheduler(*a,registry=registry,**k),
                     operational_case_reader=CaseReader,operational_validate_output=validate_output,
                     provenance_for_case=registry.expected)
    namespace['_operational_metadata_adaptations'] = changes
    namespace['_explicit_operational_source_provenance'] = {
        'actual_operator_source_sha256':IMPORT_SHA,'scientific_pipeline_reference_sha256':LP_SHA,
        'scientific_driver_reference_sha256':s.DRIVER,'original_raw_reader_reference_sha256':RAW_SHA,
        'four_metadata_adaptations_reverse_AST_exact':True,
        'private_namespace_AST_sha256':digest(ast.dump(tree,include_attributes=False).encode()),
        'not_unchanged_original_CLI_execution':True}
    return namespace


def census(output,inventory,identity,*,expected_provenance=None,registry=None):
    if registry is None:
        return original_pipeline.stopped_case_census(output,inventory,identity,expected_provenance=expected_provenance)
    require(s.safe_path(output) == registry.root,'registry census root differs')
    for row in inventory: registry.assigned(row)
    require(len({r['case_id'] for r in inventory}) == len(inventory),'census repeats case identities')
    value = build_namespace(registry)['stopped_case_census'](output,inventory,identity,expected_provenance=expected_provenance)
    registry.guard(); return value


def invoke_pipeline(name,scale,output,runtime_path=None,*,registry=None):
    if registry is None:
        return original_pipeline.run_longitudinal_pipeline(name,scale,output,runtime_path)
    require(name == 'longitudinal-adoption-5y' and scale == 1000 and s.safe_path(output) == registry.root,
            'registry cannot create a different study/output')
    require(runtime_path is not None and digest(s.read_pin(runtime_path,s.RUNTIME,bound=65536)[0]) == s.RUNTIME,
            'new operation cannot override original runtime bytes')
    registry.guard()
    value = build_namespace(registry)['run_longitudinal_pipeline'](name,scale,output,runtime_path)
    registry.guard()
    return {**value,'operational_return_adapter_sha256':IMPORT_SHA,
            'acceptance_registry_sha256':registry.registry_sha256,'original_scientific_source_sha256':s.CORE}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True); p.add_argument('--runtime-limits',type=Path,required=True)
    p.add_argument('--registry-pin'); p.add_argument('--authority-pin'); p.add_argument('--inventory-pin')
    p.add_argument('--execute-native-pipeline',action='store_true')
    a = p.parse_args(argv); registry = None
    if a.registry_pin:
        require(a.authority_pin and a.inventory_pin,'fixed Root authority/original inventory pins missing')
        runtime_raw = s.read_pin(a.runtime_limits,s.RUNTIME,bound=65536)[0]
        registry = Registry(a.output,json.loads(a.registry_pin),json.loads(a.authority_pin),json.loads(a.inventory_pin),
                            {'path':str(a.runtime_limits),'sha256':s.RUNTIME,'bytes':len(runtime_raw)})
    if a.execute_native_pipeline:
        print(json.dumps(invoke_pipeline('longitudinal-adoption-5y',1000,a.output,a.runtime_limits,registry=registry)))
    else:
        print(json.dumps({'status':'OPERATIONAL_METADATA_ONLY','registry_bound':registry is not None,
                          'authority_issued_by_this_script':False,'Model_executions':0,'provider_permission':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
