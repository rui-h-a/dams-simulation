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

## Amendment 8 (2026-10-22): prospective statistical design of the 10,000-member main study and the 50,000-member comparison

Recorded before any confirmation world of either study was run or any of their summaries, ledgers or outcome files was opened. Freeze record: the commit that adds this amendment and its source files is the frozen analysis source; its SHA and the SHA-256 of the pilot outcome files used for Table A are entered in Table A when it is filled from the 10,000-member pilot (pilot prefix pilot/n10000-r11c-20261022, source 3b4d44676872fff8d602cafbc47b49f0df6f79b9). The pilot is used for variances, reference-arm anchors and measured cost only. Earlier amendments and their executed studies keep their original rule; where this amendment differs, a corrected re-analysis of those studies, if made, is stored separately with its own date and does not replace the original.

**Endpoints.** The primary family contains four endpoints with separate units: E1 net operating result per member-year (resource units per member-year); E2 decision regret per available member-hour (regret units per member-hour); E3 governance member-hours per member-year; E4 witness resource units per member-year. The legacy governance index adds member-hours to resource units. It is retained as a descriptive secondary outcome and is not a primary endpoint. All other outcomes in `research_tools/r11_outcomes.py` are secondary and exploratory.

**Substantive thresholds.** Each endpoint has an absolute threshold in its own unit, fixed from reference-arm pilot quantities that do not involve any contrast: Delta_1 is 0.02 times mean revenue per member-year (two points of operating margin); Delta_2 is 0.02 times the mean regret per available member-hour, provided that mean is positive; Delta_3 is 0.02 times mean available member-hours per member-year; Delta_4 is 0.02 times mean operating resource units per member-year. These are design conventions, not empirical estimates. Their values are in Table A and are not changed after any confirmation outcome is inspected. If the pilot reference mean regret is not positive, E2 moves to the secondary list and the family size becomes three.

**World count.** For each endpoint let s_k be the standard deviation of the pilot paired differences (dams-witness minus control-hierarchy), nu_p the pilot degrees of freedom, and sigma_k = s_k sqrt(nu_p / chi2_{nu_p; 0.20}) its one-sided 80 % upper confidence limit. R_k is the smallest integer R >= 2 with t_{0.975; R-1} s_k / sqrt(R) <= Delta_k / 2, and the target is R* = max(10, max_k R_k). The factor 2.262 of the earlier rule is the 0.975 quantile for 9 degrees of freedom; it is not the quantile of a three-world pilot (4.303) and is not used here. The t quantile is that of the confirmation study, and the pilot's small degrees of freedom enter only through sigma_k. R_k for the other arms is reported and does not set R*. The planning value uses the pilot point estimate s_k; the value with the 80 % upper confidence limit sigma_k and the Bonferroni value are reported beside it as sensitivity and do not set R*. Implementation: research_tools/r11_world_count.py, world_count().

**Scale comparison.** The 50,000-member study uses the 10,000-member pilot s_k in the same formula, with Delta_k computed from its own 50,000-member pilot reference arm. This assumes that the paired standard deviation of each endpoint does not increase with population; the assumption is not guaranteed, because shared world-level components do not average out. After the first n0 = min(10, floor(W_max/2)) worlds (at least 5), a script that prints only standard deviations compares s_50,k with s_10,k. If s_50,k <= 1.25 s_10,k for every k, the assumption stands. Otherwise R* is recomputed once with s_50,k in the formula, within W_max. No other look at the data decides the number of worlds. The ratios, their degrees of freedom and the branch taken are reported.

**World cap.** W_max = min(W_budget, W_calendar) for each study, with W_budget = floor((B - B_reserve) / (1.25 c)), c the measured cost of one complete world at that population, B the remaining research budget, B_reserve the archive and cleanup reserve, and W_calendar the number of worlds that the measured throughput completes before the simulation freeze. B, B_reserve, c, W_budget, W_calendar and W_max for each study are entered in Table A from the measured pilot cost before the first confirmation world starts. Worlds are numbered from 1000 consecutively with seed 20261017, and n = min(R*, W_max) worlds are run. If R* exceeds W_max the study is called a precision-limited confirmation. It does not support equivalence or null claims, and neither the cap, the thresholds nor the study name changes afterwards. For every endpoint the report states the number of worlds, the attained half-width and its ratio to Delta_k / 2, the minimal detectable difference at 80 % power, and the classification of the interval against plus and minus Delta_k.

**Inference and multiplicity.** The unit is the independent world. Intervals are paired Student-t intervals on world-level differences and are marginal at 95 %. Holm adjustment is applied within the four-endpoint family of each arm comparison; the comparison dams-witness against control-hierarchy is the primary contrast, and the remaining contrasts are supportive with their own families. A Holm-adjusted p-value over all contrasts of a study is also reported. Simultaneous intervals at the Bonferroni level 1 - 0.05/(2K) are reported next to the marginal intervals; the marginal intervals are not simultaneous and are not described as such. An unavailable test keeps its place in the family unless an endpoint was removed by a rule stated above.

**Degenerate samples, closure and missing pairs.** Paired differences with zero sample variance (relative tolerance 1e-12) have no interval or p-value. They are classified as structural identity (excluded from the family only if listed before data), unexplained identity, or constant nonzero offset. Indicators such as closure are analysed with the paired table, the exact McNemar test, an exact interval for the risk difference and, when no pair is discordant, the exact upper bound 1 - 0.05^(1/n) on the discordance rate. Firms that close remain in the roster; rate outcomes are defined whenever exposure is positive. A world-arm with an undefined denominator is counted by reason and its pair is dropped from the complete-pair estimate, which is labelled conditional. If more than 5 % of assigned worlds are undefined for a primary endpoint, that endpoint supports no confirmatory claim. Extreme-case bounds that replace each missing difference by the observed minimum and maximum are always reported. Operational interruptions are resumed under the same identity; failed validation is reported by count and cause and is not replaced.

**Table A (filled mechanically before freeze):** endpoint, unit, direction, pilot file SHA-256, reference mean of the anchor, Delta_k, s_k, nu_p, rho_k = s_k/Delta_k, R_k (point s, 80 % upper s, K = 4), R*, binding endpoint, W_max, planned n.

## Correction note (2026-10-23)

The 'Growth modes' row lists 'none, acquisition, organic+acquisition'. Acquisition without organic growth was never runnable: `research_tools/r11_design.py` refuses it because the installed-inventory contract sizes guilds and sites from the maximum capacity, and a small acquired firm cannot be installed without organic headroom (Amendment 7c and README already state this). The executable growth-mode comparison is none, organic (reference) and organic+acquisition. The row is kept as written for the record; this note states the executable set.

## 100,000-person admission decision (recorded 2026-10-24 16:10Z, Asia/Tokyo 10-25 01:10)

No 100,000-person world of this design has been run to completion or inspected
(the 2026-10-21 pilot attempt and the 500,000-member probe stopped during their
histories; no arm outcome exists). Arithmetic under the rule above:

- Remaining research budget: after the reserve for archiving and cleanup, the
  remaining cloud budget is smaller than the cost of one further 3-world batch of
  10,000-person worlds on Spot (about 26 h of a 16-vCPU machine with a 1.1 TB disk
  per batch after the validator fix a4e1fe3).
- Committed cost of the 10,000-person confirmation and the 50,000-person comparison
  on GCP: ten 10,000-person worlds need about four such batches and the
  50,000-person comparison considerably more, which already exceeds the remainder.
  If both run on the local workstation their cloud cost is zero, and the remainder
  is unchanged.
- Three complete 100,000-person worlds with a 25 % margin: the measured rate after
  the attack-cohort fix is about 21 s per simulated day at 80,000 members on the
  workstation, i.e. about 26 s at 100,000; one world (2-year history, two 5-year
  arms, final validation) needs roughly 40 h of a c4d-highcpu-16 plus a disk of
  more than 1 TB, i.e. 3 x 1.25 x 40 = 150 machine-hours with such disks, several
  times the remaining budget.


Decision: the 100,000-person study is not admitted. It is reported as not run, with
this arithmetic, and is not replaced by an extrapolated result.

## Table A: main-study world count from the 10,000-member pilot (recorded 2026-10-25, before any confirmation world)

Pilot: worlds 0-2, multinational / organic, 2 + 5 years, four arms, source 3b4d446 (science manifest equal to a4e1fe3 and to this commit). All 12 arms passed the full raw validators of a4e1fe3 (snapshot, daily evidence, summary, organization accounts) in standalone runs on the pilot machine (receipts `validation/*.json`, input SHA-256 re-checked locally); the plan-bound admission of 3b4d446 was not run because its plan binds the former validator, whose quadratic step kept the arms in validation for more than 22 hours. Pilot worlds are not used for inference.

| Endpoint | Pilot paired differences (worlds 0-2) | Threshold delta_k | Pilot SD | R_k | R_k (80 % upper SD) | R_k (Bonferroni) |
|---|---|---|---|---|---|---|
| net_operating_result_per_member_year | -25.61, -25.77, -25.67 | 12.48 | 0.083 | 10 | 10 | 10 |
| decision_regret_per_available_member_hour | 2.318e-06, 1.502e-06, 1.442e-06 | 8.562e-07 | 4.89e-07 | 10 | 25 | 12 |
| governance_member_hours_per_member_year | -1.558, -1.587, -1.558 | 4.776 | 0.0167 | 10 | 10 | 10 |
| witness_resource_units_per_member_year | 18.56, 18.62, 18.63 | 0.1642 | 0.0392 | 10 | 10 | 10 |

R* = max(10, max_k R_k) = 10. The main study runs confirmation worlds 1000-1009, seed 20261017, arms control-hierarchy (reference), control-process, dams-witness and linear-witness, from this commit, on the local workstation (Rui decision 2026-10-25; GCP budget exhausted). Every endpoint's point requirement is at the minimum of 10; the regret endpoint would need 25 worlds at the 80 % upper SD and 12 under Bonferroni, reported as sensitivity values that do not set R* (Amendment 8).

## Amendment 10: execution and retention for the main study on the workstation (recorded 2026-10-25, before any confirmation world)

Science is unchanged (same commit family, design, arms, worlds 1000-1009, seed, endpoints, Amendment 8 inference). Changed are the machine and the retention of intermediate states:

- Machine: the local workstation (Apple M2 Pro, 12 cores) instead of GCP, because the cumulative cloud budget is exhausted. Simulation is deterministic per world identity, so the machine does not change results; any cross-machine difference found by the reproduction check is reported, not hidden.
- Output location: the external research volume (AGENTS.md: raw outputs, recovery states and evidence live there; the internal disk holds only bounded rebuildable scratch).
- Retained for every arm: final_state.json, final_state.sqlite (the complete original journal in its chunked lossless form and the terminal state), summary.json, timeseries.csv, arm.json receipt, and the world's prehistory branch checkpoint and receipt; plus plan.json, events.jsonl, inference.json and a full SHA-256 manifest of everything retained.
- Retired after the arm's receipt exists and its final state has passed verify_snapshot and the full validators: the yearly intermediate checkpoints of that arm and superseded working databases. They are derived states that the deterministic engine regenerates from the retained branch checkpoint and source; their SHA-256 values are kept in the manifest before deletion. Measured sizes at 10,000 members (pilot): about 7 GB per final state and per branch checkpoint against about 6-7 GB per yearly checkpoint, so retaining every yearly checkpoint would need about 1.7 TB, which the research volume cannot hold.
- Not changed: no output is truncated, no failed or missing world is dropped, and no world is replaced.

## Amendment 11: storage path for the main study (recorded 2026-10-25, before any confirmation world)

The research volume cannot hold the retained outputs of Amendment 10 (about 312 GB). As the storage rule allows, the complete outputs go to the study's cloud bucket instead: each confirmation world runs as its own batch (same arguments, one world), its raw-admitted inference report is produced on the workstation while all original evidence is present, the complete batch directory (final states with their journals, receipts, series, summaries, plan, events, inference and a SHA-256 list) is uploaded and every object is checked against the local file (path, size, CRC32C), and only then is the local copy removed. While a world runs, only the newest complete yearly checkpoint of each arm and prehistory is kept (older ones are hashed and deleted; the newest is all a resume needs). The admitted batch reports are pooled with research_tools/r11_pool.py. Science, worlds, arms and inference are unchanged. Before the cloud project is closed, the uploaded worlds still need a complete copy outside the cloud.

## Amendment 12: cloud execution of the main study, the 50,000-member comparison and the 100,000-member scale check (recorded 2026-10-25T14:27Z, before any confirmation world of these studies finished)

The owner decided on 2026-10-25 that all formal simulations run on the project's cloud machines with the cloud resources they need. Science of the main study is unchanged (design, arms, worlds 1000-1009, seed 20261017, endpoints, Amendment 8 inference, this commit family with the same science manifest).

- **Main study (10,000 members).** Each world 1000-1009 runs as its own batch on its own machine (four arms in parallel). The workstation run of world 1000 is stopped; none of its arms had finished and no outcome of it was read. Its partial state is discarded and world 1000 runs again under the same identity, as the protocol allows for operational interruptions.
- **Scale comparison (50,000 members).** Worlds 1000-1009, seed 20261017, arms control-hierarchy (reference), control-process and dams-witness, multinational context, organic growth, 2 + 5 years, one world per machine. Amendment 8 takes Delta_k from the 50,000-member pilot reference arm, but that pilot stopped at day 1461 of 2557 and has no horizon outcome; Delta_k is therefore taken from the 10,000-member pilot reference arm in the same per-member units (Table A). With the 10,000-member s_k every R_k is at the minimum, so R* = 10 and W_max = 10 (calendar). The variance check of Amendment 8 (s_50,k against 1.25 s_10,k) is run once at least five worlds have finished, printing standard deviations only; because all ten worlds run at the same time, a recomputed R* above 10 makes the study precision-limited and adds no worlds.
- **100,000 members.** The admission rule's budget input changed: the owner provided cloud resources on 2026-10-25. Calendar arithmetic: the 50,000-member pilot ran its history at about 44 s and its arms at 97-150 s per simulated day; scaling per-member cost linearly, a 100,000-member world needs about 18 h of history and 5-6 days per arm (arms in parallel) plus validation, which finishes before the simulation freeze (2026-11-06) if started on 2026-10-26. Three worlds (1000-1002) with arms control-hierarchy and dams-witness are therefore admitted as a scale check. The rate at 100,000 members is an extrapolation, not a measurement; if the worlds have not finished at the freeze they are reported as incomplete and nothing is extrapolated in their place. They are reported with their achieved precision and are not confirmatory.
- **500,000 members.** Not run. The probe's first 20 simulated days took 9,126 s in total, with the per-day time rising from 61 s to 853 s; a five-year arm is far beyond the calendar.
- **Retention and storage.** As Amendments 10 and 11: while a batch runs, only the newest complete yearly checkpoint of each arm and history is kept; after the arm's receipt and validators pass, intermediate checkpoints are retired with their SHA-256 recorded; every retained file (final states with their journals, receipts, series, summaries, plan, events, inference, retirement list) is listed with SHA-256 and uploaded create-only to the study bucket, each object checked against the local file by MD5; the machine is released only after every file verified. The raw-admitted inference runs on the machine while all original evidence is present.
- **Machines.** One machine per world: 8-vCPU general-purpose machines with local NVMe storage (C2D for the 50,000- and 100,000-member worlds, N2 for the 10,000-member worlds, one 50,000-member world on N2 by quota). Simulation is deterministic per world identity; machine differences change elapsed time, not results, and the independent reproduction check reports any difference it finds.

Note to Amendment 12 (2026-10-25T15:03Z, before any of these worlds finished): machines as launched. C2D capacity was exhausted in all us-central1 zones, and the regional local-SSD quota per machine family was used up, so the machines are: the ten 10,000-member worlds on n2-standard-8 (us-central1, one local SSD each); the ten 50,000-member worlds on n2-standard-8 with two local SSDs (world 1009 in us-central1, worlds 1000-1007 in us-east1, world 1008 in us-east4); the 100,000-member world 1000 on n2-highmem-8 (us-central1) and worlds 1001-1002 on c2d-highmem-8 (us-east1), four local SSDs each. Outputs from every region go to the same us-central1 bucket. The science arguments and seeds are those of Amendment 12.

## Amendment 13: 10,000-member supplementary studies (recorded 2026-10-26T16:53Z, before any of their worlds was run)

The protocol's 10,000-member rows for growth modes, adoption timing, evidence process and the ten-year horizon, and a demand shock, are run on the cloud machines of Amendment 12, each with the main study's world identities 1000-1009, seed 20261017, multinational context, two years of history and the source and science manifest of the main study. Arms: ten-year horizon (2 + 10 years) control-hierarchy and dams-witness; no growth and organic growth with acquisitions (2 + 5 years) control-hierarchy and dams-witness; adoption timing control-hierarchy, dams-staged-witness and dams-triggered-witness; evidence process control-hierarchy, dams-central and dams-consensus; demand shock (variant demand-shock, a 30 % demand fall one year after adoption, recovering after 180 days) control-hierarchy and dams-witness. Each batch carries its own reference arm; the main study supplies the organic-growth, fixed-adoption and witnessed-record arms of the same worlds, which are deterministic functions of the world identity. World count: ten per study (R* of Amendment 8, whose point requirements are at the minimum), run as five batches of two worlds; a study whose attained half-widths exceed Delta_k/2 is reported as precision-limited. Worlds unfinished at the simulation freeze are reported as unfinished. Retention, upload and verification as in Amendment 12. Smoke runs at 60 and 120 members checked that every configuration executes; the demand shock needs a horizon longer than one year after adoption.

## Amendment 13a: installation of a large acquired firm (recorded 2026-10-26T18:41Z, after the 10,000-member acquisition batches failed in their history and before any of their arms ran)

All five 10,000-member organic-plus-acquisition batches of Amendment 13 stopped in the history with "target installed infrastructure overbooked": the design installs the acquired firm as one more guild, department and site of the acquirer, and at 10,000 members the simulated target (1,913 people in world 1002) is larger than one guild of the acquirer (750). The design now installs a target that does not fit as as many guilds, departments and sites of the acquirer's sizes as it needs. Configurations whose target fits are unchanged (identical configuration digests for the 300-member listed and 120-member multinational acquisition worlds checked; acquisition tests 19/19 pass). This changes design inputs, so the acquisition study runs from this commit with its own reference arm in each batch; its contrasts are within-batch and are not pooled with the main study.
