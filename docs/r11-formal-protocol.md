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
