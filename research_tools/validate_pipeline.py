"""Read-only shared publication/cloud gate. Never starts a Model or cloud resource."""
from __future__ import annotations
import json
from pathlib import Path
from dams_sim.spec import resolve_spec
from dams_sim.storage import canonical,digest,file_digest
from research_tools.analysis_v2 import CheckedStudy


def _required_publication_outputs(spec):
    required={'generated/analysis/claims.json','generated/analysis/allocation_effects.csv',
              'generated/analysis/dynamics.json'}
    required.update('generated/revision_'+name+'.tex' for name in
                    ('dynamic','stress','scale','recovery','results','macros'))
    figures=['allocation-effects','governance-dynamics']
    if 'stress' in spec.stages:
        figures.append('attack-cost-effects');required.add('generated/analysis/attack_effects.csv')
    if 'scenarios' in spec.stages:required.add('generated/analysis/context_effects.csv')
    if 'sensitivity' in spec.stages:
        figures.extend(('response-boundaries','sensitivity-screen'))
        required.update('generated/analysis/'+name+'.csv' for name in
                        ('boundary_effects','elementary_effects','sensitivity_screen'))
    if 'extended' in spec.stages:
        figures.append('shock-recovery')
        required.update('generated/analysis/'+name+'.csv' for name in ('scale_effects','shock_recovery_times'))
        required.add('generated/analysis/shock_trajectories.json')
    required.update('figures/results/'+name+'.'+ext for name in figures for ext in ('pdf','svg'))
    return required


def validate_pipeline_output(output,spec=None,scale=None,*,expected_provenance=None):
    """Require a complete current-source study and return exact case origins.

    The independent CheckedStudy validates planned inventories, every child,
    raw state population/config/horizon, complete output rosters and every byte
    hash, aggregate cells, paired primary values and analysis inputs. This gate
    adds final completion counts/publication evidence and optional cloud origin
    binding. An unsigned retained manifest is integrity evidence, not proof
    against someone able to replace both manifests and data.
    """
    study=CheckedStudy(output)
    out=study.root;pipeline=study.pipeline
    requested=resolve_spec(spec or study.spec.name,scale if scale is not None else study.spec.n)
    if canonical(study.spec.to_dict())!=canonical(requested.to_dict()):
        raise ValueError('completed output differs from requested spec/scale')
    expected_stages=[*study.spec.stages,'analysis']
    if [r['stage'] for r in pipeline['stages']]!=expected_stages or any(r['status']!='complete' for r in pipeline['stages']):
        raise ValueError('complete pipeline roster differs')
    logical=sum(len(v) for v in study.stages.values())
    if pipeline['unique_complete_cases']!=len(study.case_cache) or pipeline['logical_case_rows']!=logical:
        raise ValueError('pipeline unique/logical completed case counts differ')
    generation=out/'publication/generated/analysis/generation_manifest.json'
    if pipeline['publication_generation_manifest_sha256']!=file_digest(generation):
        raise ValueError('publication generation manifest differs')
    if pipeline['publication_log_sha256']!=file_digest(out/'publication-analysis.log'):
        raise ValueError('publication log differs')
    publication=json.loads(generation.read_text())
    # The publication generator audits while the producer is still running;
    # the complete pipeline is the final authoritative state, not this flag.
    if publication.get('status')!='complete':raise ValueError('publication is not complete')
    for field in ('source_sha256','pipeline_driver_sha256','spec_sha256'):
        if publication.get(field)!=study.identity[field]:raise ValueError('publication identity differs: '+field)
    if canonical(publication.get('scientific_spec'))!=canonical(study.spec.to_dict()):
        raise ValueError('publication scientific spec differs')
    if publication.get('analysis_entrypoint_sha256')!=pipeline['publication_analysis_driver_sha256']:
        raise ValueError('publication analyzer entrypoint differs')
    repo=Path(__file__).resolve().parents[1]
    for field,path in (('analysis_driver_sha256',repo/'research_tools/analysis_v2.py'),
                       ('analysis_entrypoint_sha256',repo/'research_tools/analyze.py'),
                       ('recovery_checker_sha256',repo/'research_tools/recovery_checks.py'),
                       ('dependency_lock_sha256',repo/'uv.lock')):
        if publication.get(field)!=file_digest(path):
            raise ValueError('publication postprocessor/lock differs from current source: '+field)
    if publication.get('figure_style',{}).get('module_sha256')!=file_digest(repo/'research_tools/figure_style.py'):
        raise ValueError('publication figure style differs from current source')
    excluded={out/'pipeline_manifest.json'}
    if publication.get('pipeline_status_at_generation')=='running':excluded.add(out/'report.md')
    expected_inputs={str(Path(name).relative_to(out)):sha for name,sha in study.inputs.items() if Path(name) not in excluded}
    if publication.get('inputs')!=expected_inputs:
        raise ValueError('publication input inventory differs from independently checked scientific inputs')
    declared=publication.get('outputs',{})
    if not isinstance(declared,dict) or not _required_publication_outputs(study.spec).issubset(declared):
        raise ValueError('publication required artifact inventory is incomplete')
    publication_root=out/'publication'
    actual={str(p.relative_to(publication_root)) for p in publication_root.rglob('*') if p.is_file() and p!=generation}
    if actual!=set(declared):raise ValueError('publication physical output roster differs from declared inventory')
    for group,base in (('inputs',out),('outputs',out/'publication')):
        hashes=publication.get(group)
        if not isinstance(hashes,dict) or not hashes:raise ValueError('publication '+group+' inventory missing')
        for name,sha in hashes.items():
            path=base/name
            if not path.resolve().is_relative_to(base.resolve()) or path.is_symlink() or not path.is_file() or file_digest(path)!=sha:
                raise ValueError('publication '+group+' integrity mismatch: '+name)
    origins=[];inventory={}
    for stage in study.stages:
        manifest=json.loads((out/stage/'manifest.json').read_text())
        inventory[stage]=manifest['inventory_sha256']
    for path,(config,summary) in sorted(study.case_cache.items()):
        child=Path(path);manifest=json.loads((child/'manifest.json').read_text())
        origin=manifest.get('execution_provenance',{})
        if expected_provenance:
            for field,expected in expected_provenance.items():
                if origin.get(field)!=expected:raise ValueError('actual child execution provenance differs: '+field)
            if any(k in expected_provenance for k in ('project_hash','task_hash','instance_id')):
                if any(not origin.get(k) for k in ('instance_id','machine_type','zone','project_hash','packaged_commit','task_hash')):
                    raise ValueError('completed world lacks required actual cloud origin')
                if manifest['git_dirty'] or manifest['git_commit']!=origin['packaged_commit']:
                    raise ValueError('completed cloud world lacks clean matching fixed source')
        origins.append({'case_id':manifest['scientific_case_id'],'attempt':str(child.relative_to(out)),
                        'world':config['world'],'n':config['n'],'days':config['days'],
                        'regime':config['regime'],'backend':config['backend'],
                        'source_sha256':manifest['source_sha256'],'config_sha256':manifest['config_sha256'],
                        'git_commit':manifest['git_commit'],'git_dirty':manifest['git_dirty'],
                        'execution_provenance':origin,'manifest_sha256':file_digest(child/'manifest.json')})
    study.guard()
    return {'validation':True,'spec_sha256':study.spec.sha256,'source_sha256':study.identity['source_sha256'],
            'pipeline_driver_sha256':study.identity['pipeline_driver_sha256'],
            'validator_sha256':file_digest(Path(__file__)),
            'independent_validator_sha256':file_digest(Path(__file__).with_name('analysis_v2.py')),
            'unique_complete_cases':len(origins),'logical_case_rows':logical,
            'independent_primary_worlds':study.worlds,'inventory_sha256':inventory,
            'case_origins':origins,'case_origins_sha256':digest(canonical(origins)),
            'publication_generation_manifest_sha256':file_digest(generation),'pipeline_status':'complete'}
