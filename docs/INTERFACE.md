# Input/output interface

Source version is 0.1.0; exact `source_sha256` in each run manifest is authoritative. `Config` in `dams_sim/config.py` is the strict schema and validates unknown keys, types, nonfinite values, ranges, lengths, interval order and pooled time-reservation feasibility. Inputs are JSON objects. `Config.to_dict()` in every run preserves all actually effective defaults. The four vectors `hierarchy_weights`, `backend_review_multipliers`, `backend_settlement_days`, `administrative_censorship_exposure` accept JSON arrays. Backend vectors always use central/witness/consensus order. Config n/guilds/sites/team_size are separate, but guild→site modulo mapping can leave some configured sites empty; domain population is never validator population.

Raw summary fields (nonnegative counters may be absent when no corresponding event occurred; use zero only for known counters, never for undefined conditional means):

| Field | Unit / definition |
|---|---|
| produced_work_units | Sum of generated model work, not money or observed employee productivity |
| effort_hours | Model hour-equivalents of production effort |
| cooperation_units | Sum of bounded help choices; reserved help time =0.05×units |
| decision_regret_units | Sum of ex-post binary-action |θ| regret, including unresolved decisions |
| decision_errors / decisions_completed / decisions_unresolved | Guild proposal counts |
| review_hours / appeal_hours | Actual pooled human hour-equivalents (0.1/0.15 per record) |
| verification_resource_units | Extra per-record backend resource cost beyond common human appraisal |
| confirmed_records | Unique committed event IDs, including successfully appealed records |
| fraudulent_records_submitted / detected / accepted | Evaluator-truth counts: detected means initial-review rejection of a fraudulent claim, which can subsequently be appealed and committed; these counts are not mutually exclusive. Pending cases remain distinct |
| reviews_rejected / honest_records_rejected / appeals_denied / corrections | Procedure outcome counts |
| duplicate_records_rejected / censored_records | Explicit modeled threat counts |
| attack_budget_hours | Common declared opportunity-cost bound per active day |
| attack_hours_consumed | Exactly reserved/consumed model time across the fixed attacker group |
| max_individual_time_booked_hours | Largest care + production + procedural reservations + help + attack daily budget; ≤1 |
| quorum_unavailable_days | Conditional live-quorum failure days in stylized consensus scenario |
| confirmation_mean_days / confirmation_p95_days | Conditional completed-record delay from creation to commitment; null if none |
| appeal_mean_days | Conditional successful appeal creation-to-correction delay; null if none |
| unfinished_records | Unreviewed + accepted-but-unsettled claims at horizon; excludes appeals |
| unfinished_appeals | Outstanding appeal count at horizon |
| backlog_peak_records | Peak unreviewed + accepted-but-unsettled count |
| last_authority_contribution_tv | Final snapshot guild-mean formal/linear-credit TV, dimensionless [0,1] |
| last_signed_gini_gap | Final snapshot guild-mean G(formal)-G(confirmed), dimensionless signed |

`timeseries.csv` additionally has day, daily output, backlog, appeal backlog, daily regret, participating fraction, formal/normalized-participating weight TV, formal/credit TV, signed Gini gap, authority Gini, mean experienced trust and mean fatigue. None of these synthetic state scores is a questionnaire measure or field validation. Snapshot day is zero based. Formal shares last updated at the configured schedule can be stale relative to committed credit; the TV intentionally retains this schedule effect.

`summary.json` contains model/world/regime/backend/attack/n/day metadata. `final_state.json` stores complete state, queues and unique events; it is raw engineering evidence rather than an aggregate plot table. `manifest.json` contains source/config/output SHA-256, Git commit and dirty flag, Python/platform, seed/world, effective parameters, status/exit/resource measurements and run ID. `checkpoint.json` has source hash and raw state. Atomic writes prevent partially replaced result files. `run --checkpoint-day K` stops at K and reports checkpointed, while failures report failed; neither claims a complete horizon. `resume` creates a new unique directory and never modifies the original.

`reproduce` writes `world_summary.csv` and one complete child run per policy/backend/world. Primary scientific inference must pair the same world ID, report independent-world sample size and retain failures; no independence is assigned to people/events. This CLI does not silently run sensitivity, empirical calibration or parameter recovery. The integration lead owns confirmed experimental protocol, analysis and final scientific figures. `report.svg` and `report.md` are automatic single-world diagnostics. Publication-level ensemble figures need raw independent-world output and specified intervals.
