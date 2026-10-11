# Optional conditional entry dependence, candidate joint schema 1

This independently versioned engineering candidate is `longitudinal-enterprise-integrated-6-joint-1` when `Config.joint_population` is supplied. The default is `None`: the previous initialization, recruitment, configuration bytes, event roster, trajectories and version tags are retained. This is not a calibrated enterprise population, a formal long-term study, an out-of-sample validation, or admission to N10,000 research.

## Existing variables and actual paths

No new demographic or decorative agent attribute is added. `skill` is the existing research-only latent production multiplier, dimensionless, and is not disclosed to voters. `tenure` is the existing observed appointment-tenure proxy in calendar-year units, not lifetime work experience. `care_hours` retains its historical name but its unit is a fraction of a person's workday, not SI hours. It reduces available time and enters the existing effort choice. All five generated values are used by current behavior or accounting; autonomy and reciprocity keep their separate legacy keyed draws.

`LongitudinalEngine._enter` handles both initial staff and recruitment. `LongitudinalEngine._step` still uses skill in production, care time in availability/effort, and tenure in declared tenure-based authority. The joint generator does not introduce a demographic-to-ability or integrity mapping. M&A entry deliberately bypasses this generator and preserves the target's inherited profile and history.

## Joint distribution and assumptions

The existing synthetic marginals remain:

| Quantity | Initial staff | Recruits |
| --- | --- | --- |
| Skill multiplier | `exp(0.4 Z_skill)` | Same |
| Appointment tenure | `U(0,1)` calendar years | Exactly zero on entry |
| Care time | `0.3 U(0,1)` person-workdays | Same |
| Autonomy, reciprocity | Separate keyed `U(0,1)` draws | Same |

With Gaussian latent correlation coefficients `st`, `sc`, `tc`, the matrix in skill/tenure/care order is `[[1,st,sc],[st,1,tc],[sc,tc,1]]`. The coefficients are dimensionless **latent Gaussian correlations**, not correlations between transformed observed fields. The three coefficients default to zero; no existing registry or literature evidence was found that identifies nonzero coefficients for a target enterprise. The `.55`, `-.35`, `.1` vector in tests is an explicit engineering stress assumption, not a recommended or fitted research parameter.

For independent standard-normal inputs `z1`, `z2`, `z3` keyed to the same world and person:

- `skill = exp(0.4 z1)`.
- `y_tenure = st z1 + sqrt(1-st²) z2`.
- `a = (tc - st sc) / sqrt(1-st²)`.
- `y_care = sc z1 + a z2 + sqrt(1-sc²-a²) z3`.
- Initial tenure is `Phi(y_tenure)` and care time is `0.3 Phi(y_care)`.

Here `z2 = Phi^-1(U_tenure)` and `z3 = Phi^-1(U_care)`, using existing keyed draws. Recruits condition on entry status with tenure fixed to zero and use the skill/care marginal `y_care = sc z1 + sqrt(1-sc²) z3`. Initial-only tenure correlations do not create fictitious prior appointment tenure for a recruit. This is a conditional cohort assumption, not a recruitment-selection model estimated from employment data.

A positive-definite matrix is required, with determinant and first residual variance above `1e-8`. Invalid or nearly singular matrices are rejected, not repaired. For exactly zero correlations, the original numerical draws are returned bit for bit. For nonzero inverse-CDF inputs only, an exactly rounded 0 or 1 is mapped to the adjacent representable open-interval float; the legacy random mapper is not changed. Normal CDF tail saturation and floating-point limitations remain numerical limits, not evidence of empirical bounded latent traits.

The method is supported as a sampling construction by Henderson, Chiera and Cooke, *Generating Dependent Quasi-Random Numbers*, Winter Simulation Conference 2000, Section 2 ([original proceedings PDF](https://informs-sim.org/wsc00papers/072.PDF)). That section distinguishes normal-space covariance from transformed-space covariance and discusses feasibility limitations. This candidate does not adopt their quasi-Monte Carlo extension or claim its performance properties. The paper supports the construction, not the substantive correlation values or population marginals. Python's [`NormalDist` documentation](https://docs.python.org/3.13/library/statistics.html#statistics.NormalDist) supplies the CDF/inverse-CDF API definition. Read scope: the cited paper's Section 2 and the relevant official API paragraphs, not an assertion of full-paper review.

## Persistence, information and verification

Configuration serialization contains the optional typed schema and coefficient values. Every opt-in original `entry` event contains the algorithm/schema, initial/recruit flag, configuration SHA-256 and all five entry values. The existing complete final-state and checkpoint formats retain the configuration, agent states, person identities and original SQLite journal. No new checkpoint subsystem is created.

Recovery verifies immutable skill/care/autonomy/reciprocity against deterministic world/person generation. Appointment tenure is checked against entry tenure plus the retained Gregorian calendar interval, including weekends, closure periods and year boundaries. The check takes `O(T+N)`, not `O(N*T)`. Verified acquired identities remain exempt because their original target profiles are authoritative; arbitrary source-company labels cannot exempt an entrant. The raw validator independently reconstructs the scalar Gaussian sampling expression, configuration binding and immutable values without calling `draw_entry_traits` or a Model transition. It still shares the declared RNG and parameter schema, and this does not make all economic transitions independently validated.

Engineering controls cover sample latent correlations/marginals for 12,000 initial and 12,000 recruit draws, keyed order/world determinism, zero-path bit equality, disabled full-trajectory equality to exact preserved pre-joint r6, actual changed work-time/effort/production, recruitment, complete raw/daily/summary validation, exact checkpoint recovery, coherent state/witness forgery rejection, calendar boundaries and M&A profile/history conservation.

## Remaining scientific scope

These tests establish an implemented optional dependence mechanism and internal consistency. They do not establish real population covariance, causal effects in enterprises, identifiability, parameter recovery from held-out data, or cross-company validity. Correlations require preregistered sensitivity/ablation and appropriate independent calibration before scientific use. Separate reporting/collaboration/verification networks, task dependencies, departmental goal conflicts, and two fully evolving pre-acquisition organizations remain missing; inherited bounded M&A targets remain history/evidence capsules. The candidate is not N10,000-ready.
