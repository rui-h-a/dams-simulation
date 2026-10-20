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

## Amendment 3 (2026-10-19): two-firm acquisition study

Recorded before any confirmation world of this study was run. Design commit
69f050b (research_tools/r11_targets.py, research_tools/r11_design.py;
dams_sim identical to 65d6b4e), executed at 93e5b87 (identical design code
and dams_sim). Acquirer: listed context, 300 initial members, growth
organic+acquisition, two-year generated history and five years after the
adoption boundary (pre_years 2, post_years 5). One target per world: a
separately simulated founding-stage hierarchy firm of 60 initial members at
fixed capacity, offered from the start of the second post-adoption year for
180 days; price = net cash + 14 x last-year operating surplus; integration
window 20 workdays, cost 2.5 % of the price, normal payroll, excess exit
3.36e-3 per workday; the price may be borrowed in full. Arms
control-hierarchy (reference) and dams-witness, branched from the same
complete pre-adoption state. Two studies with separate worlds: integration
mode preserve-autonomy (variant baseline) and centralize (variant
ma-centralize).

Pilot worlds 0-3 (both modes; every acquisition closed on day 1097 and was
integrated on day 1123 in both arms of every world) give, by the rule above,
R = 237 (preserve-autonomy) and 334 (centralize). Capacity cap fixed from
compute cost before any confirmation world: at most 120 worlds per mode,
worlds 1000-1119, seed 20261017; each mode reports its achieved precision.
Primary outcomes, reference arm, inference and failure handling as above.
Operational interruptions are resumed under the same world identity;
validation failures are reported, not replaced.

## Amendment 4 (2026-10-19): assumption robustness of the founding-stage result

Recorded before any world of this study was run. Purpose: the
institutional-counterfactual and structural layer of validation. The
founding-stage 300-member study (Amendment 1; startup, organic growth,
pre_years 0, post_years 5, worlds 1000-1056, seed 20261017) is repeated
under each of ten declared assumption variants of research_tools/
r11_design.py VARIANTS, one family of assumptions at a time:
independent-traits, big-five-employees, no-origin-preferences,
evaluation-bias, strong-factions, no-factions, high-turnover,
low-turnover, reversed-share-response, demand-shock. Arms control-hierarchy
(reference) and dams-witness. Executed at 93e5b87 (dams_sim identical to
65d6b4e). The same 57 world identities as the baseline study are used, so
each variant result is paired by world with the baseline.

Reported for every variant, without selection: the paired DAMS-minus-
hierarchy differences for all outcomes with 95 % intervals, and the
difference between that contrast and the baseline contrast on the same
worlds. A conclusion of the baseline study is called robust to a variant
only if the sign of the net-operating-result contrast is unchanged and its
interval excludes zero; magnitudes are reported, not tested against a
threshold. No variant is added, removed or re-run after results are seen;
validation failures are reported, not replaced.

## Amendment 5 (2026-10-19): R11.4 sickness spells as a structural robustness check

Recorded before any world of this study was run. Model R11.4 (commit
e027d28) adds an opt-in absence model in which sickness comes in spells
whose onset and length depend on age (variant sickness-spells; sources and
calibration in research_tools/r11_design.py). In the default daily mode
R11.4 reproduces R11.3 configurations, ledgers and outcomes exactly, so the
studies below are paired by world with the R11.3 baselines:

- founding-stage startup, 300 members, pre_years 0, post_years 5, worlds
  1000-1056 (baseline: Amendment 1);
- traditional context, 300 members, pre_years 2, post_years 5, worlds
  1000-1064 (baseline: Amendment 2).

Arms control-hierarchy and dams-witness. Reported without selection: all
paired DAMS-minus-hierarchy differences, the change from the baseline
contrast on the same worlds, and simulated sick absence per member-year
against the UK 2024 value of 4.4 days (held out from calibration). The
same robustness criterion as Amendment 4 applies.

## Amendment 6 (2026-10-20): demand regime check (variant demand-matched)

Recorded before any world of this study was run. Reason: reading the
Amendment 4 results showed that the demand-shock variant changed order
counts but no operating outcome. The template fixes the market price
(price_adjustment_rate 0), and orders arrive at one unit per member per
workday times the demand schedule, so 67-90 % of order units expire in every
context and output is always capacity-bound (hierarchy arms of the formal
studies; delivered share 0.10-0.33). US capacity utilization (Federal Reserve
G.17, FRED series TCU) averaged 75.9-78.5 % in 2021-2025, so real firms
usually have spare capacity. In a capacity-bound firm every hour of
governance work is lost output; the R11.3 contrasts may therefore not hold
where demand binds.

Variant demand-matched (research_tools/r11_design.py DEMAND_MATCH) scales
base orders per workday by a context factor so that arriving orders
equal 78 % of the units the hierarchy arm delivers when it is
capacity-bound, i.e. a target capacity utilization of 0.78. The factors were measured on pilot worlds
0-2 (hierarchy arm), not on confirmation worlds: startup 0.166, traditional
0.262, listed 0.226, century 0.257, unicorn 0.078. Price, elasticity and all
other parameters are unchanged; dams_sim is unchanged (R11.4 code in its
default daily-absence mode reproduces R11.3 exactly). Studies, paired by
world with their baselines:

- founding-stage startup, 300 members, pre_years 0, post_years 5, worlds
  1000-1056 (baseline: Amendment 1);
- traditional context, 300 members, pre_years 2, post_years 5, worlds
  1000-1064 (baseline: Amendment 2).

Arms control-hierarchy and dams-witness. Reported without selection: all
paired DAMS-minus-hierarchy differences, the change from the baseline
contrast on the same worlds, the realised utilization of each arm
(delivered units divided by delivered units of the same world and arm in
the capacity-bound baseline; target about 0.78 for the hierarchy, not tuned
after the run) and the delivered share of arriving orders. A local smoke
run at 30 members, world 0, one year (not a confirmation world) delivered
all arriving orders in both arms, which led to this correction of the
target's wording before any confirmation world was run. The Amendment 4 robustness criterion applies. No world is added,
removed or re-run after results are seen.

## Amendment 7 (2026-10-21): adoption timing, record process, growth and horizon at 300 members

Recorded before any world of these studies was run. Reason: the protocol
places these comparisons at 10,000 members, where their cost competes with
the main and scale studies within the remaining compute budget. At 300
members they cost a few dollars each and cover the factors that the
10,000-member design cannot yet reach. They do not replace the
10,000-member rows, which stay in the protocol and are reported as not
run if they are not run.

All four studies: traditional context, 300 initial members, two years of
history, worlds 1000-1064, seed 20261017. These are the world identities of
Amendment 2, so every study is paired by world with that baseline (the
dams-witness and control-hierarchy arms of formal/r11-3-n300-traditional-mid).
The reference arm in each study is control-hierarchy. The commit is the
dev-branch head at launch; dams_sim is identical to e027d28 in its default
daily-absence mode, which reproduces r11.3.

- 7a, adoption timing: arms control-hierarchy, dams-staged-witness
  (four guild waves six months apart) and dams-triggered-witness
  (adoption when review backlog per active member reaches the threshold).
  Organic growth, 2 + 5 years.
- 7b, record process: arms control-hierarchy, dams-central and
  dams-consensus. Organic growth, 2 + 5 years.
- 7c, no growth: arms control-hierarchy and dams-witness, growth none,
  2 + 5 years. Acquisition-only growth is not supported by the study
  design (r11_design.base_config) and is not run.
- 7d, ten-year horizon: arms control-hierarchy and dams-witness, organic
  growth, 2 + 10 years. Outcomes are reported for the whole horizon.
  Year-by-year paths are reported where the time series allow.

World count: fixed at 65, the count the Amendment 2 pilot rule gave for
this context; the achieved precision is reported against the same
threshold. All outcomes are reported without selection, together with
the difference from the matching Amendment 2 arm on the same worlds where
the arm exists there. No world is added, removed or re-run after results
are seen.
