"""Opt-in automated record verification and nested sandboxes (research arms only).

Nothing here runs unless a configuration carries an `automation` block, so every
existing arm keeps its exact behaviour and its configuration digest.

Automated verification replaces the first human review of a contribution claim.
A claim is routed on its submission day:

  1. a declared fraction (`exception_fraction`) is sampled to the human exception
     queue and is not processed automatically;
  2. every other claim is verified by the automated verifier, which consumes
     `processing_units_per_claim` x the evidence backend's multiplier in
     processing units and no member time; when sandboxes are on, those units
     come out of the claim's sandbox quota;
  3. the verifier abstains on a claim with probability `uncertain_probability`;
     an abstained claim goes to the human exception queue;
  4. otherwise the verifier applies the declared acceptance rule to the claim's
     evidence and errs at declared rates.  Under `acceptance_rule='threshold'`
     (default) the rule is the human review's: observed work at or above 0.25
     and no audit alarm, so automation changes who verifies and when, not what
     is accepted.  Under 'measured' the measured amount is credited as it is
     and only an audit alarm refuses a record.  Relative to the rule: a claim the rule rejects is accepted
     with probability `false_accept_probability`; a claim the rule accepts is
     rejected with probability `false_reject_probability`.

The exception queue is the ordinary review queue: it uses the existing review
slots, and each human review still costs 0.1 workday.  Members keep the same
review-time reservation as in the manual arms, so the model assumes no extra
production time from the reviews that no longer happen (a conservative
choice).  All probabilities are declared design assumptions, not calibrated rates.  Draws are keyed by (day, person), so
they do not disturb any other random stream.

Sandboxes (guilds, or teams inside guilds) give each unit a scoped formal
authority (shares sum to one inside the sandbox and have no effect outside it)
and a daily verification-processing quota.  A sandbox short of quota may ask
another sandbox for spare quota; the request goes to the common parent of the
two (the guild for two teams of one guild, otherwise the organization root),
which approves it only against the donor's remaining quota and the borrower's
borrowing limit.  Every request and its outcome is logged.
"""
from __future__ import annotations

import dataclasses
import math

VERSION = 'automation-1'
ROOT = 'root'
ROUTES = ('automated', 'sampled', 'uncertain', 'quota', 'outage')
FAULT_MODES = ('none', 'service_stop', 'bad_parameter')
VERDICTS = ('accept', 'reject', 'exception')


@dataclasses.dataclass(frozen=True)
class AutomationConfig:
    schema_version: int = 1
    # Share of claims sent to humans regardless of the verifier (audit sample).
    exception_fraction: float = 0.1
    # Error model of the automated verifier relative to the review rule (conservative declared values).
    false_accept_probability: float = 0.05    # claim the rule rejects is accepted
    false_reject_probability: float = 0.05    # claim the rule accepts is rejected
    uncertain_probability: float = 0.05       # verifier abstains -> human
    # Acceptance rule of record verification where automation is in use (also applied by the human
    # exception lane there): 'threshold' (the human review's rule, default) or 'measured', see acceptance_floor().
    acceptance_rule: str = 'threshold'
    # Processing units per claim, before the evidence backend's multiplier.
    processing_units_per_claim: float = 1.0
    # Reporting conversion only: processing is not debited from cash, in line
    # with the witness verification units that are also reported separately.
    resource_units_per_processing_unit: float = 0.01
    # Nested sandboxes (dams-isa). Off: unlimited processing, no sandbox logs.
    sandboxes: bool = False
    sandbox_level: str = 'guild'                # 'guild' or 'team'
    quota_units_per_member_day: float = 1.35    # 1.25 x expected demand 1.08 (witness, 10 % sampled)
    borrow_limit_fraction: float = 0.5          # of the borrower's own quota
    # Scoped authority (sandbox architecture). A member's work is in the scope of its own team sandbox,
    # which has full authority over it: such work reports through `in_scope_layers` layers only. The share
    # `cross_boundary_share` of reporting and coordination concerns work that crosses the sandbox boundary
    # and still passes the layers above. Share 1 reproduces the unscoped organization.
    scoped_authority: bool = False
    in_scope_layers: int = 1
    cross_boundary_share: float = 0.25
    # Approval of upward/lateral crossings (sandbox graph).
    approval_rejection_probability: float = 0.1
    approval_latency_days: int = 0
    grant_validity_days: int = 30
    # Lifecycle: split a guild sandbox above split_size members, merge sibling leaves below merge_size (0 = off).
    split_size: int = 400
    merge_size: int = 4
    # Self-modification of the sandbox-local exception fraction.
    self_modify: bool = True
    proposal_interval_days: int = 30
    shadow_window_days: int = 14
    self_modify_step: float = 0.02
    health_tolerance: float = 0.0
    human_load_weight: float = 0.5              # health cost of a claim routed to a human, relative to a wrong decision
    # Fault injection into ONE sandbox (off by default). service_stop: its verification service is down for
    # fault_days (claims go to humans). bad_parameter: its local false-reject probability is set to
    # fault_bad_false_reject until the health monitor detects the breach and rolls it back (or fault_days pass).
    fault_mode: str = 'none'
    fault_sandbox: str = 'g0'
    fault_start_day: int = 0
    fault_days: int = 14
    fault_bad_false_reject: float = 0.5
    fault_detect_threshold: float = 0.2         # daily share of wrong automated decisions that counts as a breach
    fault_detect_min_claims: int = 5
    fault_drop_threshold: float = 0.2           # relative throughput drop that counts as 'affected'
    fault_baseline_days: int = 30

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError('automation configuration must be an object')
        unknown = set(value)-{f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError('unknown automation fields: '+str(sorted(unknown)))
        return cls(**value).validate()

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('unknown automation schema')
        for name, low, high in (('exception_fraction', 0., 1.), ('false_accept_probability', 0., 1.),
                                ('false_reject_probability', 0., 1.), ('uncertain_probability', 0., 1.),
                                ('borrow_limit_fraction', 0., 1.), ('cross_boundary_share', 0., 1.), ('approval_rejection_probability', 0., 1.), ('self_modify_step', 0., 0.3), ('fault_bad_false_reject', 0., 1.), ('fault_detect_threshold', 0., 1.), ('fault_drop_threshold', 0., 1.), ('health_tolerance', 0., 1.), ('human_load_weight', 0., 10.), ('resource_units_per_processing_unit', 0., 1e6)):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
                raise ValueError(f'{name} must be finite in [{low}, {high}]')
        for name in ('processing_units_per_claim', 'quota_units_per_member_day'):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 1e6:
                raise ValueError(f'{name} must be finite and positive')
        for name, low in (('in_scope_layers', 0), ('approval_latency_days', 0), ('grant_validity_days', 1), ('split_size', 0), ('merge_size', 0),
                          ('proposal_interval_days', 1), ('shadow_window_days', 1), ('fault_start_day', 0), ('fault_days', 1),
                          ('fault_detect_min_claims', 1), ('fault_baseline_days', 1)):
            v = getattr(self, name)
            if type(v) is not int or not low <= v <= 100_000_000:
                raise ValueError(f'{name} must be an integer >= {low}')
        if self.fault_mode not in FAULT_MODES or not isinstance(self.fault_sandbox, str) or not self.fault_sandbox:
            raise ValueError('fault_mode must be none, service_stop or bad_parameter and fault_sandbox a sandbox id')
        if self.fault_mode != 'none' and not self.sandboxes:
            raise ValueError('fault injection needs sandboxes')
        if self.acceptance_rule not in ACCEPTANCE_RULES:
            raise ValueError('acceptance_rule must be measured or threshold')
        if not isinstance(self.self_modify, bool):
            raise ValueError('self_modify must be boolean')
        if not isinstance(self.scoped_authority, bool) or (self.scoped_authority and not self.sandboxes):
            raise ValueError('scoped_authority is boolean and needs sandboxes')
        if not isinstance(self.sandboxes, bool):
            raise ValueError('sandboxes must be boolean')
        if self.sandbox_level not in ('guild', 'team'):
            raise ValueError('sandbox_level must be guild or team')
        return self


# ---- routing and the error model -------------------------------------------------

def is_sampled(fraction, u_sample):
    """True when the claim is audit-sampled to the human exception queue."""
    return u_sample < fraction


ACCEPTANCE_THRESHOLD = 0.25
ACCEPTANCE_RULES = ('measured', 'threshold')


def acceptance_floor(config):
    """Minimum observed work for a record to be accepted where automation is in use.

    'threshold' keeps the human review's minimum (0.25). 'measured' has none: the
    measured amount is credited as it is and a record is refused only on an audit
    alarm, so a small contribution earns a small credit instead of a rejection.
    """
    return ACCEPTANCE_THRESHOLD if config.acceptance_rule == 'threshold' else 0.


def rule_accepts(observed, audit_detected, floor=ACCEPTANCE_THRESHOLD):
    """The acceptance rule applied to a claim's evidence (no reviewer-specific
    evaluation shift: the verifier is not a colleague)."""
    return observed >= floor and not audit_detected


def verdict(config, *, rule_accept, u_uncertain, u_error, false_reject=None):
    """Automated verdict for a claim that has been processed: accept/reject/exception.

    `rule_accept` is what the review rule decides on the claim's evidence; the
    verifier deviates from it at the declared error rates.
    """
    if u_uncertain < config.uncertain_probability:
        return 'exception'
    if not rule_accept:
        return 'accept' if u_error < config.false_accept_probability else 'reject'
    fr = config.false_reject_probability if false_reject is None else false_reject
    return 'reject' if u_error < fr else 'accept'


def is_wrong(result, rule_accept):
    return (result == 'accept' and not rule_accept) or (result == 'reject' and rule_accept)


def processing_units(config, backend_multiplier):
    return config.processing_units_per_claim*backend_multiplier


def settled_ready(claim_ready, settlement_days):
    """Commit-ready day of an automatically accepted claim.

    A claim submitted on day d has ready = d+1 (+ reporting delay).  Automated
    verification happens on d itself, so the record settles after the backend's
    settlement lag counted from d; the reporting delay is kept.
    """
    return claim_ready-1+settlement_days


# ---- scoped authority: layers a member's work passes --------------------------------------

def effective_layers(config, layers):
    """Layers of reporting or coordination borne under scoped authority.

    Work inside the sandbox passes at most `in_scope_layers`; the cross-boundary share of the
    remaining layers is still borne.  With share 1 the result equals `layers`.
    """
    inside = min(layers, config.in_scope_layers)
    return inside+config.cross_boundary_share*(layers-inside)


def in_scope_delay_layers(config, layers):
    """Layers that delay a member's own work record: the record is in scope."""
    return min(layers, config.in_scope_layers)


# ---- sandboxes: quota and borrowing ----------------------------------------------------

def allocate_quota(demands, quotas, *, borrow_limit_fraction, approve=None):
    """Spend each sandbox's quota on its claims, then settle borrowing requests.

    demands: {sandbox: [units, ...]} in processing order (claims eligible for
             automated processing only).
    quotas:  {sandbox: units available today} (also lists the potential donors).
    approve: approve(requester, donor) -> (approved, reason, approver); the
             sandbox graph's approval of an upward/lateral crossing. Default:
             approve everything (approver root).
    Returns (admitted, flows). admitted[sandbox][k] is 'own', ('borrowed', donor)
    or None (not admitted: the claim goes to humans). flows is the ordered log of
    borrowing outcomes {requester, donor, units, claims, approver, approved, reason};
    a borrower stays within borrow_limit_fraction x its own quota and a donor
    never lends more than its remaining quota.
    """
    approve = approve or (lambda requester, donor: (True, 'approved', ROOT))
    admitted, used, spare = {}, {}, {}
    for sb in sorted(demands):
        quota = float(quotas[sb]); total = 0.; marks = []
        for units in demands[sb]:
            if total+units <= quota+1e-12:
                total += units; marks.append('own')
            else:
                marks.append(None)
        admitted[sb] = marks; used[sb] = total
    for sb in sorted(quotas):
        spare[sb] = max(0., float(quotas[sb])-used.get(sb, 0.))
    flows = []
    for sb in sorted(demands):
        limit = borrow_limit_fraction*float(quotas[sb]); borrowed = 0.
        taken = {}  # donor -> [units, claims]
        denied = {}  # reason -> [units, claims]
        verdicts = {}
        for k, units in enumerate(demands[sb]):
            if admitted[sb][k] is not None:
                continue
            if borrowed+units > limit+1e-12:
                reason = 'over borrow limit'
            else:
                reason = 'no spare quota'; chosen = None
                for donor in sorted((d for d in spare if d != sb and spare[d]+1e-12 >= units), key=lambda d: (-spare[d], d)):
                    if donor not in verdicts:verdicts[donor] = approve(sb, donor)
                    ok, why, approver = verdicts[donor]
                    if ok:chosen = donor; break
                    reason = why
                if chosen is not None:
                    spare[chosen] -= units; borrowed += units
                    admitted[sb][k] = ('borrowed', chosen)
                    flow = taken.setdefault(chosen, [0., 0]); flow[0] += units; flow[1] += 1
                    continue
            row = denied.setdefault(reason, [0., 0]); row[0] += units; row[1] += 1
        for donor in sorted(taken):
            flows.append({'requester': sb, 'donor': donor, 'units': taken[donor][0], 'claims': taken[donor][1],
                          'approver': verdicts[donor][2], 'approved': True, 'reason': 'donor quota available'})
        for reason in sorted(denied):
            flows.append({'requester': sb, 'donor': None, 'units': denied[reason][0], 'claims': denied[reason][1],
                          'approver': ROOT, 'approved': False, 'reason': reason})
    return admitted, flows


# ---- scoped authority ---------------------------------------------------------------

def scoped_shares(contributions, exponent, scope_size):
    """Formal shares computed and normalised inside consecutive scopes.

    contributions are the members of one guild in roster order; every block of
    `scope_size` members (a team) is one sandbox.  Shares are computed from the
    members' own contributions only, sum to one inside each block, and a block
    contributes nothing to any other block's shares.  Returns (weights, blocks).
    """
    from .authority import authority
    weights, blocks = [], []
    for start in range(0, len(contributions), scope_size):
        block = list(contributions[start:start+scope_size])
        w = authority(block, exponent, zero_policy='equal')
        if not math.isclose(math.fsum(w), 1., rel_tol=0, abs_tol=1e-9):
            raise ValueError('scoped shares do not sum to one inside their sandbox')
        weights.extend(w); blocks.append((start, start+len(block)))
    return weights, blocks
