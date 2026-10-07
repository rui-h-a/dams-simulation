# Reproduction and platform evidence

[README](../README.md) is the single command entry point. This guide explains its evidence and failure semantics. [ODD](ODD.md) defines the model; [INTERFACE](INTERFACE.md) defines parameters, units and raw fields; [INFRASTRUCTURE](INFRASTRUCTURE.md) owns measured capacity, operating tiers and cloud dry-run assumptions. These documents do not create competing model specifications.

## Complete scientific pipeline

`research_tools/reproduce_thesis.py` runs from the repository root. Its required `--output` identifies the raw study directory; `--analysis-dir` defaults to `OUTPUT/report`; `--workers` accepts 1–4 and defaults to 2. The workers apply to the extended scale stage. Other stages retain their actual serial/matched-world design. No MPI or within-world GPU partition is implemented.

| Stage | Evidence produced or checked |
|---|---|
| Tests and smoke | Executable invariants/regressions plus a complete small synthetic world; neither substitutes for the full study |
| Pilot | Protocol, fixed subsequent sample size and source/driver binding; the pilot is not part of the confirmation estimate |
| Confirmation | Prespecified paired independent-world comparisons over policies, evidence assumptions and update schedules |
| Mechanisms | Analytical/fixed-stream allocation diagnostics, kept distinct from endogenous dynamic outcomes |
| Stress | Attacked and own-policy unattacked worlds, declared opportunity-cost budgets and structural boundaries |
| Sensitivity | Finite-difference screening of six design dimensions; these are not Sobol indices or calibration |
| Contexts | Explicit synthetic organizational stress configurations; labels do not establish industry or demographic realism |
| Extended scale | Full individual-model organizational scaling and independent-world resource-bounded execution |
| Recovery | Synthetic behavioral/rule/parameter recovery within the declared generating grid; not empirical identification |
| Analysis/report | Verified raw cases, world-level calculations, vector figures, LaTeX fragments and self-contained HTML |

Exact cases, seeds, parameters, policy/backend sets and stop bounds are defined in the current drivers and effective configuration records, not inferred from a plot label. The pilot protocol records the fixed confirmation design; no significance-based sequential stopping is introduced. Machine-readable output retains unfinished stocks, failed attempts and conditional completed-record measures.

The driver checks at least 8,000,000,000 bytes free disk and 4,000,000,000 bytes installed RAM when the latter is measurable. These preflight thresholds do not certify the smallest machine or an overnight finish. Leave OS/RAM/disk headroom and profile the actual workload using the measured tiers. First `uv` use can require dependency/build-tool downloads; installed/cached runs make no external model/API calls. The analysis extra is locked, and no command starts paid infrastructure.

## Locate results and verify completion

Inside the chosen study directory:

| Artifact | Meaning |
|---|---|
| `pipeline_manifest.json` | Current full-core hash, Git/dirty state, complete-driver/study/inventory/lock hashes, host facts, workers, stage outcomes, completion/error and report hashes |
| `STAGE.log` | Actual subprocess output; logs append on retry rather than concealing the earlier attempt |
| Stage manifests and summaries | Fixed case design, source/driver provenance, hashes, world-level summaries and retained noncomplete attempts |
| Per-case `manifest.json` | Effective configuration, source/config/output hashes, host/runtime details and actual status |
| `final_state.json`, `timeseries.csv`, `summary.json` | Complete raw state and recorded trajectory for the 2,388 stored pilot/organization cases; hashes are checked before cached analysis. Recovery retains its four-pattern observations and derived fits for 328 model executions, without serialized final states |
| `report/generated/analysis/` | Recomputable calculations, primary claims and `generation_manifest.json` |
| `report/generated/` | Automatically generated LaTeX result fragments and macros |
| `report/figures/results/` | Editable SVG and vector PDF ensemble figures |
| `report/report.html` | Self-contained scientific outcome report; it embeds the generated SVGs |

The pipeline finishes only with `pipeline_manifest.json` at `status: complete` and exit code zero. A running or failed status, orphaned directory, incomplete horizon or missing output hash does not become a complete world. A recorded failed attempt is retained even if a later compatible attempt succeeds. Unsigned hashes detect accidental differences against the retained manifest; they do not authenticate an adversary who can replace both data and manifest.

Repeating the same command verifies and reuses compatible complete stages/cases. A source or scientific-driver change requires a new output directory. The core CLI supports an explicit partial checkpoint and a separately written restart:

```sh
python3 -m dams_sim run --config configs/reference.json --checkpoint-day 30
python3 -m dams_sim resume --checkpoint runs/ACTUAL-RUN/checkpoint.json
```

Replace the path with the actual checkpoint produced. The original directory remains intact; the restarted manifest identifies its parent run, checkpoint and source/config/output chain. Exact equality is a same-platform engineering check, not external model validation. Abrupt termination can leave a core manifest `running`, and an OOM/RSS stop may not have a newly written checkpoint.

## Hardware timing is a separate experiment

Scientific outcomes and hardware timing answer different questions. The complete scientific driver does not claim an idle-host benchmark or reproduce another computer's time. Use the bounded timing entry point when the computer is not also running a DAMS scientific batch:

```sh
python3 research_tools/benchmark.py plan
python3 research_tools/benchmark.py run --output evidence/benchmarks --repeats 3
```

It preserves full normal modules, clean child interpreters, process-cold and fresh-model warm repeats, 1/2/4-worker strong/weak scaling, source consistency, disk bytes and real watchdog/refusal records. Its large-scale paths are bounded partial attempts or preflight refusals, not silently reduced complete simulations. Read the exact cold/cache, RSS sampling, complete-wrapper/core/launch timing and extrapolation definitions in [INFRASTRUCTURE](INFRASTRUCTURE.md).

Publication postprocessing is separate from the actual executing driver. It reads retained raw measurement/watchdog JSONs, checks derived CSV values and hash consistency, and records its own source hash. `--state final` requires the currently installed full core; `--state analysis-ready` retains preliminary evidence and writes pending result markers. Local NNLS/leave-one-workload-out diagnostics expose fit sensitivity; neither certifies million-person capacity. Hardware runs must preserve the original measurement-tool snapshot rather than rewrite its provenance after a postprocessor change.

## Platform and environment support matrix

| Environment or path | Actual evidence status | Practical boundary |
|---|---|---|
| macOS 26.6, Apple M2 Pro arm64, CPython 3.14.6 | Core runs, bounded scale measurements, independent-world batching, output integrity and same-platform state equality actually exercised | 12 physical/logical cores and 16 GiB RAM are observed hardware, not universal minima; user applications continued during timing |
| Same macOS/arm64 host, uv-managed CPython 3.14.2 | Clean public clone executed the complete scientific study; all 2,388 persisted world states, summaries and time series matched the reported 3.14.6 outputs byte for byte, with recovery patterns/fits checked separately | This is one measured cross-version reproduction on the same host; it does not certify Linux, Windows or arbitrary Python versions |
| Optional Matplotlib 3.11.2 analysis | Locked environment actually produced grayscale vector PDF/SVG performance figures and generated LaTeX | Requires the `analysis` extra; scientific calculations, data provenance and figure generation remain separately versioned |
| Other Python versions ≥3.11 | Declared portability target | Only the named 3.14.6/3.14.2 paths were exercised; rerun tests, source/hash checks and bounded timing |
| Linux/WSL CPU | Portability target; `/proc` monitoring path exists | No current measured run certifies this environment or identical timing/bit patterns |
| Native Windows | Untested portability target | POSIX child/watchdog handling is not certified; unavailable resource measurements must remain null |
| Docker/container, linux/amd64 and linux/arm64 targets | [Explicit allowlist recipe and fixed official Python/uv image-index digests](CONTAINER.md); local build/doctor/smoke attempts failed at the unavailable daemon socket | Upstream identities are verified metadata; no application image, container run or cross-platform numerical equality is asserted |
| MPI/GPU | No implemented simulation path | Parallelism is across independent CPU worlds, not a distributed single-world benchmark |
| Workstation/HPC allocation | Conditional resource plan | Profile the actual N, horizon, guild/site/link structure and retained outputs before raising limits |
| GCP cloud burst | Price/planning dry run only | No VM was started; VM-hour reservations are assumptions, not measured E2 runtime |
| Solidity/local EVM | Separate research prototype and retained validation artifacts | Gas, storage and contract properties belong to that EVM experiment, not the Python model or a production network |

## Contract regressions and build

The contract directory has its own pinned npm lock and Solidity compiler settings:

```sh
cd contracts
npm ci
npm test
```

First dependency installation can require network access. Read the local validation artifacts and test source when interpreting gas or trust-protocol results. The complete Python scientific driver does not silently represent those tests as an executed ledger backend. Its stylized evidence labels remain explicit parameter assumptions.

The core package can be built with `uv build`; the source checkout is required for the research drivers and retained evidence. Restricted full-text literature, private thesis/product histories, secrets and customer data are not package inputs. Use the exact commit/config/driver/lock and output manifests when citing or comparing a reproduction; public release claims require actual remote/release evidence.

## Evidence distribution

The release layout separates the compact Git tree from the complete raw bundle. Code, configuration/registries, summary CSVs, vector diagnostics, manifests and an evidence index remain directly inspectable; the compressed release asset retains all original full states, trajectories and failed/orphaned attempts. Archive byte count, SHA-256 and download URL must match the actual uploaded asset before it is called available. Do not substitute a summary-only tree for the complete raw evidence or rewrite old-source manifests when preparing a new release. Downloading the bundle is optional for a fresh execution, but required when auditing raw cases from the reported run.

The reported complete raw bundle is published at [v0.1.0](https://github.com/rui-h-a/dams-simulation/releases/tag/v0.1.0):

```sh
curl -L --fail -o dams-research-evidence-v0.1.0.tar.gz https://github.com/rui-h-a/dams-simulation/releases/download/v0.1.0/dams-research-evidence-v0.1.0.tar.gz
python3 research_tools/unpack_evidence.py --archive dams-research-evidence-v0.1.0.tar.gz --sha256 77c02991aaa39964b34831c99f375c1b77497640d3de8e3e74601af557a5a06a --destination raw-evidence
```

The archive is 676,674,034 compressed bytes. The companion [index](../evidence/raw-release-index.json) lists every retained file's original byte count and hash. The extractor verifies the whole archive before writing, accepts only regular relative evidence files, and refuses to overwrite differing data. It needs enough free space for the uncompressed files. The release preserves the measured model and tool snapshots. Subsequent typography changes use unchanged raw scientific data and record their own postprocessing hashes; they do not rewrite the original execution metadata.

## Independent exact comparison

The [fresh-clone evidence](../evidence/fresh-clone/README.md) records a complete clean execution without copying reference world states into its inputs. Its portable read-only comparer uses the fixed protocol inventory, verifies every recorded output digest and exact persisted scientific bytes, and retains runtime/provenance differences separately. Recovery patterns and finite-grid fits are compared without claiming that unpersisted recovery states were checked.

```sh
python3 research_tools/compare_reproduction.py --reference raw-evidence/runs/revision-v2 --fresh runs/thesis-reproduction --output runs/comparison --expected-origin-commit EXACT_EXECUTED_COMMIT --expected-checkout-commit EXACT_CURRENT_CLEAN_COMMIT
```

Supply the commits actually recorded by the fresh execution and current checkout. The defaults identify the original validated `a9f6d7` reproduction. This comparator is deliberately specific to the retained thesis protocol; it does not substitute a tolerance, treat missing matching stages as equality or certify unrelated design changes. It expects a complete fresh pipeline manifest and a clean public checkout. If only postprocessing has been refreshed, preserve the original full-execution manifest before overwriting it and retain the new wrapper evidence separately.
