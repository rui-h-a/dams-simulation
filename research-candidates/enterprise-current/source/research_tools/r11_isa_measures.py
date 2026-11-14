"""Per-arm measures for judging the automation / nested-sandbox proposition (new file; changes no existing output).

    python -m research_tools.r11_isa_measures ARM_DIR [ARM_DIR ...]   # prints JSON, writes isa-measures.json
                                                                      # only for arms that carry an automation block

Groups: governance cost (member-hours and processing units kept apart), collaboration (cross-sandbox
requests), structural change, scaling, safety, governance quality.  Rates use realised member-years.
Processing units are not member time and are not debited from cash; `processing_cost_at_declared_rate`
applies the declared conversion only for reporting.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def measures(arm_dir):
    arm_dir = Path(arm_dir)
    s = json.loads((arm_dir/'summary.json').read_text())
    arm = json.loads((arm_dir/'arm.json').read_text())
    auto = arm['config'].get('automation')
    if auto:
        from dams_sim.automation import AutomationConfig
        auto = AutomationConfig.from_dict(auto).to_dict()
    with (arm_dir/'timeseries.csv').open() as f:
        rows = list(csv.DictReader(f))
    g = lambda k: s.get(k, 0.) or 0.
    years = s['active_member_calendar_days']/365.25
    per = lambda v: v/years if years else None
    member_hours = sum(g(k) for k in ('review_hours', 'appeal_hours', 'governance_hours', 'organization_verification_hours'))
    requests, approved = g('sandbox_requests'), g('sandbox_requests_approved')
    auto_claims = g('automated_accepts')+g('automated_rejects')+g('exception_claims')
    processed = g('automated_verifications')
    out = {
        'arm': arm['arm'], 'member_years': years,
        'governance_cost': {
            'member_hours_per_member_year': per(member_hours),
            'review_hours': g('review_hours'), 'appeal_hours': g('appeal_hours'), 'governance_hours': g('governance_hours'),
            'processing_units_automated': g('automated_processing_units'),
            'processing_units_per_member_year': per(g('automated_processing_units')),
            'witness_verification_units_per_member_year': per(g('verification_resource_units')),
            'processing_cost_at_declared_rate_per_member_year': per(g('automated_processing_units')*auto['resource_units_per_processing_unit']) if auto else None,
            'exception_claims': g('exception_claims'),
            'exception_routes': {k: g('exception_'+k) for k in ('sampled', 'uncertain', 'quota', 'outage')},
            'exception_share_of_automated_stream': g('exception_claims')/auto_claims if auto_claims else None},
        'collaboration': {
            'cross_sandbox_requests': requests, 'approved': approved, 'rejected': requests-approved,
            'rejection_rate': (requests-approved)/requests if requests else None,
            'mean_approval_latency_days': g('sandbox_approval_latency_days')/approved if approved else None,
            'borrowed_quota_units': g('sandbox_borrowed_units'),
            'reviews_deferred_for_read_grant': g('sandbox_read_deferred_records')},
        'structural_change': {k: g(k) for k in ('sandboxes_created', 'sandboxes_split', 'sandboxes_merged', 'sandboxes_closed')},
        'scaling': {
            'produced_work_units_per_member_year': per(g('produced_work_units')),
            'confirmed_records_per_member_year': per(g('confirmed_records')),
            'output_per_available_member_hour': s.get('produced_work_per_available_member_hour'),
            'active_members_final': s.get('active_members_final'),
            'review_backlog_at_end': float(rows[-1]['review_backlog_records']) if rows else None,
            'confirmation_mean_days': s.get('confirmation_mean_days')},
        'safety': {
            'blocked_privacy_reads': g('privacy_blocks'), 'blocked_core_modifications': g('core_blocks'),
            'self_modification_adoptions': g('selfmod_adoptions'), 'self_modification_rollbacks': g('selfmod_rollbacks'),
            'faults_injected': g('faults_injected'), 'faults_detected': g('faults_detected'), 'faults_recovered': g('faults_recovered'),
            'fault_containment': s.get('fault_containment')},
        'governance_quality': {
            'decision_regret_per_available_member_hour': s.get('decision_regret_per_available_member_hour'),
            'decision_regret_units': g('decision_regret_units'),
            'automated_false_accepts': g('auto_false_accepts'), 'automated_false_rejects': g('auto_false_rejects'),
            'automated_verifications': processed,
            'honest_records_rejected': g('honest_records_rejected'),
            'fraudulent_records_accepted': g('fraudulent_records_accepted')},
        'sandbox_usage_attribution': s.get('sandbox_usage'),
    }
    return out, bool(auto)


def main():
    result = {}
    for d in sys.argv[1:]:
        m, has_auto = measures(d)
        result[str(d)] = m
        if has_auto:
            (Path(d)/'isa-measures.json').write_text(json.dumps(m, indent=1, sort_keys=True))
    print(json.dumps(result, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
