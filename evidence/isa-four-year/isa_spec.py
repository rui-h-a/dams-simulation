"""The pre-specified design of the automation / nested-sandbox study, in one place.

The study prespecified seven arms, four primary contrasts with Holm
adjustment, secondary contrasts S1-S7, primary and secondary outcomes, the margin rule for contrast 3.  The collector,
stats.py, proposition.py, figures.py and make_tex.py import this module so that arm names, order, labels, contrasts and
outcome definitions cannot differ between them.  Nothing here reads data.
"""
from __future__ import annotations

REFERENCE = 'control-hierarchy'

# Seven arms in the order of the prespecification (and of every table and figure).
ARMS = ['control-hierarchy', 'dams-witness', 'hierarchy-auto-coord', 'dams-auto-coord', 'hierarchy-isa-coord',
        'dams-isa-central-coord', 'dams-isa-coord']

# Display names (exact).
NAME = {'control-hierarchy': 'Hierarchy', 'dams-witness': 'Manual DAMS', 'hierarchy-auto-coord': 'Automated hierarchy',
        'dams-auto-coord': 'Automated DAMS', 'hierarchy-isa-coord': 'Sandbox hierarchy',
        'dams-isa-central-coord': 'Sandbox DAMS, central record', 'dams-isa-coord': 'Sandbox DAMS'}
# Short forms for narrow axes (still unambiguous; never used where a full name fits).
SHORT = {'control-hierarchy': 'Hierarchy', 'dams-witness': 'Manual DAMS', 'hierarchy-auto-coord': 'Autom. hierarchy',
         'dams-auto-coord': 'Autom. DAMS', 'hierarchy-isa-coord': 'Sandbox hierarchy',
         'dams-isa-central-coord': 'Sandbox DAMS, central', 'dams-isa-coord': 'Sandbox DAMS'}
# Letters-only fragments for LaTeX macro names.
CAMEL = {'control-hierarchy': 'Hier', 'dams-witness': 'ManualDams', 'hierarchy-auto-coord': 'AutoHier',
         'dams-auto-coord': 'AutoDams', 'hierarchy-isa-coord': 'SandboxHier', 'dams-isa-central-coord': 'SandboxCentral',
         'dams-isa-coord': 'Sandbox'}

# (id, minuend, subtrahend, kind, description)  -- minuend minus subtrahend.
CONTRASTS = [
    ('P1', 'dams-isa-coord', 'control-hierarchy', 'primary', 'the complete design against the supervisory hierarchy'),
    ('P2', 'dams-isa-coord', 'hierarchy-auto-coord', 'primary', 'against a hierarchy with the same automation'),
    ('P3', 'dams-isa-central-coord', 'hierarchy-isa-coord', 'primary',
     'same automation, same scope rule, same central record; the authority rule alone differs'),
    ('P4', 'dams-auto-coord', 'hierarchy-auto-coord', 'primary',
     'authority rule and record at equal automation, without the scope rule'),
    ('S1', 'dams-isa-coord', 'dams-auto-coord', 'secondary', 'what the sandbox architecture adds under the same authority rule and record'),
    ('S2', 'dams-isa-coord', 'dams-isa-central-coord', 'secondary', 'what the witnessed record adds, and costs, over a central record'),
    ('S3', 'dams-isa-coord', 'hierarchy-isa-coord', 'secondary', 'the complete design against the strongest hierarchical competitor'),
    ('S4', 'hierarchy-isa-coord', 'hierarchy-auto-coord', 'secondary', 'what the sandbox architecture adds inside a hierarchy'),
    ('S5', 'dams-auto-coord', 'dams-witness', 'secondary', 'automation inside the contribution-based design'),
    ('S6', 'dams-witness', 'control-hierarchy', 'secondary', "the manual-review design under this model's accounting"),
    ('S7', 'hierarchy-auto-coord', 'control-hierarchy', 'secondary', 'automation inside the hierarchy'),
]
ATTRIBUTION = [('P3', 'Authority rule alone'), ('S1', 'Sandbox architecture under the DAMS rule'), ('S4', 'Sandbox architecture inside a hierarchy'),
               ('S2', 'Witnessed record'), ('S3', 'Complete design against the strongest hierarchy')]
PRIMARY_IDS = [c[0] for c in CONTRASTS if c[3] == 'primary']
CONTRAST_BY_ID = {c[0]: c for c in CONTRASTS}
CONTRAST_KEY = {c[0]: f'{c[1]}_minus_{c[2]}' for c in CONTRASTS}
ID_BY_KEY = {v: k for k, v in CONTRAST_KEY.items()}
CONTRAST3 = 'P3'
MARGIN_PERCENT = 2.0   # 2% of the control-hierarchy mean of the primary outcome (prespecification, interpretation rules)
HOLM_FAMILY_SIZE = 4

PRIMARY_OUTCOME = 'net_operating_result_post_per_member_year'

# Outcome registry.  name -> dict(label, unit, direction, basis, scale, places, group, definition)
#   basis  'post'  : from the branch day on, from the daily series (difference of cumulative columns, or mean of daily values)
#          'whole' : whole run (history included) because no cumulative column exists; the shared history is identical in every
#                    arm, so differences between arms are unaffected, levels are diluted by the shared years
#          'count' : total over the run; the shared history carries none (no automation, no sandbox, no record units there)
#          'final' : value at the last day
#   scale/places: how tables and figures present the number (stored values are unscaled).
#   group  'primary', 'secondary' (listed in the prespecification), 'descriptive' (time budget and other aids; not pre-specified).


def _o(label, unit, direction, basis, group, definition, scale=1., places=2):
    return dict(label=label, unit=unit, direction=direction, basis=basis, group=group, definition=definition, scale=scale, places=places)


UNIT_TIME = 'member-workdays per member-year'   # the engine's *_hours counters add 0.1 per review etc.: they count member-workdays

OUTCOMES = {
    PRIMARY_OUTCOME: _o('Net operating result', 'resource units per member-year', 'higher', 'post', 'primary',
                        '(revenue - operating cost) over the days from the branch day on, divided by member-years of the same days '
                        '(active member calendar days / 365.25); operating cost includes the processing charge', places=2),
    # secondary outcomes of the prespecification
    'decision_regret_per_available_member_hour_post': _o(
        'Decision regret', 'regret units per million available member-hours', 'lower', 'post', 'secondary',
        'increase of decision_regret_units_cumulative over increase of available_member_work_hours_cumulative from the branch day on',
        scale=1e6, places=2),
    'administrative_hours_per_member_year_whole': _o(
        'Governance and administrative time', UNIT_TIME, 'lower', 'whole', 'secondary',
        'review + appeal + governance + organisational verification hours of the whole run over whole-run member-years '
        '(r11_outcomes: governance_member_hours_per_member_year)', places=3),
    'review_appeal_governance_hours_per_member_year_post': _o(
        'Review, appeal and governance time', UNIT_TIME, 'lower', 'post', 'secondary',
        'increase of the cumulative review, appeal and governance hours from the branch day on over post member-years '
        '(organisational verification hours have no cumulative column and are not included)', places=3),
    'confirmation_mean_days_whole': _o(
        'Confirmation delay', 'calendar days', 'lower', 'whole', 'secondary',
        'mean days from submission to confirmation over all confirmed records of the run (summary.json confirmation_mean_days)', places=1),
    'review_backlog_at_end_per_member': _o(
        'End backlog', 'records per active member', 'lower', 'final', 'secondary',
        'review_backlog_records / active_members on the last day', places=2),
    'gini_authority_post_mean': _o(
        'Formal-authority concentration', 'Gini of formal shares', 'descriptive', 'post', 'secondary',
        'mean of the daily gini_authority over the days from the branch day on', places=3),
    'exits_per_member_year_post': _o(
        'Exits', 'exits per member-year', 'lower', 'post', 'secondary',
        'increase of (ever_people - active_members) from the branch day on over post member-years (equals summary exits plus retirements over the whole run; the engine counts the two separately)',
        places=4),
    'processing_charge_per_member_year_post': _o(
        'Processing charge', 'resource units per member-year', 'lower', 'post', 'secondary',
        'summary processing_charge_resource_units over post member-years (the shared history carries no charge: control-hierarchy pays none)',
        places=3),
    # counts (secondary): automated decisions and sandbox events, whole-run totals, history carries none
    'automated_accepts': _o('Automated accepts', 'decisions', 'descriptive', 'count', 'secondary', 'summary automated_accepts', places=0),
    'automated_rejects': _o('Automated rejects', 'decisions', 'descriptive', 'count', 'secondary', 'summary automated_rejects', places=0),
    'automated_false_accepts': _o('Automated false accepts', 'decisions', 'lower', 'count', 'secondary', 'summary auto_false_accepts', places=0),
    'automated_false_rejects': _o('Automated false rejects', 'decisions', 'lower', 'count', 'secondary', 'summary auto_false_rejects', places=0),
    'sandbox_requests': _o('Sandbox requests', 'requests', 'descriptive', 'count', 'secondary', 'summary sandbox_requests', places=0),
    'sandbox_refusals': _o('Sandbox refusals', 'requests', 'descriptive', 'count', 'secondary',
                           'summary sandbox_requests - sandbox_requests_approved', places=0),
    'core_protection_blocks': _o('Core-protection blocks', 'attempts', 'descriptive', 'count', 'secondary',
                                 'summary core_blocks (absent = no attempt was journalled)', places=0),
    'selfmod_rollbacks': _o('Rule-change rollbacks', 'rollbacks', 'descriptive', 'count', 'secondary', 'summary selfmod_rollbacks', places=0),
    # descriptive aids (not pre-specified outcomes)
    'selfmod_adoptions': _o('Rule-change adoptions', 'adoptions', 'descriptive', 'count', 'descriptive', 'summary selfmod_adoptions', places=0),
    'privacy_blocks': _o('Blocked reads', 'reads', 'descriptive', 'count', 'descriptive', 'summary privacy_blocks', places=0),
    'net_operating_result_whole_per_member_year': _o(
        'Net operating result, whole run', 'resource units per member-year', 'higher', 'whole', 'descriptive',
        '(revenue - operating cost) of the whole run over whole-run member-years (r11_outcomes definition)', places=2),
    'processing_units_per_member_year_post': _o(
        'Processing units', 'processing units per member-year', 'lower', 'post', 'descriptive',
        '(automated + witness verification + coordination processing units) over post member-years (history carries none)', places=1),
    'unfunded_operating_per_member_year_post': _o(
        'Unfunded operating cost', 'resource units per member-year', 'lower', 'post', 'descriptive',
        'increase of operating_unfunded_resource_units_cumulative from the branch day on over post member-years', places=3),
}
# record-class counts (secondary: "by record class")
CLASSES = ('objective', 'inferential', 'disputed')
# outcome suffix -> (label text, summary.json key suffix after rc_<class>_)
CLASS_FIELDS = {'claims': ('claims', 'claims'), 'automated_decisions': ('automated decisions', 'automated'),
                'wrong_automated_decisions': ('wrong automated decisions (false accepts + false rejects)', 'wrong'),
                'human_reviews': ('human reviews', 'human_reviews')}
for _c in CLASSES:
    for _k, (_lab, _key) in CLASS_FIELDS.items():
        OUTCOMES[f'class_{_c}_{_k}'] = _o(f'{_c.capitalize()} records: {_lab}', 'records', 'descriptive', 'count', 'secondary',
                                          f'summary rc_{_c}_{_key}', places=0)

# Time budget components, whole-run rate (all six exist on this basis) and, where a cumulative column exists, from the branch day on.
TIME_COMPONENTS = [  # key, label, post possible
    ('review', 'Review', True), ('appeal', 'Appeal', True), ('governance', 'Governance', True),
    ('verification', 'Org. verification', False), ('coordination', 'Coordination', False), ('network', 'Reporting and network', True)]
for _k, _lab, _post in TIME_COMPONENTS:
    OUTCOMES[f'hours_{_k}_whole_per_member_year'] = _o(f'{_lab} time, whole run', UNIT_TIME, 'descriptive', 'whole',
                                                        'descriptive', f'whole-run {_lab.lower()} hours over whole-run member-years',
                                                        places=2)
    if _post:
        OUTCOMES[f'hours_{_k}_post_per_member_year'] = _o(f'{_lab} time, from adoption', UNIT_TIME, 'descriptive', 'post',
                                                           'descriptive', f'increase of the cumulative {_lab.lower()} hours from the branch day on over post member-years',
                                                           places=2)

# Change in member time relative to Hierarchy after adoption: (arm whole-run total - Hierarchy whole-run total) / post member-years of the arm.
# Exact because the shared history is identical in every arm (collect.py asserts it); negative = less member time than the hierarchy.
for _k, _lab, _post in TIME_COMPONENTS:
    OUTCOMES[f'hours_{_k}_change_per_post_member_year'] = _o(
        f'{_lab} time, change vs Hierarchy', UNIT_TIME, 'descriptive', 'post', 'descriptive',
        f'(whole-run {_lab.lower()} hours of the arm minus those of control-hierarchy) / post member-years of the arm', places=2)
OUTCOMES['administrative_hours_change_per_post_member_year'] = _o(
    'Governance and administrative time, change vs Hierarchy', UNIT_TIME, 'lower', 'post', 'secondary',
    '(review + appeal + governance + organisational verification hours of the arm minus those of control-hierarchy) / post member-years of the arm',
    places=3)
OUTCOMES['administrative_hours_per_member_year_whole']['group'] = 'descriptive'

SECONDARY_NAMES = [n for n, d in OUTCOMES.items() if d['group'] == 'secondary']
# Order of the secondary outcome table (rates first, then counts)
SECONDARY_RATES = ['decision_regret_per_available_member_hour_post', 'administrative_hours_change_per_post_member_year',
                   'review_appeal_governance_hours_per_member_year_post', 'confirmation_mean_days_whole',
                   'review_backlog_at_end_per_member', 'gini_authority_post_mean', 'exits_per_member_year_post',
                   'processing_charge_per_member_year_post']
SECONDARY_COUNTS = (['automated_accepts', 'automated_rejects', 'automated_false_accepts', 'automated_false_rejects']
                    + [f'class_{c}_{k}' for c in CLASSES for k in ('claims', 'automated_decisions', 'wrong_automated_decisions', 'human_reviews')]
                    + ['sandbox_requests', 'sandbox_refusals', 'core_protection_blocks', 'selfmod_rollbacks'])

# Secondary outcomes the pipeline cannot compute from the downloaded files (stated in README and in the results).
NOT_COMPUTABLE = {
    'accepts, rejects, false accepts and false rejects separately by record class':
        'summary.json carries the four counts only as run totals; by class it carries claims, automated decisions, wrong automated '
        'decisions (false accepts and false rejects together), human reviews, confirmed records, units and days to confirmation. '
        'The split by class and error direction exists only in the event journal, which is not downloaded.',
    'organisational verification hours and coordination hours from the branch day on':
        'no cumulative daily column exists for them; they are reported for the whole run (the shared history is identical in every arm).',
}


# The unit of inference is a simulated firm; the files and variables of the pipeline say "world", every text it emits says firm.
UNIT_WORD, UNIT_WORDS, UNIT_CAP = 'firm', 'firms', 'Firms'
# Declared processing rate (resource units per processing unit) of the formal study; the rate-sensitivity macros are built from it.
PROCESSING_RATE = 0.01

# A cumulative unfunded amount above this many resource units counts as non-zero (floating-point noise of differences of cumulative sums is far below it).
UNFUNDED_TOLERANCE = 1e-9
# What the engine's operating_unfunded_resource_units contains (dams_sim/longitudinal_model.py, see README "The unfunded amount").
UNFUNDED_CONTENT = ('the part of the daily operating bill that cash could not cover (payroll, maintenance, dual-running, infrastructure maintenance, '
                    'legal and interest costs; LongitudinalEngine._step lines 818-829) plus the part of the end-of-day processing charge that cash could not cover (lines 1087-1092); '
                    'it is not split by cause in the output files, so it is an upper bound on the unpaid processing charge')


# ---- wording helpers shared by stats.py and make_tex.py (LaTeX-ready text) -------------------------------------

NUMBER_WORDS = {0: 'no', 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten'}


def nw(k):
    """Number word for 0 to 10 ('no' for 0), digits above."""
    return NUMBER_WORDS.get(k, str(k))


def tex_minus(s):
    return s.replace('-', '$-$')


def plain(v, places):
    """Number with `places` decimals; a negative sign is written $-$; a value that rounds to zero is written without sign."""
    if v is None:
        return 'n/a'
    return tex_minus(f'{v:.{places}f}' if float(f'{v:.{places}f}') != 0 else f'{0:.{places}f}')


def plain_abs(v, places):
    """Absolute value with `places` decimals; a non-zero value that would print as zero is written 'less than 0.01' (to the precision shown)."""
    if v is None:
        return 'n/a'
    a = abs(v)
    if a != 0 and float(f'{a:.{places}f}') == 0:
        return f'less than {10**-places:.{places}f}'
    return f'{a:.{places}f}'


def join_and(items, sep=None):
    """'a', 'a and b', 'a, b and c'; with `sep` '; ' (used when an item contains a comma) the last separator is '; and '."""
    items = list(items)
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    if sep is None:
        sep = '; ' if any(',' in i for i in items) else ', '
    if sep == ', ':
        return ', '.join(items[:-1]) + ' and ' + items[-1]
    return sep.join(items[:-1]) + '; and ' + items[-1]


def direction(mean):
    """'higher than', 'lower than' or 'equal to' for the sign of a mean difference."""
    return 'higher than' if mean > 0 else 'lower than' if mean < 0 else 'equal to'


def interval_position(c):
    """Position of the 95% interval of a contrast entry relative to zero: above, below, contains, none (no interval), missing."""
    if c is None or c.get('mean') is None:
        return 'missing'
    ci = c.get('ci95')
    if ci is None:
        return 'none'
    return 'above' if ci[0] > 0 else 'below' if ci[1] < 0 else 'contains'


def zero_phrase(c):
    """Verb phrase for 'the interval ...' relative to zero."""
    pos = interval_position(c)
    if pos == 'none':
        return 'is not defined for one firm' if c.get('n') == 1 else 'is not defined because the paired differences do not vary'
    return {'above': 'lies above zero', 'below': 'lies below zero', 'contains': 'contains zero', 'missing': 'is not estimated'}[pos]
