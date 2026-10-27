"""Yearly post-adoption series of one arm from its timeseries.csv (cumulative columns differenced at year ends)."""
import csv, math


def yearly(path, pre_years=2):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    g = lambda r, c: float(r[c])
    start = round(365.25*pre_years)
    years = (len(rows)-start)/365.25
    out = []
    for y in range(1, int(math.floor(years+1e-9))+1):
        # cumulative columns include the row's own day: year y spans rows (ia, ib]
        ia, ib = start+round(365.25*(y-1))-1, min(len(rows), start+round(365.25*y))-1
        a, b = rows[ia], rows[ib]
        my = (g(b, 'active_member_calendar_days_cumulative')-g(a, 'active_member_calendar_days_cumulative'))/365.25
        hrs = g(b, 'available_member_work_hours_cumulative')-g(a, 'available_member_work_hours_cumulative')
        net = (g(b, 'revenue_resource_units_cumulative')-g(a, 'revenue_resource_units_cumulative')
               - (g(b, 'operating_resource_units_cumulative')-g(a, 'operating_resource_units_cumulative')))
        gov = sum(g(b, c)-g(a, c) for c in ('review_hours_cumulative', 'appeal_hours_cumulative', 'governance_hours_cumulative'))
        out.append({'year': y, 'member_years': my,
                    'net_per_member_year': net/my if my else None,
                    'regret_per_hour': (g(b, 'decision_regret_units_cumulative')-g(a, 'decision_regret_units_cumulative'))/hrs if hrs else None,
                    'output_per_hour': (g(b, 'produced_work_units_cumulative')-g(a, 'produced_work_units_cumulative'))/hrs if hrs else None,
                    'review_governance_hours_per_member_year': gov/my if my else None,
                    'review_backlog_per_member_end': g(b, 'review_backlog_records')/max(1., g(b, 'active_members')),
                    'active_members_end': g(b, 'active_members')})
    return out
