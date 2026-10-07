# Fresh public checkout validation

This evidence records an actual clean public clone of
[`a9f6d711ca23f2bc9762b7a6d2bba42db95be275`](https://github.com/rui-h-a/dams-simulation/tree/a9f6d711ca23f2bc9762b7a6d2bba42db95be275),
followed by the complete documented command:

```sh
uv run --locked --extra analysis python research_tools/reproduce_thesis.py \
  --output runs/fresh-full --workers 2
```

The output directory did not exist before execution. No reference cases or
private thesis files were used as run inputs. All eleven pipeline stages passed,
including26 unit tests, smoke, every scientific stage and self-contained HTML
analysis. The command exited0 after970.78 seconds on the recorded macOS/arm64
host. This elapsed time includes ordinary concurrent application activity and is
not a controlled hardware benchmark.

The fresh command selected managed CPython3.14.2; the reference scientific run
used CPython3.14.6. The public checkout had no `.python-version`, and no interpreter
override was passed to the command. The lock, core and study hashes are recorded
in `manifest.json`. The checkout stayed clean and1983 tracked files retained
identical bytes across two independent stability passes.

| Stage | Persisted complete worlds | Additional retained outputs |
|---|---:|---|
| Pilot |20|20-row pilot summary and frozen protocol|
| Confirmation |960|960-row summary|
| Mechanisms |0|40 fixed-stream,4 replication and50 split rows|
| Stress |496|496-row summary|
| Sensitivity |312|168 screening,144 structural and48 elementary-effect rows|
| Scenarios |360|360-row summary|
| Extended |240|240-row summary|
| Recovery |0|328 model executions represented by four scientific patterns and fitted selections/distances|

The portable comparer was actually run against the independent fresh outputs
and the reference run. All2388 `summary.json`, `final_state.json` and
`timeseries.csv` files match bytes; all16 top-level scientific CSV files also
match bytes. Each side has11964 recorded output-file hashes independently
verified. Numeric tolerance is zero. CSV comparison permits only exact row and
column reordering, with malformed headers/rows rejected; this pair required no
such reordering.

Recovery retains216 candidate-training pattern rows,72 synthetic-observation
rows,24 held-out prediction rows,8 output-null rows representing16 model calls,
162 distance rows and6 fitted conditions. It does **not** retain per-execution
full final states, so this evidence makes no recovery-state identity claim.
The required filenames, row counts, four pattern fields and condition count are
checked independently of the two directories agreeing with each other.

Runtime and provenance differences are enumerated separately. In this pair,
`output_bytes` differs by-21 in each fresh world because the counted running
manifest contains a22-byte shorter Python provenance string and a1-byte longer
`git_dirty=false` value. The comparer independently reconstructs the exact
recorded size for both runs; state/summary/trace identity remains mandatory.
The initial failed comparison and its explanation are retained in the audit
history summarized by `manifest.json`.

`comparison.json` and `comparison.log` are the successful actual comparison.
`pipeline_manifest.json`, `command.log`, `tests.log` and
`stage-log-inventory.json` bind the original execution. `case-inventory.csv.gz`
contains the2388 config identities and both sets of scientific file hashes.
`provenance-runtime-differences.jsonl.gz` preserves the excluded differences;
only absolute local path prefixes are portable-redacted. The gzip members have
fixed timestamps. These compact files contain no complete states or private
research graph.

A new comparison can use a downloaded reference archive without a Git checkout,
while the fresh run must belong to a clean Git checkout:

```sh
python research_tools/compare_reproduction.py \
  --reference /path/to/reference-run \
  --fresh /path/to/fresh-checkout/runs/fresh-full \
  --output runs/reproduction-comparison \
  --expected-origin-commit a9f6d711ca23f2bc9762b7a6d2bba42db95be275
```

An explicit `--expected-checkout-commit` allows comparison after a later clean
checkout while retaining the original run's origin commit. The comparer checks
current core/study identity and retained extended/recovery driver bindings. It
does not certify that every change between the commits concerned fonts; any
such scope claim requires a separate Git diff. Original world and pipeline
origin metadata are never rewritten.

`validation.json` records actual rejection tests for duplicate headers, extra
or missing CSV columns, mutually missing stage outputs and a wrong expected
origin. The full successful comparison checks the eleven-stage roster and its
recorded log hashes, mandatory stage output inventories, completed-world counts,
config/source/study bindings, local output/protocol hashes and exact values.
The synthetic study's results are distinct from container, contract and
host-specific performance validation.
