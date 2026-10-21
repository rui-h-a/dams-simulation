# Fixed external evidence, five authority policies

This experiment isolates authority allocation under one externally supplied
evidence history. It complements the endogenous-behaviour experiments. It does
not generate work, model a company, estimate decision quality or identify actual
influence, fairness, survey calibration or adoption effects. Replaying an input
does not create a new independent ABM world. No paid service or Model is used.

From the repository root, with Python3.11+ and no extra dependencies:

```sh
python3 -B -m research_tools.fixed_contribution_replay --tape input.json --output NEW_RESULT.json
python3 -B -m unittest tests.test_fixed_contribution_replay
```

An openly specified stress fixture can be generated with
`python3 -B -m research_tools.fixed_replay_fixture --population 10000 --output NEW_INPUT.json`.
It has two domains, square-pattern credit, staggered confirmation, record-specific
revocation, eligibility removal/regrant and exit, observed at days0/1/30/90/180/365.
Every person initially belongs to both domains. Every tenth record explicitly
splits credit1/4 to its primary domain and3/4 to the other. Quantities and dates
are designed controls, not observed frequencies or empirical coefficients.

The output parent directory must already exist; the output itself must not.
Publication writes and fsyncs a temporary file, then atomically links the final
name without replacing old results. Input JSON rejects duplicate keys and NaN.
The input ceiling is16MiB and output ceiling64MiB. These byte ceilings do not
constitute a whole-process RAM/CPU admission or permission to run large jobs.
The shared `run.sh` enterprise path is unchanged; this is an explicit research
module, not a claim that the remaining one-line full-model workflow is complete.

## Tape and event semantics

The exact top-level fields are `schema_version:1`, `time_unit:"calendar_day"`,
`alpha` strictly between0 and1, positive `half_life_days` (or null for none),
positive nondecreasing `tier_weights`, `domains`, `members`, `events`, and
strictly increasing `query_days`. Maximum roster100000, domains64, events1000000,
queries256. Members have `member_id` and `appointment_days`, an object mapping
their initially eligible domains to nonpositive appointment-day integers.
Eligibility is an explicit input, not inferred from credit or demographics.

Every event has a unique `event_id`, nonnegative integer `day` and `sequence`,
and `kind`. Events must be strictly ordered by `(day,sequence)`; no hidden
sorting changes event precedence. Queries occur after all events on that day.
An event after the final query refuses instead of being silently discarded.

* `record`: `claim_id`, `earned_day<=day`, nonnegative `credit`, and nonempty
  `attributions`, each containing `member_id`, `domain`, positive `fraction`.
  Unique member/domain pairs and fractions summing to one prevent duplicate
  cross-domain credit. A record is visible evidence, not researcher-known true
  individual contribution; the tape does not assert that a team outcome has a
  uniquely correct individual attribution.
* `confirm`: an existing `claim_id`; one confirmation per record. Only this
  transition makes credit available for authority. Same-day sequence matters.
* `revoke`: an existing, confirmed, unrevoked `claim_id`. It removes that
  record's remaining credit prospectively; it does not subtract its original
  amount from other records or rewrite earlier decisions.
* `eligibility`: known `member_id`, known `domain`, Boolean `eligible`.
  Grants start domain appointment tenure at the grant day; removal and regrant
  restart that tenure. Redundant transitions refuse. Previously confirmed
  credit in that domain remains recorded and can count after a later grant.
* `exit`: known active `member_id`. Exit removes eligibility in all domains
  permanently in this protocol; histories remain. Re-entry is not implemented
  here and must not be simulated by inventing a new account for the same person.

Credit for record r available at t is `v_r * fraction * 2^(-(t-earned_day)/H)`
only after confirmation and before revocation. Delayed confirmation therefore
does not rejuvenate old work. Credits aggregate per person and domain. Moving
eligibility does not transport old credit to a different domain. Fractional
cross-domain attribution is declared once in the original record and shared by
all policies. This is a fixed-tape convention, not a calibrated rule for merging
contributions from heterogeneous companies or units.

## Policies and outputs

At every query, the eligible set, confirmation prefix, decay, attribution and
appointments are identical for all five policies:

* Equal eligibility:1/n.
* Linear confirmed credit:C/sum(C).
* Sublinear DAMS:C^alpha/sum(C^alpha), using the existing overflow-safe helper.
* Performance tiers: the same confirmed C ranked through declared tier weights.
* Appointment-tenure tiers: days since the latest domain appointment ranked
  through those weights. This is an appointment proxy, not measured employment
  seniority, education, experience or contribution. Days versus years do not
  change its ordering in this isolated allocation comparison.

Tier ties average positional weights across the entire tied block, eliminating
ID-based tie privileges. As in the reference helper, rank r uses tier
`floor(r*K/n)`; K>n can leave tiers unused. N2,K4,weights1/2/4/8 therefore gives
the two untied ranks weights1 and4, not1 and8. All-zero confirmed credit uses
the ABM equal-among-eligible fallback for linear/sublinear/performance policies;
it does not change the separate contract's all-zero suspension rule. Tenure
tiers retain their own appointment scores. An empty eligible set suspends all
five policies and reports null concentration/distance, not invented zeros.
Positive credit that underflows binary64 after decay refuses; it must not be
silently treated as genuinely zero credit and trigger the equal fallback.

Results retain every query/domain, member-level eligible credit, appointment
tenure and all five share vectors. Gini describes formal-share concentration;
total variation from linear is a dimensionless allocation distance. Neither is
fairness or time. Record-status counts cover all domains and are labelled as
such. Unconfirmed and revoked records remain counted, not silently dropped.
Input, replay source and authority source SHA256 are attached; timing is printed
separately so deterministic result bytes can be compared across repeats.

The implementation uses no future observations in allocations, no RNG, no
worker identifier and no policy-specific tape. Complexity is O(Q*(R+A+P*D*NlogN))
with records R, attribution entries A, population N, domains D, queries Q and
five policies P; retained share vectors cost O(Q*D*N*P). Replay from the same
immutable tape is deterministic recomputation, not checkpoint restoration of a
longitudinal ABM. Full input/output evidence should be retained. Synthetic tapes
and exact arithmetic checks must stay labelled as mechanism evidence.
