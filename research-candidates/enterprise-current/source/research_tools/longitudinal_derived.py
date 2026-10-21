"""Dependency-free descriptive publication recipes from validated raw evidence.

These helpers never execute a simulation. They do not independently certify the
model's causal mechanism. Means use math.fsum; percentiles use linear order-
statistic interpolation, matching the declared empirical distribution bands.
"""
from collections import Counter
from datetime import date, timedelta
import json
import math
from pathlib import Path
import statistics

from dams_sim.longitudinal import anniversary
from dams_sim.storage import file_digest

IMPORTED_MODULE_SHA256 = file_digest(Path(__file__))
METRICS = ('active_members', 'cash_resource_units', 'review_backlog_records')

LIMITATIONS = ["Uncalibrated synthetic conditional comparison", "Approximate Student-t conditional continuous coverage",
               "Closure uses conservative exact discordance intervals", "Undefined worlds are never dropped",
               "Formal allocation does not measure real influence", "Synthetic memory does not measure innovation"]
DERIVED_DEFINITIONS = {
    "independent_unit": "paired world; cohort members and calendar days are descriptive within-world observations",
    "formal_allocation": "First recorded positive formal share before competing events; equal fallback or hierarchy may assign it immediately. No real influence claim.",
    "positive_delay": "Calendar days, conditional on the earliest event being positive; full risk-set outcomes and day ties accompany the median.",
    "competing_events": "Earliest recorded positive allocation, departure/retirement/layoff, closure or suspension wins. Same-day priority: positive allocation, departure, closure, suspension; positive-departure ties remain counted. Administrative censoring is separate.",
    "memory": "Equal declared-domain synthetic state, including merged source-domain labels; no member-weighted or innovation interpretation. Departure memory loss includes exit, retirement and layoff.",
    "exposure": "Recorded person counters; present hours use fixed care hours and actual present days. No initial N times elapsed-time denominator.",
    "cash": "Synthetic resource balance includes costs; paired differences are not empirical profit or calibrated ROI.",
    "decision_regret": "Total recorded regret, including unresolved-decision losses, divided by completed decisions; undefined when no decisions completed. This is not regret only among completed decisions.",
    "trajectory_summary": "All-assigned paired arithmetic means use math.fsum/n. Empirical percentiles use sorted observations at h=(n-1)q with linear interpolation; bands are world distributions, not confidence intervals.",
    "relative_adoption": "Actual last-guild adoption anchors the matched treatment and control at the same Gregorian dates. All assigned worlds stay in coverage; a missing anchor or day makes the all-world daily mean undefined. No adopter-only effect is substituted.",
}



def allocation_outcome(person, state):
    """Earliest event wins; same-day positive/departure ties stay visible."""
    first, exited = person["first_positive_authority_day"], person["exited_day"]
    events = [(first, 0, "positive-formal-allocation"),
              (exited, 1, "competing-" + (person["exit_reason"] or "departure")),
              (state["closed_day"], 2, "competing-organization-closure"),
              (state["suspended_day"], 3, "competing-organization-suspension")]
    events = [event for event in events if event[0] is not None]
    if not events:
        return "administratively-censored", None, False
    day, _, outcome = min(events)
    positive = outcome == "positive-formal-allocation"
    return outcome, day - person["entered_day"] if positive else None, positive and first == exited


def derive_case(case, envelope, journal):
    """Small within-world cohort summaries from complete state and event raw."""
    state = envelope["state"]
    founding = set()
    memory_integral = memory_initial = memory_final = None
    routine_integral = routine_initial = routine_final = None
    memory_days = 0
    lost_memory = 0.0
    for day, kind, _event, payload in journal:
        value = json.loads(payload)
        if kind == "entry" and value["initial"]:
            founding.add(value["person"])
        elif kind in ("exit", "retirement", "layoff"):
            lost_memory += value["memory_loss"]
        elif kind == "day_end":
            # This explicitly uses equal declared-domain weighting. Merged
            # source domains remain separate labelled states, not new samples.
            memory = statistics.fmean(value["memory"].values())
            routine = statistics.fmean(value["routine"].values())
            if memory_initial is None:
                memory_initial, routine_initial = memory, routine
                memory_integral = routine_integral = 0.0
            memory_integral += memory
            routine_integral += routine
            memory_final, routine_final = memory, routine
            memory_days += 1
    if memory_days != case["config"].days:
        raise ValueError("derived memory journal horizon differs")
    if not founding or not founding.issubset({p["id"] for p in state["people"]}):
        raise ValueError("founding cohort is absent from retained entry evidence")
    agent_by_id = {a["id"]: a for a in state["agents"]}
    result = []
    for cohort in ("founding", "entrant"):
        people = [p for p in state["people"] if (p["id"] in founding) == (cohort == "founding")]
        counts = Counter()
        delays = []
        ties = 0
        for person in people:
            outcome, delay, tie = allocation_outcome(person, state)
            counts[outcome] += 1
            ties += tie
            if delay is not None:
                delays.append(delay)
        result.append({
            "case_id": case["case_id"], "world": case["config"].world,
            "context": case["tags"]["context"], "arm_id": case["tags"]["arm_id"],
            "cohort": cohort, "assigned_people": len(people),
            "positive_formal_allocations": counts["positive-formal-allocation"],
            "positive_and_departure_same_day": ties,
            "allocation_outcome_counts_json": json.dumps(dict(sorted(counts.items())), sort_keys=True),
            "positive_delay_median_calendar_days_conditional": statistics.median(delays) if delays else None,
            "active_member_calendar_days": sum(p["active_calendar_days"] for p in people),
            "active_member_workdays": sum(p["active_workdays"] for p in people),
            "present_member_workdays": sum(p["present_workdays"] for p in people),
            "available_member_work_hours": sum(p["available_work_hours"] for p in people),
            "present_member_work_hours": math.fsum(p["present_workdays"] * (1 - agent_by_id[p["id"]]["care_hours"]) for p in people),
            "whole_case_equal_declared_domain_memory_day_integral": memory_integral,
            "whole_case_equal_declared_domain_memory_first_day": memory_initial,
            "whole_case_equal_declared_domain_memory_final": memory_final,
            "whole_case_equal_declared_domain_routine_day_integral": routine_integral,
            "whole_case_equal_declared_domain_routine_first_day": routine_initial,
            "whole_case_equal_declared_domain_routine_final": routine_final,
            "whole_case_departure_memory_loss_units": lost_memory,
            "closed_day": state["closed_day"], "suspended_day": state["suspended_day"],
            "scope": "within-world descriptive synthetic state; people are not independent Monte Carlo worlds",
        })
    return result



def _percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _estimates(values):
    return math.fsum(values) / len(values), _percentile(values, .025), _percentile(values, .975)


def common_trajectory_data(checked, contrast, case_map):
    """Return exact paired observations and all-world Gregorian summaries."""
    from itertools import zip_longest
    worlds = checked.protocol['confirmation_world_ids']
    if not worlds:
        raise ValueError('trajectory has no assigned worlds')
    paired, adoption_days, never = [], [], 0
    for world in worlds:
        treatment = case_map[contrast['treatment_arm'], world]
        reference = case_map[contrast['reference_arm'], world]
        values = []
        for day, (left, right) in enumerate(zip_longest(checked.iter_timeseries(treatment['case_id']), checked.iter_timeseries(reference['case_id']))):
            if day >= checked.spec.common_end_day:
                break
            if left is None or right is None or left['day'] != day or right['day'] != day:
                raise ValueError('paired full-calendar trajectory differs')
            value = [left[metric] - right[metric] for metric in METRICS]
            if not all(type(v) in (int, float) and math.isfinite(v) for v in value):
                raise ValueError('nonfinite raw trajectory')
            values.append(value)
        if len(values) != checked.spec.common_end_day:
            raise ValueError('trajectory common horizon incomplete')
        anchors = list(treatment['summary']['adoption_days'].values())
        if not anchors or any(value is None for value in anchors):
            never += 1
        else:
            adoption_days.append(max(anchors))
        paired.append(values)
    origin = date.fromisoformat(checked.spec.calendar_start)
    age = case_map[contrast['treatment_arm'], worlds[0]]['config'].longitudinal.organization_initial_age_years
    rows = []
    for column, metric in enumerate(METRICS):
        for day in range(checked.spec.common_end_day):
            mean, low, high = _estimates([values[day][column] for values in paired])
            rows.append({'contrast_id': contrast['contrast_id'], 'day': day,
                         'calendar_date': (origin + timedelta(days=day)).isoformat(),
                         'metric': metric, 'assigned_worlds': len(worlds),
                         'mean_paired_difference': mean, 'world_distribution_p025': low,
                         'world_distribution_p975': high, 'initial_organization_age_years': age,
                         'elapsed_calendar_days': day, 'never_fully_adopted_worlds': never})
    return {'paired': paired, 'rows': rows, 'adoption_days': adoption_days,
            'never_adopted_worlds': never, 'initial_organization_age_years': age}


def relative_trajectory_data(checked, contrast, case_map, paired):
    """Align observed days to actual adoption, preserving the full risk set."""
    worlds = checked.protocol['confirmation_world_ids']
    horizon = checked.spec.common_end_day
    if not worlds or len(paired) != len(worlds) or any(len(values) != horizon for values in paired):
        raise ValueError('relative trajectory paired world/horizon differs')
    if any(len(values) != len(METRICS) or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
           for world in paired for values in world):
        raise ValueError('relative trajectory contains invalid observations')
    origin = date.fromisoformat(checked.spec.calendar_start)
    anchors, coverage = [], []
    for world in worlds:
        case = case_map[contrast['treatment_arm'], world]
        days = list(case['summary']['adoption_days'].values())
        anchor = max(days) if days and all(day is not None for day in days) else None
        if anchor is not None and (type(anchor) is not int or anchor < 0):
            raise ValueError('relative trajectory invalid actual adoption day')
        anchors.append(anchor)
        adopted_on = origin + timedelta(days=anchor) if anchor is not None else None
        if adopted_on is not None:
            years = adopted_on.year - origin.year
            if anniversary(origin, years) > adopted_on:
                years -= 1
            begin, end = anniversary(origin, years), anniversary(origin, years + 1)
            age = case['config'].longitudinal.organization_initial_age_years + years + (adopted_on - begin).days / (end - begin).days
        else:
            age = None
        coverage.append({'contrast_id': contrast['contrast_id'], 'world': world,
                         'treatment_case_id': case['case_id'],
                         'reference_case_id': case_map[contrast['reference_arm'], world]['case_id'],
                         'actual_last_guild_adoption_day': anchor,
                         'actual_last_guild_adoption_calendar_date': adopted_on.isoformat() if adopted_on else None,
                         'organization_age_at_adoption_years': age,
                         'alignment_status': 'anchor-defined' if anchor is not None else 'never-fully-adopted',
                         'closure_day': case['summary']['closure_day'],
                         'suspension_day': case['summary']['suspension_day'],
                         'paired_common_calendar_days': horizon,
                         'relative_first_day': -anchor if anchor is not None else None,
                         'relative_last_day': horizon - 1 - anchor if anchor is not None else None})
    defined = [anchor for anchor in anchors if anchor is not None]
    days = list(range(-max(defined), horizon - min(defined))) if defined else [0]
    rows = []
    for day in days:
        observed = [values[day + anchor] for values, anchor in zip(paired, anchors)
                    if anchor is not None and 0 <= day + anchor < horizon]
        complete = len(observed) == len(worlds)
        for column, metric in enumerate(METRICS):
            mean, low, high = _estimates([values[column] for values in observed]) if complete else (None, None, None)
            rows.append({'contrast_id': contrast['contrast_id'], 'relative_calendar_day': day,
                         'metric': metric, 'assigned_worlds': len(worlds), 'defined_worlds': len(observed),
                         'never_fully_adopted_worlds': len(worlds) - len(defined),
                         'unavailable_calendar_worlds': len(defined) - len(observed),
                         'status': 'all-assigned-defined' if complete else 'undefined-incomplete-assigned-coverage',
                         'mean_paired_difference': mean, 'world_distribution_p025': low,
                         'world_distribution_p975': high})
    return {'rows': rows, 'coverage': coverage, 'days': days,
            'defined_anchors': defined, 'assigned_worlds': len(worlds)}
