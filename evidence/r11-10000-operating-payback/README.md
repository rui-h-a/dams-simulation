# Operating totals and ten-year break-even for the 10,000-member study

This retrospective analysis uses ten simulated multinational worlds. Each world began with 10,000 members and has a matched retained hierarchy arm and witnessed DAMS arm. The five years after adoption are simulated. Years 6–10 are conditional calculations. The two years of shared history before adoption are outside both sums.

## Recompute the results

Run `python3 -I -S -B verify.py` with Python 3.9 or later and compare its output with `results.json`. No network connection or simulation is required. To compare every exported row and the archive and arm fingerprints against the retained analysis, add `--source-dir` followed by the directory containing the calendar-corrected analysis files listed in `provenance.json`.

`annual-inputs.csv` contains 100 rows, one for each world, arm and year after adoption. Each row reports an annual net operating rate in synthetic resource units per member-year and exposure in member-years (365.25 active-member calendar days). Multiplying rate by exposure recovers the year's net operating total. Sum within each arm, subtract the hierarchy from DAMS within each world, then average the ten differences. Each arm's five projected years use the simple mean of its years 2–5 rates and its own year-5 exposure.

A common increase in the DAMS rate during years 6–10 is the negative of the mean projected ten-year total difference divided by mean DAMS exposure in years 6–10. It sets the **mean** undiscounted total difference to zero with the hierarchy path and both arms' exposure fixed. The mean of the ten world-specific ratios is a different quantity. Its interval and the total-difference intervals summarize between-world Monte Carlo variation under the stated rule. They do not incorporate projection-rule uncertainty. These synthetic operating sums do not estimate investment cash flows or identify a particular transition cost.

## Source binding

`provenance.json` pins the retained calendar-corrected analysis, the ten archive fingerprints, and the paired prehistory, receipt, summary and time-series fingerprints. The retained input preparation matched the arm files to their receipts and checked branch timing. Its independent arithmetic check compared daily and cumulative columns. This packet verifies the exported inputs and derived arithmetic. It records the original archive fingerprints without redistributing or revalidating the full event journals.
