# Automation and sandbox arms (opt-in)

Study design of the automation and sandbox comparison: variant `accounted-work`, arms `control-hierarchy`,
`hierarchy-auto`, `dams-witness`, `dams-auto`, `dams-isa` and `dams-isa-x50` (10,000 members); the other arms
below are mechanism and sensitivity studies.

New arms: `hierarchy-auto`, `dams-auto`, `hierarchy-auto-measured`, `dams-auto-measured`, `dams-isa`,
`dams-isa-fault`, `dams-isa-saturated`. The arms of the earlier studies are unchanged: they carry no
`automation` block, and `tests/test_automation_arms.py::ExistingArmsUnchangedTest` checks that their daily
series are byte-identical to those of the source that ran the 10,000-member study (source SHA-256
`76246e9f…`, commit `fe08659`).

All probabilities and quantities below are declared design assumptions, not calibrated values. The
behavioural equations of the engine (effort, trust, fatigue, learning) are the same in every arm.

## Work accounting (variant `accounted-work`)
In the earlier model members withheld time for review, appeal and governance, but the work itself did not
enter the workload of the fatigue update. The variant counts the review (0.1 workday per record, shared by
the present members of the domain), appeal (0.15), organizational verification (0.025, the named reviewer)
and governance (0.05) work a member actually does. Fatigue changes by 0.25 x (hours - reference), the
slope of the existing update in workload. The reference, 0.14 workday, is the administrative work of a
present member in the manual hierarchy of the earlier model, so the hierarchy keeps the fatigue level the
constants were set for and only departures from that load matter. The variant applies to the shared
history and to every arm of a study; time freed by automation is not converted into extra production
time (the reservations are unchanged), it lowers the workload.

## Automated verification (`dams-auto`, `hierarchy-auto`)
The first review of a claim is automated on the submission day. Per claim:

1. with probability `exception_fraction` (0.1) the claim is sampled to the human exception queue;
2. otherwise the verifier processes it (`processing_units_per_claim` 1.0 x the evidence backend's
   multiplier; no member time) and abstains with `uncertain_probability` (0.05) -> human queue;
3. otherwise it applies the acceptance rule to the claim's evidence and deviates from it at declared rates:
   a claim the rule refuses is accepted with `false_accept_probability` (0.05), a claim the rule accepts
   is refused with `false_reject_probability` (0.05).

The acceptance rule is the human review's (`acceptance_rule='threshold'`): observed work of at least 0.25
and no audit alarm. The alarm is the same signal the human review uses (0.6 for forged, 0.02 for honest
claims), so the verifier has no better fraud detection than a reviewer. Automation therefore changes who
verifies and when, not what is accepted.

The exception queue is the ordinary review queue (same slots, 0.1 workday per human review). Members keep
the review-time reservation of the manual arms; the model assumes no extra production time from reviews
that no longer happen. Accepted claims settle `settlement_days` after submission (the reporting delay is
kept). Automated rejections have the consequences of a human rejection (trust, appeal path). Processing
units are reported, not debited from cash (`resource_units_per_processing_unit` 0.01 is a reporting
conversion), as for witness units.

`hierarchy-auto` is `control-process` (same migration, training and dual-running costs) plus the same
verification. Record verification exists independently of the authority rule, so the comparison of
`dams-auto` with `hierarchy-auto` isolates the authority rule and evidence backend at equal automation.

## Measured credit (`*-measured`)
`acceptance_rule='measured'` removes the minimum amount: the measured work is credited as it is and a record
is refused only on an audit alarm (plus the declared machine error). The human exception lane applies the
same rule in these arms. This is a different verification policy that automation makes practicable, run
for both the hierarchy and DAMS so that its effect can be separated from the effect of automation.

## Sandbox architecture (`dams-isa`)
`dams-auto` plus the sandbox graph (`dams_sim/sandbox.py`):

- guild sandboxes with multi-membership; split above `split_size` (400) members and merge below
  `merge_size` (4); members, credit, open claims and history are carried to the successors;
- a per-sandbox daily verification quota (`quota_units_per_member_day` 1.35); a sandbox short of quota asks
  another for spare quota, capped at `borrow_limit_fraction` (0.5) of its own quota and approved per
  request; overflow goes to the human exception lane; consumption is attributed to the sandbox;
- scope: a sandbox acts freely on itself and its descendants; upward and lateral requests need approval
  (by the target, or by the common parent) with `approval_rejection_probability` 0.1,
  `approval_latency_days` 0 and `grant_validity_days` 30, and every request is logged;
- privacy scope: a human reviewer whose home sandbox does not contain the claimant's needs a read grant;
  without one the read is blocked and logged and the claim waits a day;
- a protected core (identity, record substrate, allocation rules, joint confirmation, recovery) that no
  sandbox can modify; attempts are blocked and logged;
- self-modification of the sandbox-local `exception_fraction` inside declared bounds (0.02-0.30): a
  proposal runs on a shadow copy for 14 days, is adopted only if the health measure does not degrade, is
  watched for 14 more days and rolled back automatically if health falls below its earlier level.

Scoped authority (`scoped_authority`, on in `dams-isa`). A member's work lies in the scope of its own
team sandbox, which has full authority over it. Such work therefore passes `in_scope_layers` (1) layer
of reporting and coordination instead of the layers of the whole organization; the share
`cross_boundary_share` (0.25, the dependency of a work unit on other departments' output) of the remaining
layers is still borne, because work that crosses the boundary needs the units above. For a member at
reporting depth d the layers borne are min(d, 1) + 0.25 (d - min(d, 1)); the same rule applies to the
coordination layers. It changes reporting time, the readiness penalty per layer, the delay of a work
record (the record is in scope: at most one layer) and coordination demand. With share 1 the arm equals
`dams-isa-unscoped`; `dams-isa-x50` uses 0.5. Team sandboxes are represented by this rule; guild sandboxes
are graph objects that carry quota, grants, lifecycle, self-modification and faults.

The engine drives guild sandboxes, the lifecycle, quotas and borrowing, reviewer read grants,
self-modification and scoped authority. Project sandboxes, core-modification attempts and allocation-rule proposals are graph
mechanisms exercised by unit tests, not by the engine's daily loop. With `sandbox_level='team'` formal
shares are normalised inside each team sandbox (tested; not used by the study arms).

## Fault and saturation studies
`dams-isa-fault`: a 14-day verification-service outage in sandbox `g0` starting 120 days after the boundary.
Reports whether the throughput of other sandboxes (confirmed claims per member-day, fault window against
the 30 days before) fell by more than `fault_drop_threshold` (0.2), and the days to detection and recovery.
A `bad_parameter` fault mode (a wrong local parameter, detected by the health monitor and rolled back) is
tested but not part of the study arms.

`dams-isa-saturated`: the quota is 0.6 units per member-day, about half of the expected demand, in every
sandbox, so no sandbox has quota to lend and the overflow is reviewed by members.

## Measures
`python -m research_tools.r11_isa_measures ARM_DIR ...` prints governance cost (member-hours and processing
units apart), collaboration, structural change, scaling, safety and governance-quality measures, and writes
`isa-measures.json` only into arms that carry an automation block.
