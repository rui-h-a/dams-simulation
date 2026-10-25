# DAMS simulation: research reference implementation

This repository contains the simulation models, study protocols, analysis tools and tests behind the computational evaluation in the DAMS thesis (Decentralized Autonomous Management System). DAMS is a governance design that links verified contribution to domain-specific formal authority while keeping procedural accountability, guild autonomy and data rights. The code here evaluates parts of that design under declared assumptions. It is a research instrument, not a governance product, and none of its parameters is calibrated to an observed company.

## Current research snapshot — 22 October 2026

This branch contains the complete current enterprise research candidate, temporal fixed-contribution experiment, ACS measurement tools, tests and retained engineering fixtures. The observed-population entry provider is a source-pinned data component; it is not yet connected to the enterprise engine. No complete 10,000- or 50,000-person formal study is accepted by this snapshot.

See [current evidence and exact sources](docs/CURRENT_RESEARCH.md) before interpreting or executing a study. Historical results retain their original source commits. The existing one-command enterprise entry is available below; this update does not certify clean execution on every supported platform, complete cloud custody or empirical calibration.

## What is in the repository

| Path | Content |
|---|---|
| `dams_sim/` | The simulation engine. The enterprise model (version r11.3, with r11.4 adding an optional sickness-spell rule) simulates whole organizations day by day: people with correlated traits, guilds, teams, departments and sites, a market, finance, organic growth, contraction, closure and a reduced target-capsule acquisition mechanism. Complete transfer of two firms' histories and outstanding obligations remains unimplemented in that R11 mechanism. The shorter reference model isolates single mechanisms over 60 days. |
| `research_tools/r11_design.py` | Study design: organizational contexts, growth modes, adoption arms, assumption variants and the sources of their parameters. |
| `research_tools/r11_study.py`, `r11_retention.py` | Paired-world driver and preservation checks. New batches retain original journals, prehistory, checkpoints and interruption evidence. Scientific, design, execution and validation files are bound separately from the engine's historical source digest. |
| `research_tools/r11_archive.py` | Closed-writer archive transport using the existing lossless chunk codec, fixed-generation remote byte verification, offline mirror restoration and zero-step checkpoint recovery. Transport is not scientific or off-GCP custody acceptance. |
| `research_tools/r11_outcomes.py`, `r11_inference.py`, `r11_world_count.py`, `r11_robustness.py`, `r11_figure.py` | Outcomes per arm, paired Student-t intervals with Holm adjustment, the pre-registered world-count rule, variant-versus-baseline reports and a per-study effect figure. |
| `docs/r11-formal-protocol.md` | The study protocol and its dated amendments, each recorded before the worlds it covers were run. |
| `docs/r11-resource-measurements.md` | Measured run time, memory and disk use. |
| [Historical contract prototype](https://github.com/rui-h-a/dams-simulation/tree/838b78c62c9cb1ba330f047a541faad271e3d9e9/contracts) | The separately versioned Solidity/local-EVM allocation prototype. It is not an enterprise ABM consensus backend. |
| `tests/` | Unit and regression tests. |

The separate [complete-history two-firm candidate](docs/full-history-counterfactuals-r12.md)
adds common-state no-acquisition, autonomy and centralization branches with
complete native histories and funded governance conversion. Its small
engineering checks do not replace the frozen R11 results or establish formal
large-population completion. The existing R11 command does not select this
candidate automatically.

## Requirements

The bootstrap has pinned uv 0.9.26 artifacts for macOS and Linux, on arm64 and x86_64. It requires `bash` and `curl`, installs Python 3.14.2 and the dependencies in `uv.lock`, and checks the downloaded uv archive's SHA-256. The installed toolchain follows uv's managed environment/cache locations. Artifact availability is not a clean full-workload validation of every platform; native Windows and WSL2 have not been validated. Local execution needs no root. The separately admitted VM archive path additionally requires `lsof`, a closed owned process group and an attached least-privilege workload identity.

## One command from source to result

```sh
./run.sh --prepare-only          # install the pinned toolchain and run the environment check (doctor)
./run.sh --spec enterprise-r11 --scale 30 --context startup --pre-years 0 --post-years 1 \
         --worlds 0,1 --arms control-hierarchy,dams-witness --jobs 2 --output runs/example
```

The second command generates two independent worlds of a 30-member startup, runs one year under the kept hierarchy and under DAMS on a witnessed record, validates every arm, and writes:

- `runs/example/<world>/<arm>/arm.json`, `summary.json`, `timeseries.csv`, the full original final SQLite/journal and its state envelope;
- the original pre-adoption checkpoint/SQLite, periodic checkpoints and retained interrupted attempts;
- `plan.json`, which binds the population, worlds, seed, horizon, arms and complete necessary scientific/design/execution/validation source files;
- `runs/example/inference.json`, the assigned-world roster, missing/invalid evidence and conditional paired differences against `control-hierarchy`, calculated only from original evidence admitted by the inference validator;
- `runs/example/effects.pdf`, a figure of those differences.

The short example is an engineering demonstration, not a formal study or archive acceptance. The public flow does not run the standalone `r11_outcomes.py` aggregate or publish `paired-outcomes.json`; its analysis goes through original-evidence inference validation, and the figure refuses reports with admission errors. Existing historical aggregates are preserved without becoming accepted evidence. The same bound source, command and output can resume from verified checkpoints. A legacy plan without the full dependency binding refuses ordinary resume; a nonempty output with a missing plan cannot be relabelled as a new study. Completed historical compact arms are reported missing original journals and are not rerun to manufacture original evidence. New batches keep complete journals and recovery/interruption evidence; old space forecasts based on journal deletion do not admit this retention policy.

Options: `--context startup|unicorn|listed|traditional|century|multinational`, `--growth none|organic|organic+acquisition`, `--pre-years` (history before adoption), `--post-years`, `--worlds`, `--arms`, `--jobs`, `--variant`. Acquisition-only growth is still rejected by the current design; it is not an implemented comparison. The arms are `control-hierarchy`, `control-process`, `dams-central`, `dams-witness`, `dams-consensus`, `linear-witness`, `equal-witness`, `dams-staged-witness` and `dams-triggered-witness`. The variants are listed in `research_tools/r11_design.py` (`VARIANTS`); each changes one family of assumptions.

## Inspect, recover and preserve

Inspection and a zero-step recovery have explicit local entries. They do not add scientific samples:

```sh
./run.sh --spec enterprise-r11 --inspect-retention --output runs/example
./run.sh --recover-checkpoint runs/example/WORLD/prehistory/CHECKPOINT.json \
         --recovery-output runs/recovery-new
```

`WORLD` and `CHECKPOINT.json` are the exact paths in the retained study. The recovery target must not exist. Recovery uses the original matching engine source, verifies the complete snapshot and restores it without advancing a simulated day. Its receipt does not accept the whole study, remote archive or cleanup. Missing original SQLite/journal cannot be repaired by passing an inspection or by counting a replay as a new world.

The VM transport entry is explicit and has no paid public default:

```sh
./run.sh --spec enterprise-r11 --archive-config PRIVATE_ADMISSION.json \
         --archive-owned-pgid OWNED_EXITED_GROUP --archive-state PRIVATE_PERSISTENT_STATE \
         --output CLOSED_STUDY_DIRECTORY
```

Root must separately admit that private mode-0600 JSON, cumulative costs, VM lifecycle, storage and an absolute server-side stop. The JSON schema is `r11-root-archive-admission-v1`; required fields are `bucket`, `prefix`, `deadline_utc`, `max_requests`, `max_download_bytes`, `max_storage_bytes`, `max_raw_bytes`, `max_files`, `minimum_free_bytes`, the fixed absolute `state_directory` and the exact `archive_implementation_sha256` file map. `implementation_hashes()` in `r11_archive.py` supplies that map for review. The command does not create resources, grant IAM or supply spending authorization. Root must retain and pre-admit the original private `requests.json`, `downloads.json` and `upload-state.json` cumulative ledgers at that directory. Missing accounting or a different state path refuses execution; the tool does not initialize replacement zero counters. Another invocation is not a new allowance.

The owned runner must have exited and `lsof` must find no open research file. The transport then copies the exact bound source into the evidence tree and archives the entire closed roster, including hidden working SQLite, prehistory, checkpoints, interrupted files and sidecars. It uses the existing 4 MiB lossless codec with whole-roster/identity/hash rechecks. No active SQLite or unstable directory snapshot is accepted. Every data object and descriptor is read through EOF at its fixed GCS generation and checked against its byte count and SHA-256; the revision/precondition parameters follow the official [Objects: get API](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get). Reused upload-state entries still need current generation verification. An immutable remote receipt body is also verified, and a separate local index records that receipt's generation. Both belong in the independent preservation package.

`cloud/r11-startup.sh` requires `dams-commit`, `dams-args` and an explicit `dams-archive-config` admission. Its fixed persistent accounting directory is `/srv/r11-archive/transport`; a different admitted state path refuses preflight before the runner starts. The local CLI can use another Root-admitted persistent state directory. The cloud script refuses a changed source checkout, waits for its owned runner, and has no periodic live uploader or `done.json` deletion signal. Simulation status, archive transport, independent recovery and complete off-GCP custody are separate records. A generation receipt remains `archive_accepted: false` until Root's independent acceptance. Disk deletion, lifecycle cleanup and automatic restart are not authorized by that receipt. Historical launchers that delete a VM/disk upon `done.json` are incompatible with this path.

For an independently downloaded mirror, object files use `GENERATION/SHA256` names from the fixed-generation receipt. Offline restoration is:

```sh
.venv/bin/python -m research_tools.r11_archive restore-mirror TRANSPORT_RECEIPT.json \
  --objects MIRROR_DIRECTORY --destination NEW_RESTORED_DIRECTORY --max-raw-bytes RAW_BYTE_LIMIT
```

This restores and checks every original byte using the same codec; it does not prove the mirror's GCS custody or resume unvalidated interrupted SQLite. Independent checkpoint recovery, scientific input verification and complete off-GCP preservation remain required before any resource retirement. Legacy plan migration, live Linux/GCS end-to-end verification and a new full-retention capacity/cost forecast are pending for this candidate.

## Reproducing a thesis study

Each study in the protocol states its command, worlds and source commit. For example, the mid-life traditional-firm study (Amendment 2) is

```sh
./run.sh --spec enterprise-r11 --scale 300 --context traditional --growth organic --pre-years 2 --post-years 5 \
         --worlds $(seq -s, 1000 1064) --arms control-hierarchy,dams-witness --jobs 8 --output runs/traditional
```

Historical results name their original commits, including `93e5b87`, `e027d28` and `4347ccb`; use each result's actual source and protocol rather than silently applying a newer model. Those historical commits predate the complete-retention candidate. Deterministic replay is useful for reproduction but is not preservation of the deleted original journal. Random streams are keyed to world/person/event rather than worker identity. Exact platform/source equivalence and complete input preservation must be checked for the reported run; this README does not certify them for untested platforms.

The 10,000-member main study (protocol Table A and Amendment 10) is

```sh
./run.sh --spec enterprise-r11 --scale 10000 --context multinational --growth organic --pre-years 2 --post-years 5 \
         --worlds $(seq -s, 1000 1009) --arms control-hierarchy,control-process,dams-witness,linear-witness \
         --jobs 6 --output /path/on/a/large/volume/main-n10000
```

followed, after every arm has its receipt, by retiring intermediate checkpoints as Amendment 10 describes.
Under Amendment 12 each world of the main study, the 50,000-member comparison and the 100,000-member scale check runs as its own batch on its own machine, with the same command and one world, for example

```sh
./run.sh --spec enterprise-r11 --scale 50000 --context multinational --growth organic --pre-years 2 --post-years 5 \
         --worlds 1003 --arms control-hierarchy,control-process,dams-witness --jobs 3 --output /srv/runs/batch
./run.sh --spec enterprise-r11 --scale 100000 --context multinational --growth organic --pre-years 2 --post-years 5 \
         --worlds 1001 --arms control-hierarchy,dams-witness --jobs 2 --output /srv/runs/batch
```

and the admitted per-world reports are pooled with `research_tools/r11_pool.py`. A world's result does not depend on the machine it runs on. A quick check of the whole path (environment, two arms, validation, inference and figure) takes about three and a half minutes on the reference workstation:

```sh
./run.sh --spec enterprise-r11 --scale 120 --context multinational --growth organic --pre-years 1 --post-years 1 \
         --worlds 0 --arms control-hierarchy,dams-witness --jobs 2 --output runs/smoke
```

## Computing resources

Measured complete runs (`docs/r11-resource-measurements.md`): a 10,000-member arm needs about 15 hours for five simulated years and 12 to 14 hours of full validation (both measured with 12 arms sharing 16 vCPU), about 7.3 GB of retained output and up to about 14 GB of working space; the main study (40 arms) therefore needs about 320 GB retained, 6 to 8 cores for about four to five days, and memory well below 1 GB per process. A 50,000-member arm needs at least 75 hours of simulation.

Worlds can run independently in parallel. Admission must measure the complete world and batch, including prehistory, validation, full checkpoints/journals, interrupted attempts, upload, off-GCP transfer and recovery. The historical short probes in `docs/r11-resource-measurements.md` are not a full-retention forecast. Worker count changes wall time and simultaneous disk demand; it does not change the declared population, horizon, world count or model. The candidate has not established a new hardware/cost boundary for complete 10,000- and 50,000-person studies.

Engineering evidence and remaining acceptance boundaries for this preservation candidate are recorded in [the dated validation note](docs/r11-preservation-validation-20261021.md).

## Tests

The storage layer refuses paths through symbolic links, so tests need a temporary directory that is not a symlink (on macOS `/tmp` and `/var` are):

```sh
mkdir -p "$PWD/runs/tmp"
TMPDIR="$PWD/runs/tmp" PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests -p 'test_r11*.py'
```

## Limits

The models generate synthetic organizations from declared assumptions; behavior paths with literature support are marked as such in the thesis, and the rest are assumptions. The historical market baseline has fixed-price, capacity-bound production; the demand-matched variant changes that assumption. The current configuration permits some origin-conditioned trait and trust shifts. These are model assumptions, not empirical estimates of nationality, ability or honesty, and remain a design gap. The observed-population component preserves measured categories without inferring psychological traits or capability from them. The local contract and signature checks do not establish independently operated consensus. Results support conditional comparisons under these assumptions, not predictions for a particular organization.

## Licence and citation

MIT (see `LICENSE`). Cite the exact commit and run manifest you used (`CITATION.cff`). `SECURITY.md` describes the research security boundary; `THIRD_PARTY_NOTICES.md` lists third-party components.
