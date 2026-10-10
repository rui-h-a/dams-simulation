
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
are rejected before execution. These repairs address three reproduced
internal-correctness failures; they do not establish complete-model correctness.
The model remains synthetic and externally uncalibrated.
