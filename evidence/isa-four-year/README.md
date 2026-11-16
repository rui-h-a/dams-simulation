# Four-year automation and sandbox comparison

This bundle recomputes the reported statistics from aggregate outcomes for seven governance arms in each of four simulated organizations. The outcomes come from day-2191 checkpoints in the automation and sandbox experiment. All arms branch from the same state within a world on day 731. The analysis covers the following 1,460 days, ending on simulation day 2190. Inference uses the world as the independent unit.

Run `python3 verify.py` from this directory with Python 3.11 or later. On Windows, use `py -3 verify.py`. The verification uses the standard library and temporary storage. It compares the full results, including paired differences, confidence intervals, raw p-values, and Holm adjustments for the four primary contrasts. Floating-point fields allow relative differences of up to `5e-12` across Python builds. Input checksums and discrete values must match exactly.

`arm-outcomes.json` contains only the aggregate analysis inputs. `stats.py` implements the paired analysis, `isa_spec.py` defines the arms and outcomes, and `expected-statistics.json` contains the reference output. Use this command to export a fresh result.

```sh
python3 -X utf8 -B stats.py arm-outcomes.json --horizon-years 4 --out recomputed-statistics.json
```

The [frozen enterprise engine](https://github.com/rui-h-a/dams-simulation/tree/ef6cb2959419134d86dd05327b0689ea0d8e022e/research-candidates/enterprise-current/source) has commit `ef6cb29` and `dams_sim` source hash `03d17dc53a2538ae14da040883b07ff270ac42c22de5d0a3acad4fc10edd30fb`. Its [arm specification](https://github.com/rui-h-a/dams-simulation/blob/ef6cb2959419134d86dd05327b0689ea0d8e022e/research-candidates/enterprise-current/source/docs/automation-arms.md) defines the compared mechanisms. The comparison describes outcomes under the declared synthetic conditions. This bundle starts from checkpoint-derived arm aggregates. Event replay, checkpoint restoration, and five-year acceptance require their respective raw records and execution receipts.
