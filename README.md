# Independent enterprise operations r4 candidate

This isolated candidate repairs three reproduced operating semantics gaps in
r3. It is not adopted into the frozen small-enterprise or 144-case studies.
No completed formal case is regenerated, and M&A work remains a separate
candidate. The current runtime packet is `source-manifest.operations-r4.json`;
`publication-manifest.json` identifies the finite portable source and fixtures.
Inherited causal packets and coverage documents record history, not this
version's execution identity or completed empirical validation.

Export restrictions now govern origin-to-destination fulfillment. A declared
`destination_market_shares` tuple creates domestic and cross-context orders;
an empty tuple sends all demand to context index 0. Domestic calendar work
remains available when exports are prohibited. Ineligible orders remain in the
backlog until eligible production or their declared expiry. Exporters can serve
other destinations after domestic-only origins reserve their own-market demand.
Logistics cost and delay are still origin-context assumptions: bilateral route
costs, destination tariffs, congestion and detailed customs are not modeled.

Coordination requests retain the uncapped layer, span and context coefficients.
Actual service is limited by available individual time and the explicit
`coordination_service_budget_workday_fraction` (default 0.4). Unserved work
persists by installed site. Subsequent service consumes real time, and the
served-to-total ratio limits productive readiness. Requested, served and
pending work are observable and checkpointed. This service/queue mechanism and
its coefficients are synthetic hypotheses, not empirical estimates of firms.

Production uses each site's actual potential and material/logistics unit cost.
Uniform scaling enforces demand, installed capacity and available cash; a
zero-potential expensive site consumes no production budget. Exact binary64
rational constraints, downward representable allocations and person-level
partitioning make allocation and FIFO settlement agree. Small unfulfilled
contract residuals are retained rather than discarded by an epsilon. Actual
material and logistics settlement remains a conservation gate. This is a
feasible proportional allocation rule, not a claimed profit-optimal allocator.

59 finite engineering controls passed, including independent Decimal budget
checks, 200 fixed scalar input cases, multi-site and zero-output cases,
non-workdays, fractional funding, exact uninterrupted/resumed checkpoints,
source/state/raw-journal gates and three independent semantic edge cases.
Run them with Python 3.11+ from this directory:

    PYTHONPATH=.:tests python3 -B -m unittest -v test_enterprise_growth test_enterprise_operations test_enterprise_operations_r4

Tests create only owned temporary engineering fixtures. The r3 comparison core
is a portable read-only fixture under `tests/fixtures/operations-r3-reference`.
Exact cross-version agreement is established only for the tested unbound case
with one person per site and for the disabled operating path. An earlier growing
multi-site comparison differed in an order's floating representation by
3.552713678800501e-15; its failed expectation and source versions are preserved
in the private implementation receipt. No general byte-equivalence is claimed.
The raw-accounting reader shares transition functions, so its consistency gate
is not an independent economic solver; the scalar Decimal constraints provide
a separate budget calculation within the tested domain.

The calendar profile JSON files are updated to operating schema 2 and retain
all previous populations, calendar settings and seeds. They have not been run
as new five-year studies in this candidate. Passing tiny controls establishes
internal software behavior only. Empirical calibration, held-out cross-firm
validation, integrated M&A, full candidate scientific admission and formal
statistical inference remain uncompleted for this version.

## Preserved r3 description (historical)


## Independent causal operating candidate v3

This candidate adds a daily operating path to the existing endogenous-growth
model. Competitor prices and demand create priced orders. Existing detailed
agent effort fulfils those orders subject to installed capacity, material cash,
local availability and coordination time. Paid shipments settle later; realised
cash and past operating margins drive delayed investment, recruiting and layoffs.
Explicit credit carries debt and interest. Recruitment and training have delays.
All operating state is checkpointed and reconstructed by the raw accounting gate.

The actual paths, formulas, observables and remaining gaps are recorded in
`candidate-evidence/CAUSAL_COVERAGE.json`. All numerical operating coefficients
remain synthetic scenario assumptions. Country contexts encode declared costs,
work windows and transit times. They never assign ability or honesty to national
or demographic groups. This is not a reconstruction of Amazon or another firm.

`research_tools/enterprise_validation.py` provides a finite multi-objective
calibration protocol with frozen roles, metrics, parameter grid and all world
seeds. It rejects fitting with validation data, unidentifiable parameter grids
and changes after freezing evaluation predictions. An exposed report remains a
retrospective diagnostic. The only available audited report parser covers one
headcount aggregate. Complete multi-firm observations and independently held-out
validation have not been supplied or completed. Passing synthetic recovery
controls establishes software behavior, not empirical validity.

The source derives from the independently tested schema2/growth candidate and
is a new model version. It does not alter or supplement samples from the frozen
formal or small-enterprise experiments. Source and configuration receipts for
any candidate profiling are kept separately.

Version 3 corrects three reproduced correctness failures in version 2. Calendar
cash receipts and paid operating costs carry to the next eligible workday
signal update, including weekend interest. Checkpoints bind cumulative
operating stocks to aggregate metrics and the last committed day-end journal.
Configurations whose declared price/order envelope exceeds finite arithmetic
are rejected before execution. These repairs establish internal correctness
only; the model remains synthetic and externally uncalibrated.
