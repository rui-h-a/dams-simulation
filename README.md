# DAMS research reference implementation

This repository makes the DAMS research model, synthetic experiment design, analysis and resource measurements inspectable. It studies contribution-contingent **formal** authority, bounded observation, guild coordination, procedural capacity and conditional evidence-substrate costs. Parameters and populations are design or stress assumptions. Synthetic work, concentration and completed-record delay do not establish field effectiveness, fairness, organizational efficiency or real consensus performance.

## Reproduce the complete scientific study

From this source repository, with Python 3.11 or newer and `uv` available:

```sh
uv run --locked --extra analysis python research_tools/reproduce_thesis.py --output runs/thesis-reproduction --workers 2
```

The command runs tests and a bounded smoke world, establishes or verifies the pilot protocol, executes the fixed confirmation, mechanism, threat, sensitivity, context, scale and recovery designs, verifies retained output hashes, and generates the analysis and a self-contained HTML report. Existing compatible complete cases are verified and reused. Source or driver mismatches are refused; use a new output directory for a new version. `--workers` controls independent-world parallelism in the scale stage, not partitioning of a single world.

Raw cases, stage logs and `pipeline_manifest.json` are saved under `runs/thesis-reproduction/`. The default analysis directory is `runs/thesis-reproduction/report/`, containing `report.html`, editable SVG/vector PDF figures, LaTeX fragments and machine-readable calculations. `--analysis-dir PATH` selects another report directory. A pipeline is complete only when its manifest records `status: complete`; a command being available or an individual stage passing does not establish full execution.

First use can fetch the pinned build/analysis dependencies. The runtime model requires no external service, credentials, dataset download, paid compute or GPU. The full study has an 8 GB free-disk and 4 GB installed-RAM preflight; those are rejection thresholds, not certified minimum hardware. Consult [resource measurements and operating tiers](docs/INFRASTRUCTURE.md) before choosing concurrency. The complete study is deliberately larger than the quick check below. Hardware timing and local-EVM validation are separate experiments, described in the [reproduction guide and support matrix](docs/REPRODUCIBILITY.md).

A [clean public-clone reproduction](evidence/fresh-clone/README.md) actually completed this command at commit `a9f6d711ca23f2bc9762b7a6d2bba42db95be275` in 970.782 seconds on the recorded macOS/arm64 host, using locked CPython 3.14.2. All 2,388 persisted world summaries, final states and time series matched the reported CPython 3.14.6 outputs byte for byte. The 328 recovery executions save observable patterns and fits rather than full states; those outputs also matched exactly. Runtime and provenance differences are retained separately. The final paper snapshot updates typography, report licensing and explicit failure records while preserving the scientific core and study design.

## Run a small world

The standard-library path needs no package installation:

```sh
python3 -m dams_sim smoke
```

It produces a complete small synthetic world, effective configuration, raw state, metrics, an editable SVG diagnostic and a Markdown report in a unique run directory. Other useful commands are:

```sh
python3 -m dams_sim doctor
python3 -m unittest discover -s tests -v
python3 -m dams_sim run --config configs/reference.json
python3 -m dams_sim reproduce --config configs/reference.json --worlds 3
```

The core `reproduce` command runs five policies and three stylized evidence scenarios on matched worlds. It is distinct from the complete study driver above and does not silently add sensitivity, recovery or empirical calibration. Single-world `report.svg` is a diagnostic, not an independent-world inference result.

## Read the model and evidence

- [ODD specification](docs/ODD.md): entities, events, behavioral choices, update rules and validity limits.
- [Input/output interface](docs/INTERFACE.md): strict configuration, units, counters and unresolved cases.
- [Parameter assumptions](docs/parameters.json) and [parameter evidence registry](docs/parameter_evidence_registry.json): design assumptions, source bounds and calibration status.
- [Factor disposition](docs/factor_disposition.json): implemented mechanisms, experimental controls, proxies, limitations and exclusions.
- [Source/claim registry](docs/source_claim_registry.json): what each retained source supports.
- [Reproduction/support guide](docs/REPRODUCIBILITY.md) and [computational infrastructure](docs/INFRASTRUCTURE.md): commands, provenance, tested boundaries, measured capacity and unexecuted plans.
- [Optional container packaging](docs/CONTAINER.md): explicit file allowlist, immutable official image identities and amd64/arm64 targets. Local daemon failures are retained; container execution remains unverified.
- [Security boundary](SECURITY.md): integrity is distinct from authentication, input truth, privacy and unique-person admission.

Inference pairs the same independent world across policies; people and events are not independent replications. The `central`, `witness` and `consensus` labels encode delay, per-record cost and censorship assumptions. They do not start a database, transparency-log protocol, BFT client or network of validators. Central records can be sufficient under a trustworthy operator; replication has no universal modeled benefit.

Policies are equal eligible shares, linear or sublinear decaying domain credit, performance-ranked tiers and tenure-ranked local tiers. Allocation shape and update schedule are separate controls. Sublinearity compresses concentration and creates identity-splitting incentives; it does not establish fairness or unique identity. Dynamic attacks use common declared budgets and their own-policy unattacked controls. Analytical identity splitting is separate; unique-person identity is assumed in the dynamic model.

## Preserve execution boundaries

The reference keeps explicit Python agents, claims, delays and full restart state. Memory grows with population and retained events; queues, sorting and serialization add cost. No scale comparison silently substitutes aggregation or disables normal modules. Set `max_events`, `max_wall_seconds`, `max_output_mb` and `max_rss_mb` for the intended workload. A sampled limit is not an OS guarantee against transient overshoot.

Completed cases retain source/config/output hashes and atomic writes. Caught failures and explicit partial horizons remain distinct from completed worlds. An abrupt process kill can leave a core manifest at `running`; the external watchdog, if used, is the terminal evidence. An RSS/OOM failure may lack a new checkpoint. Restart creates a new directory, verifies source/output integrity, and retains its parent chain; it does not overwrite the original. Exact state reproduction is tested on the measured platform; cross-platform bitwise equality is untested.

`contracts/` contains a separately compiled Solidity/local-EVM research prototype and regressions. Its gas/storage results are not agent-scale CPU results or an implemented ABM consensus backend. Product code, customer data, credentials, private Git history and restricted literature are outside this research release.

## Packaging and citation

```sh
uv sync --locked
uv run dams-sim smoke
uv build
```

`uv.lock` pins the environment; the model runtime dependency list is empty, the optional analysis extra pins Matplotlib, and the build backend is pinned. Run research tools from the source checkout. The wheel exposes the core `dams-sim` CLI rather than bundling the entire research evidence tree.

Use [CITATION.cff](CITATION.cff), cite the exact source commit and manifest, and retain the effective configuration and analysis version. The software is [MIT licensed](LICENSE) and has no assigned DOI. [Third-party notices](THIRD_PARTY_NOTICES.md) preserve the license for the unmodified Computer Modern font embedded in the report. The public repository and release URLs below are verified against the GitHub API.

The publication layout keeps inspectable code, compact metadata/CSV/SVG, manifests and an evidence index in Git; full raw state, trajectories and retained attempts belong in a checksummed compressed release asset. The original local evidence is preserved. The original raw archive is available in [v0.1.0](https://github.com/rui-h-a/dams-simulation/releases/tag/v0.1.0), with SHA-256 `77c02991aaa39964b34831c99f375c1b77497640d3de8e3e74601af557a5a06a` and 676,674,034 bytes. Its companion index and server-reported digest match the retained local archive. A fresh full reproduction can generate its own raw cases without a private thesis checkout.

Research repository: [rui-h-a/dams-simulation](https://github.com/rui-h-a/dams-simulation).

To audit the reported raw cases, download the `.tar.gz` asset and restore it without overwriting differing evidence:

```sh
python3 research_tools/unpack_evidence.py --archive dams-research-evidence-v0.1.0.tar.gz --sha256 77c02991aaa39964b34831c99f375c1b77497640d3de8e3e74601af557a5a06a
uv run --locked --extra analysis python research_tools/reproduce_thesis.py --output runs/revision-v2 --workers 2
```

The final paper snapshot uses a unified Computer Modern font family for text, mathematics and plots. This changes figure styling and source hashes; the model core, configurations, seeds, numerical analysis and retained raw outputs are unchanged. Cite the exact final snapshot commit as well as model version 0.1.0.

The [current complete-entry manifest and logs](evidence/full-entry-final/content-manifest.json) record actual tests, smoke and analysis together with hash-verified reuse of the complete scientific stages. The generated [self-contained report](data/report.html) retains its exact content hash. These current postprocessing records supplement the original raw release; they do not replace its execution provenance. The formal paper cites the `paper-2026-10-08` snapshot.
