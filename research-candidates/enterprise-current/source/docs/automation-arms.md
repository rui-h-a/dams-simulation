# Automation and sandbox arms (opt-in)

Study design of the automation and sandbox comparison: variant `accounted-work`, arms `control-hierarchy`,
`hierarchy-auto`, `dams-witness`, `dams-auto`, `dams-isa` and `dams-isa-x50` (10,000 members); the other arms
below are mechanism and sensitivity studies.

New arms: `hierarchy-auto`, `dams-auto`, `hierarchy-auto-measured`, `dams-auto-measured`, `dams-isa`,
`dams-isa-fault`, `dams-isa-saturated`, `dams-isa-full`, `hierarchy-isa-full` (project sandboxes, see below).
New variants: `accounted-work-decisions` and `accounted-work-decisions-g20` (decision effect, see below). The arms of the earlier studies are unchanged: they carry no
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
self-modification and scoped authority. Project sandboxes, core-modification attempts and allocation-rule proposals are
driven by the daily loop only in the `*-isa-full` arms (`project_sandboxes: true`, see "Project sandboxes and
core-protection attempts"); in every other arm they remain graph mechanisms exercised by unit tests. With
`sandbox_level='team'` formal shares are normalised inside each team sandbox (tested; not used by the study arms).

## Delegated hierarchy (`hierarchy-isa`) and span variants
`hierarchy-isa` gives the retained hierarchy automated verification and the same sandbox architecture and
scope rule as `dams-isa`; authority stays with performance tiers and the record stays central. It is the
strongest hierarchical competitor: the difference between `dams-isa` and `hierarchy-isa` is what the
contribution-based authority rule and the witnessed record add once scope and automation are equal.
Variants `accounted-work-span4` and `accounted-work-span16` change the span of control from 8, and with it
the supervisory depth and the sizes at which a layer is added. Arms and variants are configurations:
the model source hash covers `dams_sim/*.py` only, so they branch from the same histories as the other arms.

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

## Decision effect on operations (variants `accounted-work-decisions`, `accounted-work-decisions-g20`)
Without the block each guild's daily binary proposal decision only produces a regret figure that is reported and never
feeds back into operations. A top-level `decision_effect` block (absent by default; `dams_sim/decision_effect.py`)
adds one channel from decision quality to output. The variants are `accounted-work` plus the block with its defaults
(`accounted-work-decisions`) or with `gamma` 0.2 (`accounted-work-decisions-g20`); like every variant the block belongs to
the shared history and to all arms of a study.

State and rule. Every guild g carries a decision-quality debt D_g in [0, 1], initially 0 (a guild without a recorded
decision holds 0). After each workday on which the guild decided,

    D_g <- D_g + (min(1, loss / reference_loss) - D_g) / memory_workdays

with `loss` the journalled decision loss (|theta| for a wrong or unresolved decision, else 0). The next workday every
member of guild g has potential output multiplied by `1 - gamma * D_g`, using the morning value (before that day's
decision; the engine snapshots the debts before the decision loop and today's decisions move them only for tomorrow).
The factor is applied where the per-member production potential is computed: the organization-tasks branch (after the
reporting-readiness factor, before the dependency constraint), the enterprise-operations branch and the plain branch of
`_step`, one multiplication in each, so an arm uses the same factor whichever branch produces its output. The work record
carries it as `decision_output_factor` (only when the block is present).

Declared design assumptions (not calibrated): `gamma` 0.05 (share of a guild's output that depends on decision quality;
0.2 in `-g20`), `memory_workdays` 20. `reference_loss` 0.7978845608 is E|theta| for the unit-normal proposal value
(sqrt(2/pi)), the loss of a decision that is always wrong; it follows from the declared proposal distribution. That
decision quality changes output at all is the modelling assumption of this channel, not a finding.

Guild changes. A scheduled guild merger gives the successor domain the member-weighted mean of the two debts (plain
mean for two empty domains), the rule the engine uses for organizational memory and routine, evaluated with the same
member counts. Guilds added by enterprise growth or by acquisition are new domains and start at 0. A guild identifier changes
only through a scheduled merger (`guild_mergers`, which may also name acquired guilds); acquisitions themselves add
guilds and never merge or rename them, so no case has to be guessed and none raises. Persons who move between guilds
leave the debt with the guild.

Persistence. The debts are saved in the checkpoint header (`decision_state`, only with the block) and restored with a
consistency check against the declared block; the day-end record carries `decision_effect_state`. Metrics:
`decision_effect_output_factor_sum` and `decision_effect_output_factor_count` (one factor per work record), and
`decision_effect_mean_output_factor` in the summary. With `gamma` 0 the factor is exactly 1 and the daily series equal
those of the same arm without the block (tested); with the block absent every earlier arm is unchanged
(`ExistingArmsUnchangedTest`, and the configuration digests of earlier arms are asserted in `tests/test_decision_project.py`).

Validated independently. The longitudinal validator re-derives D_g from the `decision` events alone (including the merger
rule), requires every work record's factor to equal `1 - gamma * D_g(morning)`, re-sums the two metrics and compares the
day-end debts. The organization validator does the same with its own recursion and also includes the factor in its
recomputation of `potential_before_dependencies`, so a factor that is recorded but not applied (or applied but not recorded)
is refused; it compares the final `decision_state` with its reconstruction. Not validated: the decision loss itself is
re-derived from the recorded votes and proposal truth by the longitudinal validator (inherited check); the starting value 0
and the parameters are assumptions.

## Project sandboxes and core-protection attempts (`dams-isa-full`, `hierarchy-isa-full`)
`dams-isa` / `hierarchy-isa` plus `project_sandboxes: true` (active only with `sandboxes` and the organization-tasks model,
because collaboration peers exist only there). The fields `project_sandboxes`, `project_idle_days`,
`core_attempt_probability` and `allocation_attempt_probability` appear in the stored configuration only when the feature
is on, so earlier arms keep their configuration digest, and they may be set only with `project_sandboxes`.

Collaboration across home sandboxes. A member's home is its primary non-project sandbox. When a member i and a present
collaboration peer j have different homes that are not in each other's scope, their collaboration needs a project
sandbox with both homes as parents. Once per day and unordered pair of homes (a < b) the engine asks
`request(day, a, b, 'collaborate')`: a lateral request approved by the common parent, with the existing rejection
probability (`approval_rejection_probability`), latency and grant validity (`grant_validity_days`); a standing grant is
reused until it expires, and a refusal stands for the day. On an approved (or granted) day the project sandbox of the pair
is created (deterministic id `p:a+b`, `#k` for a later generation after a closure) or joined, and the members involved are
added; the member's home does not change. If the request is refused, or the approval is still pending (latency > 0), the pair
does not collaborate that day: peer j contributes nothing to member i's `help_input` but still counts in its denominator
(the mean over present peers is not renormalised, so the boundary has a real cost). A work record then lists the excluded
peers in `collaboration_excluded` (present only when the feature is on). The engine calls the sandbox mirror once a day
before collaboration (it is idempotent within a day), so a guild adopted that day already has homes.

Lifecycle. A project sandbox without collaboration (no approved or granted pair-day) for `project_idle_days` (30) calendar
days is closed; the heir is its first parent, or the first live successor when that parent has been split or merged since
(`close` carries credit and open claims to the heir; tested for conservation). Projects hanging under a guild do not
freeze that guild's split or merge. Members who leave the organization or change guild leave their projects with the
existing mirror rule.

Attempts. When a sandbox makes its periodic self-modification proposal (every `proposal_interval_days`, but only on a
workday, because the verification step that makes it runs only on workdays with claims, so a multiple of the interval
that falls on a weekend is skipped; sandboxes with members that are not in a shadow window; `self_modify` on) one keyed draw (new process `sandbox_attempt`, identity day and
sandbox) decides: below `core_attempt_probability` (0.05) it proposes to change a protected-core item instead, cycling
through `PROTECTED_CORE` in a persisted order (counter `core_cycle` in the sandbox state); the graph blocks and logs it
(`core_block`). Below the sum with `allocation_attempt_probability` (0.05) it proposes a change of `authority_alpha`, which
goes through `propose` (parent approval, never applied locally) and is counted as `allocation_attempts`. Otherwise the
ordinary local proposal is made. The attempt draw has its own process name (`sandbox_attempt`); collaboration approvals reuse the approval process
`sandbox_approval` with the action `collaborate` in the key, which no earlier request uses, so the earlier random streams are
untouched (the daily series of the earlier automation arms are byte-identical to those of the source before these
mechanisms; the digests of two of them are asserted in `tests/test_decision_project.py`).
Both probabilities and `project_idle_days` are declared design assumptions, as is that a refused collaboration costs the
peer's help for the day.

Counts (engine metrics and summary): `project_sandboxes_created`, `project_sandboxes_closed`, `project_sandboxes_live`
(summary), `project_sandbox_member_workdays` (members in live projects, counted once per workday after the day's joins and
closures), `project_collaboration_pair_days` (approved or granted), `refused_collaboration_pair_days` (refused or
pending, once per pair and day), `refused_collaboration_links` (directed member-peer links excluded),
`allocation_attempts`; `core_blocks` and the sandbox request counters already existed and now include these events.
`research_tools/r11_isa_measures.py` exports them (collaboration, safety and governance-quality groups).

Validated independently (longitudinal validator): every `collaborate` request is re-drawn with the keyed stream
(rejection, latency, standing grant); each pair decision in the daily `project_collaboration` event must follow from that
day's requests and the standing grants; creation, joins and closures are rebuilt from the events (one live project per
pair of homes, closure exactly after `project_idle_days` without collaboration, only projects are ever closed); core
blocks must follow the declared cycle and each core and allocation attempt is re-drawn; the counters are re-summed; the
refused-link count must equal the exclusions in the work records. Organization validator: it recomputes `help_input` with
the recorded exclusions and compares them to the pair decisions: for a member and a present peer whose home sandboxes are
both known (the `sandbox` field of that day's automated-verification records), different homes must be excluded exactly
when the pair was refused, and equal homes never. The organization validator applies the arm's project rules from the branch day on (`branch_origin.parent_day`); the shared prehistory before it has no automation and must carry no project record. Not independently verified: the sandbox graph itself (which sandbox a
member belongs to when no automated-verification record exists that day, e.g. a censored claim; those exclusions are
counted in the receipt as `collaboration_exclusions_with_unverified_home`), scope between distinct homes (the engine's homes
are always leaf sandboxes, so distinct homes are outside each other's scope; the verifier assumes this and refuses a
cross-home pair without a recorded decision), the approver chosen for a request, project membership beyond the upper bound
of recorded joins, and completeness of attempts (an attempt that was drawn but not logged would not be noticed; every logged
attempt is checked against its draw).

Size. Worlds have `max(2, round(n/250))` guilds, so a 60-member world has two guild sandboxes and one pair of homes
(one project sandbox, no closure unless guilds split); projects multiply with the number of sandboxes (several guilds, or
splitting with `split_size`). The test arms in `tests/test_decision_project.py` use small `split_size` and
`project_idle_days` to exercise creation, closure, refusal and attempts.

## Record classes in automated verification (`record_classes`)
Opt-in (`AutomationConfig.record_classes`; the class fields are written to the stored configuration only when it is on and may
be set only with it). Each work record of an adopted guild is drawn into a class per (day, person) with the keyed process
`record_class`:

| class | share | handling |
|---|---|---|
| objective | 0.70 | measurable output, verified exactly as before |
| inferential | 0.25 | abstention probability 0.20 (instead of 0.05), false-accept and false-reject probability 0.10 (instead of 0.05), processing units x2 |
| disputed | 0.05 | cannot be decided automatically: always routed to the human exception lane (route `disputed`), nothing processed, no quota demanded |

Shares, abstention and error rates and the multiplier are config fields (`class_share_*`, `inferential_uncertain_probability`,
`inferential_error_probability`, `inferential_processing_multiplier`) and declared design assumptions, not calibrated values.
Sampling into the exception lane (`exception_fraction`) and outages apply to objective and inferential records as before. The
class is not stored on the claim: it is a keyed draw of (submission day, person), repeated at human review and at commit; only
records submitted after the adoption of their domain have one. The class is journalled on the `auto_verify` record.

Per class the engine counts (metrics `rc_<class>_*`, summary `record_class_summary`, `r11_isa_measures`): claims, automated
decisions (so the automated share), wrong automated decisions (accept of a record the rule refuses, or reject of one it accepts),
human first reviews, confirmed records and the days from submission to commit (mean days to confirmation), and processing units.
Validated independently: the validator re-draws every class and requires the journalled class to equal it, requires the route
`disputed` for exactly the disputed records (verdict exception, no units), re-states the units per class (backend multiplier x
2 for inferential) and re-sums every `rc_` metric from the `auto_verify`, `review` and `commit` records (classes of reviews and
commits are re-drawn from the claim's event identity). Not verified: that the error rates of the verifier hold per class (only
the rule outcome and the wrong-decision count follow from the records; the rates are visible in the counts, as before).

## Automated coordination (`coordination_automation`)
`coordination_automation` is a share in [0, 1] (default 0 = off). In adopted guilds of an arm that sets it, the per-layer
coordination time (`coordination_workday_units_per_layer`) and the per-layer reporting hours (`reporting_hours_per_layer`) of
each member are multiplied by (1 - share). Only those two per-layer terms change: the overload and communication terms of
coordination, the collaboration and verification hours, the readiness penalty per layer and the report delay still use the layers
borne. Each such present member-day consumes `coordination_processing_units_per_member_day` (default 0.5) processing units,
counted in `coordination_processing_units` and added to the processing total of `r11_isa_measures`
(`processing_units_automated` is verification only; `processing_units_total` includes coordination). The share and the 0.5
units are declared design assumptions.

Composition with scoped authority. The share multiplies the layers borne: the layers are first reduced by the scope rule
(`effective_layers`) where scoped authority is on, and the result is multiplied by (1 - share). Implementation:
`enterprise_operations.coordination_required(..., scope, layer_time)` and `prepare_coordination(..., scoped, scope, automated,
automation_share)` extend the existing `scope` hook (a member can be scoped, automated, both or neither; the staged queue,
service and readiness arithmetic is the existing one), and `organization_tasks.prepare(..., automated, automation_share)` charges
reporting hours for `depth_borne x (1 - share)` layers while readiness and delay keep `depth_borne`. The plan records
`reporting_automation` (share) per automated member and the coordination plan `automated_people` and `automation_share`.
Arms: `hierarchy-auto-coord`, `dams-auto-coord` (the `-auto` arms with share 0.5), `hierarchy-isa-coord`, `dams-isa-coord`
(the `-isa-full` arms with share 0.5, so scoped authority, sandboxes and project sandboxes included).

Validated independently: the longitudinal validator re-runs the staged coordination phase with the same arguments and compares
the whole plan; re-counts `coordination_processing_units` from the present roster and adoption; the organization validator
requires `reporting_automation` exactly for present members of adopted guilds (equal to the declared share), recomputes the
network request and the verification capacity from reduced reporting hours and keeps the readiness on the layers borne.
Not validated: the realism of a time share taken over by automation, and that the 0.5 units are a sufficient or fair price.

## Processing charged to cash (`charge_processing`)
With `charge_processing` the day's processing units (automated verification, witness verification units of human reviews
(`verification_resource_units`), coordination automation) times `resource_units_per_processing_unit` (default 0.01, the
existing reporting conversion) are paid from cash as an operating expense. Timing: verification and review happen after the
day's production, so the charge is made at the end of that same day, from the cash that is left; it is added to
`operating_resource_units` (so the cash identity of the engine holds), to the new metric `processing_charge_resource_units`, and
the growth model sees it as a cost of that day. What cash cannot cover is not paid and is added to
`operating_unfunded_resource_units`. The morning funding fraction, growth and closure therefore respond from the next day on, not
on the same day (the morning funding fraction is fixed before the day's processing exists); this is a deliberate simplification.
Each day carries a `processing_charge` journal record (units by source, rate, requested, charged).

Variants (on top of `accounted-work-decisions`): `accounted-work-decisions-charged` (rate 0.01) and
`accounted-work-decisions-charged-r10` (0.1). Variant keys beginning with `automation.` are not applied to the shared history;
`arm_config(base, branch, arm, variant)` sets them on the automation block of every arm that has one (an arm without a block,
such as `control-hierarchy`, is unchanged), and `r11_study` records the variant in the prehistory receipt so that `arm` can pass it
(a receipt without one means none). Two further variants of the same kind switch record classes on
(`accounted-work-decisions-classes`, and `-classes-charged` with rate 0.01); the check run uses the latter.

Validated independently: the validator re-derives the day's units from its own re-derived records (automated units from the
`auto_verify` records, witness units from the reviews, coordination units from the roster), the requested and charged amounts
(charged = min(requested, cash left)), compares them with the `processing_charge` record, and applies the debit to its own cash,
metrics and growth observation. Not validated: the economic meaning of the rate.

## Collusive review (`attack='collude'`)
A new attack beside `forge`, `duplicate`, `censor` and `freeride` (longitudinal engine only). The cohort and window are those of
the existing attacks: the present members whose initial slot is below `ceil(n x attack_cohort_fraction)` are the attackers, inside
`[attack_start_day, attack_stop_day)`, and each spends `attack_budget_hours_per_day` / (present attackers) of his day. Attackers
submit forged records exactly as under `forge` (the observed work is inflated by the hours spent, and the audit alarm rings with
probability 0.6 instead of 0.02). New: when a forged record reaches a HUMAN first review, and the reviewer (the named
organizational reviewer when the organization model gives one, otherwise a deterministic keyed draw `collude_reviewer` among the
present members of the domain, in slot order) belongs to the cohort inside the window, the review ignores the audit alarm and the
threshold and accepts it (`colluded_acceptances`; `colluded_decisive_acceptances` counts those the rule would have refused). The
automated verifier cannot be bought: it applies the rule and its declared error (`forged_accepted_by_automation`). Records routed
to the human exception lane (audit sample, abstention, overflow, disputed) are exposed to collusion as any human review; in
sandbox arms a reviewer from another sandbox still needs the read grant, so a colluding reviewer outside the claimant's sandbox is
deferred like any other. The review record carries `fraudulent`, `rule_accept` and `colluded` (and `collusion_reviewer` when drawn).

Reported quantities: forged records submitted (`fraudulent_records_submitted`), accepted by automation
(`forged_accepted_by_automation`), accepted by collusion (`colluded_acceptances`), detected (`fraudulent_records_detected`),
credit obtained by forged records (`forged_credit_units`, divided by the cohort size for credit per attacker), and the attackers'
formal share at the end (read from the final state) against their share in the same world without the attack (the same arm of
the study without the attack variant). Variants `accounted-work-decisions-collude05` and `-collude20` set the attack on the base
configuration: cohort fraction 0.05 / 0.2, window from the adoption boundary to the horizon, budget 0.1 workday per cohort member
(a declared design assumption; the attack hours are taken from the attackers' own time).

Validated independently: the validator requires the work record's fraud label, re-derives for every review who the reviewer is
(the organizational verifier checks the named reviewer; the keyed draw is re-drawn for the non-organizational case), whether he
is in the cohort and the window, whether the rule alone would accept, and requires `accepted = rule or colluded`; the four
collusion metrics and the forged credit are re-summed from the reviews, automated records and commits. Not validated: that a
real bribe works this way. In particular there is NO laundering model: credit is non-transferable and the model has no assets
or payments between members, so a bribe has no flow to launder and none is modelled; the cost of bribery to the attackers is only
the time budget above, and the payment itself is not represented.
