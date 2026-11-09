# Verify the fixed-evidence authority experiment

The checker uses Python 3.10 or newer and its standard library. Check out
[the frozen simulation source](https://github.com/rui-h-a/dams-simulation/tree/ed82a5817158d735b4878998e55cb312b1576b40)
at commit `ed82a5817158d735b4878998e55cb312b1576b40`. Download
`input.json.gz` and `result.json.gz` from
[the fixed release](https://github.com/rui-h-a/dams-simulation/releases/tag/fixed-authority-replay-20261022)
and decompress them. From this directory, run the following command with a
fresh output path.

```sh
python3 -B -I verify.py --pinned /path/to/checkout --tape input.json --result result.json --output check.json
```

The checker binds the original source and data to the published hashes in
`identity.json` and the original experiment manifest. It reconstructs the six
query dates from a chronological event index. It checks the exact eligible
population and appointment ages, the decayed contribution credits, all five
authority policy vectors, their sums, their Gini coefficients and their total
variation distances from linear credit. Fractions preserve exact attribution
and tier ties. Credit and allocation comparisons use a 64-digit Decimal
reference and allow binary64 rounding.

The published result contains 119,700 eligible person–date–domain entries and
598,500 individual shares. The maximum absolute share discrepancy is about
1.21 × 10⁻¹⁷. All 120 Gini and total variation values agree within
1.60 × 10⁻¹⁶ in absolute value. `verification.json` gives the full audit counts
and error bounds. This deterministic allocation experiment supplies no new
behavioral worlds or empirical estimates.
