# Marginal changes in the baseline founding-startup simulations

This retrospective analysis uses the six initial membership sizes in the default R11 founding-startup design with organic growth. Each size contains five simulated worlds, each with both the DAMS witnessed-record arm and the retained performance-tier hierarchy arm. Each observation covers the same 366 calendar days. Calendar member-year exposure varies with the active roster, so it can differ across arms, worlds, and sizes.

Run with Python 3.9 or later. No external packages or simulation are required.

```sh
python3 verify.py
```

To check the pinned public aggregate file as well, pass `--public-summary` with the local path to `04_evaluation/r11/scale-study.json`. The verifier compares all six reconstructed rate gaps and their original intervals against the frozen aggregates. The supplied `results.json` is the verifier output.

## Pairing and outcomes

For each arm and world, net operating result is operating revenue less operating cost, in synthetic resource units. The per-member-year rate divides this result by the arm's active-member calendar days divided by 365.25. The whole-firm first-year total uses revenue less operating cost directly. Its gap is calculated before averaging across worlds, preserving actual exposure in each arm without substituting initial headcount.

At each size, the gap is the DAMS witnessed-record outcome minus the hierarchy outcome from the same world. For two adjacent sizes, the change is the larger-size gap minus the smaller-size gap, aligned by world ID. The same seed and world IDs recur across sizes without a size-specific random-world context, so equivalent keyed random calls can share draws. Computing the change within each matched world retains the empirical cross-size covariance. World alignment does not hold each roster or draw sequence fixed when the simulated organization grows. It also does not isolate a size effect from design rules that vary with initial membership.

The rate change is reported in resource units per member-year per 100 additional initial members. The total change is reported in first-year resource units per additional initial member. Both measures summarize adjacent design points and do not estimate the effect of adding a particular individual.

## Uncertainty and interpretation

Each interval uses a paired Student t calculation on the five world-specific changes (four degrees of freedom). The ten-comparison family consists of the five adjacent size steps for each of the two outcomes. The familywise 95% intervals use a two-sided Bonferroni adjustment over all ten comparisons and do not require independence across those comparisons. Coverage still depends on the t interval's assumptions for the five simulated draws. These intervals express Monte Carlo uncertainty within the synthetic design. They provide no validation for real firms or an optimum outside or within the grid.

## Provenance

The portable CSV retains the original numeric precision of the three input fields from each of the 60 synthetic arm summaries, along with source SHA-256 fingerprints. During extraction, each summary was verified against its arm manifest, and each matched pair was bound to the same prehistory receipt. The source snapshot, portable data and frozen public summary pins are recorded in `provenance.json`. The portable packet reproduces the numerical analysis using the exported endpoints. Source fingerprints are traceability records for the retained originals.
