# R11 formal study protocol (pre-registration draft)

Status: draft. It is frozen, with a commit hash recorded in this file, before any
confirmation world is inspected. Pilot worlds are never part of inference.

## Aims and estimands (ADEMP)

- Data-generating mechanism: `research_tools/r11_design.py` at the frozen commit
  (model `longitudinal-enterprise-person-context-11-2`), context and growth mode as
  stated per study, prehistory `pre_years` before adoption, `post_years` after.
- Estimand: for each outcome Y and comparison arm A against the reference arm B,
  the mean paired difference E[Y_r,A − Y_r,B] over independent worlds r of the
  declared scenario. Worlds share initialization and keyed exogenous streams up to
  the adoption boundary and branch from one complete state.
- Inference unit: the independent world. Members, guilds, dates, resumed runs and
  machine changes do not add repetitions.

## Studies

| Study | Population | Context / growth | Horizon | Arms |
|---|---|---|---|---|
| Main | 10,000 initial | multinational / organic | 2 y prehistory + 5 y post | control-hierarchy (reference), control-process, dams-witness, linear-witness |
| Scale comparison | 50,000 initial | multinational / organic | 2 + 5 y | control-hierarchy, control-process, dams-witness |
| Conditional | 100,000 initial | multinational / organic | 2 + 5 y | control-hierarchy, dams-witness |
| Ten-year robustness | 10,000 | multinational / organic | 2 + 10 y | control-hierarchy, dams-witness |
| Growth modes | 10,000 | multinational / none, acquisition, organic+acquisition | 2 + 5 y | control-hierarchy, dams-witness |
| Contexts | 10,000 | startup, unicorn, listed, traditional, century / organic | 2 + 5 y (startup: 0 + 5 y founding adoption) | control-hierarchy, dams-witness |
| Adoption timing | 10,000 | multinational / organic | 2 + 5 y | dams-staged-witness, dams-triggered-witness vs control-hierarchy |
| Evidence process | 10,000 | multinational / organic | 2 + 5 y | dams-central, dams-consensus vs dams-witness |

The 100,000-person study is admitted only if the measured full-workload time,
memory, storage and recovery fit the remaining budget and calendar; the decision is
recorded before any of its worlds is inspected and does not depend on results.

## Primary outcomes (per member-year or per available member-hour)

1. `net_operating_result_per_member_year` (resource units / member-year)
2. `decision_regret_per_available_member_hour` (regret units / member-hour)
3. `governance_cost_per_member_year` (resource units / member-year)

All other outcomes in `research_tools/r11_outcomes.py` are secondary and reported
with the same paired summary, without multiplicity adjustment and labelled as such.

## Substantive thresholds and precision

For each primary outcome the substantive threshold `δ` is 2 % of the mean of the
reference arm across pilot worlds. The confirmation world count is

    R = max(10, ceil((2.262 · s_pilot / (δ / 2))²))

using the largest pilot standard deviation of paired differences among the three
primary outcomes for the main comparison (dams-witness vs control-hierarchy), with
2.262 the 0.975 Student-t quantile for 9 degrees of freedom. If R exceeds the
computational capacity that the budget and calendar allow, the achieved precision
is reported as a limitation; the threshold is not changed after results are seen.

## Scale-specific world counts and the 100,000-person admission rule

Recorded on 2026-10-17 before any pilot or confirmation outcome was inspected.

- 50,000 members: a two-world, two-arm pilot measures full-horizon time, memory,
  disk and recovery. Its two worlds cannot estimate variance reliably, so the
  50,000-person world count uses the 10,000-person pilot standard deviation of
  the paired differences in the same formula, with a minimum of 10. Organization-
  level outcomes average over more members at larger scale, which makes this
  conservative under the model's assumptions; the assumption is reported.
- 100,000 members: admitted only if, after the committed cost of the 10,000-person
  confirmation and the 50,000-person comparison, the remaining research budget
  (excluding the reserve for archiving and cleanup) covers at least three complete
  100,000-person worlds at the measured C4D rate with a 25 % margin, and the
  calendar allows completion before the simulation freeze. The admission decision
  and its arithmetic are recorded before any 100,000-person world is inspected.
  If admitted, the study uses the arms control-hierarchy and dams-witness and is
  reported as a scale check with its achieved precision, not as a confirmatory
  study.

## Multiplicity

Holm adjustment across the three primary outcomes within each arm comparison.
Intervals are paired Student-t intervals on the world-level differences.

## Missing pairs and closure

Modeled closure is an outcome and remains in the analysis with its exposure.
Operational interruption is resumed under the same world identity. Any world whose
pair is not complete by the analysis freeze is reported by count and reason; the
complete-pair summary is labelled as conditional on completion.

## Seeds and worlds

Seed 20261017. Pilot worlds 0–2 (excluded from inference) ran on R11.1 (`0413e38`);
R11.2 adds paths with small default coefficients, so the pilot variance is used as an
approximation for the world-count rule and the approximation is reported. Confirmation worlds
start at 1000 and run consecutively; no world is skipped or replaced.

## Amendment 1 (2026-10-18): model version R11.3 and the small-enterprise studies

Recorded before any confirmation world of any R11.3 study was run.

R11.2 is withdrawn for inference. Its coordination requirement assigned one
manager per governance domain, so reports per manager grew with headcount;
above about 80 reports per manager the requirement exceeded the 0.4 workday
service cap, coordination debt grew without bound and output collapsed in
every arm (a 300-member startup fell to 2-24 members; a 10,000-member
multinational prehistory fell to about 5,300). R11.3 (commit 65d6b4e) adds
span-based supervision, sources the remaining trait correlations, gives risk
tolerance a path into voluntary exit and replaces fixed 1e-11 accounting
tolerances by a-priori rounding bounds. Pilot outputs of R11.2 are kept but
do not enter the world-count rule.

Small-enterprise studies. Context startup, organic growth, adoption at
founding (pre_years 0, post_years 5, 1,827 days with tail), initial sizes 30,
120 and 300 members, arms control-hierarchy (reference), control-process,
dams-witness and linear-witness. The R11.3 pilot (worlds 0-2, commit 224413c,
whose simulated values equal 65d6b4e; only validation checks differ) gives,
under the rule above with the largest of the three primary outcomes,
R = 6,688 (30 members), 665 (120) and 57 (300). In each case the largest
requirement comes from decision regret per available member-hour, whose
paired standard deviation is large relative to 2 % of its reference mean.

Capacity cap, fixed now from compute cost rather than from results: at most
300 confirmation worlds per size. Where R exceeds the cap (30 and 120
members), 300 worlds are run and the achieved half-width of each primary
interval is reported against delta; the threshold is not changed. The
300-member study runs R = 57 worlds. Confirmation worlds are 1000, 1001, ...
consecutively with seed 20261017; no world is skipped or replaced. Holm
adjustment and paired Student-t intervals as above. Worlds that fail
validation are reported by count and cause and are not replaced.

## Amendment 2 (2026-10-19): mid-life adoption in four organizational contexts

Recorded before any confirmation world of these studies was run. Contexts
traditional, century, listed and unicorn; 300 initial members; organic growth;
adoption at the boundary after a two-year generated history (pre_years 2,
post_years 5); arms control-hierarchy (reference) and dams-witness, both
branched from the same complete pre-adoption state. Pilot worlds 0-2 (commit
3547fac; dams_sim identical to 65d6b4e) give, by the rule above, R = 65
(traditional), 11 (century), 376 (listed) and 119 (unicorn); decision regret
sets R except for the century context. Capacity cap fixed from compute cost
before any confirmation world: at most 120 worlds per context, so the listed
context runs 120 worlds and reports its achieved precision. Worlds start at
1000, consecutive, seed 20261017; operational interruptions are resumed under
the same world identity; validation failures are reported, not replaced.
