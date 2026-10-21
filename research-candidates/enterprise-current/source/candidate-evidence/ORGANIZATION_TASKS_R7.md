# Conditional organization/task mechanism (r7)

This isolated candidate derives from r6-joint-1 (core `236c52d299676bf18f1f73b31a3c566e40ee5773a5ef8da92d05e266f7cbbec5`). `organization_tasks` is absent by default. No existing frozen study adopts it. Engineering controls, including the N30 sixty-day executable profile, add **zero formal worlds**. Calibration, parameter identification, real-company reconstruction and external validation remain incomplete.

## Mechanisms and observables

The three networks represent different functions. The formal reporting layer is a breadth-first tree within each controlled task department; `management_span` determines parent and depth, without altering guild population or formal voting weights. It reserves `depth × reporting_hours_per_layer` of the existing workday budget, reduces production readiness to `1/(1+depth×reporting_readiness_penalty_per_layer)`, and delays evidence readiness by `depth×reporting_delay_days_per_layer` calendar days. Its parent/depth map and individual readiness/ready-day appear in the raw journal. These depth effects are conditional modelling assumptions, not estimated effects of management.

Collaboration uses bounded deterministic contacts, preferentially in other task departments, sampled from the actual roster without randomness or demographic proxies. Presence and actual cooperation determine the average help input in the existing production equation. A contact reserves `collaboration_hours_per_contact`; a missing/absent contact provides no free input. The generator is O(N×bounded contacts plus department inventory); it does not materialize all possible cross-department edges.

Verification is a separate directed, bounded assignment layer. It excludes self-review, a direct reporting superior and a direct reporting subordinate. A cross-department contact is preferred; a one-department organization may use a non-reporting peer. This structural separation does not establish reviewer independence in the statistical or political sense and does not prevent collusion. Paid verification capacity is the residual of network reservations after reporting/collaboration, multiplied by funding, divided by `verification_hours_per_claim`. A review consumes one quota, retains the actual reviewer ID and time, and still uses the inherited noisy audit/acceptance sensor. No available paid reviewer causes an explicit deferral to the next day; it creates no credit or free review. The model does not claim to identify capture risk or empirically calibrate detection rates.

Departments have predeclared `(production, quality, learning)` weights summing to one. They compete for the *same* residual individual time after inherited care, governance, training, coordination, review and appeal reservations. Increasing one component has an opportunity cost. Production weights cap effort; quality time reduces measurement noise to `review_error_sd/(1+quality_measurement_efficiency×quality_hours)`; learning time contributes to the existing bounded skill-learning update. Network, quality and learning allocations enter funded time booking and fatigue. Historical `*_hours` fields mean normalized fractions of a model member workday, not claims about sixty-minute physical hours. The three new time metrics report funded **allocations**, while actual learning updates are independently checked from work records. Tail/closed/no-production days do not become training outcomes simply because time was allocated.

A department d>0 requires prior-day deliverables from d−1. At morning inventory I, potential P and requirement r, its dependency factor is `min(1,I/(rP))` (one for r=0 or P=0). Actual downstream output consumes r input units per work unit. Every department's output creates one deliverable token per work unit. The stock update is `I[t+1]=I[t]+produced[t]−consumed[t]`. Same-day output cannot bootstrap another task. Tokens are enabling knowledge/artifact inputs, do not carry cash or balance-sheet asset value, and are not extra saleable products. Existing output measures count work across tasks; they do not claim a physical material supply chain or consumer final-goods accounting. A chain is an explicit acyclic experimental hypothesis, not a calibrated universal enterprise task graph.

The controlled `department_count` is independent of r6's dynamic installed infrastructure department inventory. Observables use `task_department_count` to distinguish them. Identity modulo this count is an explicit neutral experimental assignment; it is not a realistic departmental personnel allocation rule. Entries, exits, actual sites, guild moves, mergers and M&A retain the existing r6 history and update the roster. M&A itself remains the inherited separately tested mechanism; r7 does not substitute population increase for an acquisition.

## Provenance and verification

`organization_plan`, `organization_tasks`, actual `review` assignments, `organization_verification_deferred`, individual `work.organization`, daily time/stock observations, and `organization_tasks_state` are retained. Checkpoints bind the r7 source, configuration and full ledger. `None` stays omitted from configuration/state and does not add RNG draws. Fork changes to optional world assumptions are rejected using the union of old/new configuration keys.

`research_tools/validate_organization_tasks.py` does not call the producer's topology/prepare/allocation/finish/reviewer functions. It independently reconstructs role contacts, local time arithmetic, potential and quality equations, prior-day dependency conservation, actual verification quotas, all calendar days, historical sites, knowledge stocks, learning/forgetting and fatigue. It rejects coherent final-stock/day-end forgeries and restored learning/fatigue inflation. The inherited raw validator continues to check lifecycle, funded time, queues, enterprise market/cash and acquisition accounts. Shared parsers, calendar utility and keyed RNG are disclosed; this is additional internal correctness evidence, **not complete independent empirical validation**.

The new verifier pins its own imported source bytes before/after reads; its SHA is explicitly in the r7 manifest. Sealed SQLite reads use immutable read-only mode, while existing live/in-memory connections are used directly. The preserved r6 source and historical candidate manifests remain historical provenance, not the active r7 manifest.

## Conceptual evidence (no numerical calibration)

- Martela (2019), DOI `10.1186/s41469-019-0062-9`, pp.5–6,12,14–16: direction, interdependent-task coordination and monitoring/accountability are distinct organizational functions. Conceptual ideal-type comparison, not estimates of these network parameters.
- Simon (1991), DOI `10.1257/jep.5.2.25`, pp.32–34: authority, policy premises and information channels can differ; task interdependence complicates individual contribution measurement and local incentives can displace costs to other departments.
- Dabbish et al. (2014), DOI `10.48550/arXiv.1407.0377`, pp.2–3: qualitative GitHub dependency monitoring, notification and mutual adjustment. It does not identify an enterprise task chain or edge coefficients.
- Vogelpohl et al. (2020), DOI `10.1007/978-3-030-49183-3_33`, pp.434–435: a research design contrasts common versus conflicting local organizational objectives. The paper states at p.437 that analysis was pending; its planned sample is not an observed treatment effect.

Existing checked local full texts and bibliography locations are recorded in `R7_FACTOR_COVERAGE.json`; coefficients, joint distributions and graph generators remain conditional synthetic hypotheses. No new empirical target was opened, no result-driven tuning or seed selection occurred, and no superiority of DAMS is assumed.

## Existing execution entry

From this candidate root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m dams_sim run --config candidate-evidence/calendar-networks-r7.json --journal-chunk-bytes 1048576 --output /Volumes/DAMS-Workspace/private-validation/root-enterprise-r7-networks-tasks-20261012-r1/reproduction
```

The existing CLI now exposes its already implemented `run_world(journal_chunk_bytes=...)` argument for run/resume. Default remains None. An optional `--checkpoint-day 23` intentionally stops at that checkpoint; complete the remainder through the same `resume --checkpoint <run>/checkpoint.json --journal-chunk-bytes 1048576` entry. A checkpointed execution is not a completed sixty-day case. Actual macOS controls are retained in the private engineering evidence; Linux, long-horizon N10k/N50k, parameter recovery, empirical calibration and held-out generalization remain unadmitted.
