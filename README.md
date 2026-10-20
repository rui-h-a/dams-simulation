# DAMS simulation: research reference implementation

This repository contains the simulation models, study protocols, analysis tools and tests behind the computational evaluation in the DAMS thesis (Decentralized Autonomous Management System). DAMS is a governance design that links verified contribution to domain-specific formal authority while keeping procedural accountability, guild autonomy and data rights. The code here evaluates parts of that design under declared assumptions. It is a research instrument, not a governance product, and none of its parameters is calibrated to an observed company.

## What is in the repository

| Path | Content |
|---|---|
| `dams_sim/` | The simulation engine. The enterprise model (version r11.3, with r11.4 adding an optional sickness-spell rule) simulates whole organizations day by day: people with correlated traits, guilds, teams, departments and sites, a market, finance, organic growth, contraction, closure and acquisitions. The shorter reference model isolates single mechanisms over 60 days. |
| `research_tools/r11_design.py` | Study design: organizational contexts, growth modes, adoption arms, assumption variants and the sources of their parameters. |
| `research_tools/r11_study.py` | Paired-world driver: generates each world's history once, copies the complete state at the adoption boundary to every arm, runs the arms, validates them with independent checkers and resumes from checkpoints. |
| `research_tools/r11_outcomes.py`, `r11_inference.py`, `r11_world_count.py`, `r11_robustness.py`, `r11_figure.py` | Outcomes per arm, paired Student-t intervals with Holm adjustment, the pre-registered world-count rule, variant-versus-baseline reports and a per-study effect figure. |
| `docs/r11-formal-protocol.md` | The study protocol and its dated amendments, each recorded before the worlds it covers were run. |
| `docs/r11-resource-measurements.md` | Measured run time, memory and disk use. |
| `contracts/` | The Solidity reference contract used for local allocation checks. |
| `tests/` | Unit and regression tests. |

## Requirements

macOS (arm64 or x86_64) or Linux (x86_64 or aarch64), `bash` and `curl`. Nothing needs root. On first use `run.sh` downloads uv 0.9.26 from its official release, checks the archive against a pinned SHA-256, installs Python 3.14.2 and the locked dependencies from `uv.lock`, and keeps them inside `runs/.tools`. Native Windows is not supported; WSL2 is untested.

## One command from source to result

```sh
./run.sh --prepare-only          # install the pinned toolchain and run the environment check (doctor)
./run.sh --spec enterprise-r11 --scale 30 --context startup --pre-years 0 --post-years 1 \
         --worlds 0,1 --arms control-hierarchy,dams-witness --jobs 2 --output runs/example
```

The second command generates two independent worlds of a 30-member startup, runs one year under the kept hierarchy and under DAMS on a witnessed record, validates every arm, and writes:

- `runs/example/<world>/<arm>/arm.json`, `summary.json`, `timeseries.csv` and a compact final state for each arm;
- `runs/example/paired-outcomes.json`, the outcome vector of every arm;
- `runs/example/inference.json`, paired differences from the first arm with 95 % intervals;
- `runs/example/effects.pdf`, a figure of those differences.

On a clean clone with an empty cache this took 1 min 49 s on an Apple M2 Pro, including the toolchain download. Running the same command again on the same `--output` resumes from the last checkpoints; working journals are deleted after validation, so only receipts, outcomes and figures stay.

Options: `--context startup|unicorn|listed|traditional|century|multinational`, `--growth none|organic|acquisition|organic+acquisition`, `--pre-years` (history before adoption), `--post-years`, `--worlds`, `--arms`, `--jobs`, `--variant`. The arms are `control-hierarchy`, `control-process`, `dams-central`, `dams-witness`, `dams-consensus`, `linear-witness`, `equal-witness`, `dams-staged-witness` and `dams-triggered-witness`. The variants are listed in `research_tools/r11_design.py` (`VARIANTS`); each changes one family of assumptions.

## Reproducing a thesis study

Each study in the protocol states its command, worlds and source commit. For example, the mid-life traditional-firm study (Amendment 2) is

```sh
./run.sh --spec enterprise-r11 --scale 300 --context traditional --growth organic --pre-years 2 --post-years 5 \
         --worlds $(seq -s, 1000 1064) --arms control-hierarchy,dams-witness --jobs 8 --output runs/traditional
```

The formal studies ran at commit `93e5b87`; the sickness-spell studies at `e027d28` and the demand-matched studies at `4347ccb`, whose simulation package is byte-identical to `e027d28`. Version r11.4 in its default mode reproduces r11.3 configurations, journals and outcomes exactly. Results are deterministic for a given world, arm and source: random numbers are keyed to the world, person and event, not to worker processes, so the number of jobs changes run time and nothing else. Same-platform reruns are bit-identical; across platforms the same tolerance applies as in the thesis.

## Computing resources

A 300-member world arm over five years takes about 5 to 15 minutes on one core and 70 to 135 MiB of memory. A 10,000-member arm takes about five hours and several gigabytes of temporary disk; a 50,000-member arm takes about half a day and 13 to 27 MB of disk per simulated day. Worlds are independent, so studies parallelize across cores and machines without communication. A workstation is enough up to a few hundred members and a few hundred worlds; larger populations need machines with one disk per running arm. See `docs/r11-resource-measurements.md` for measurements.

## Tests

The storage layer refuses paths through symbolic links, so tests need a temporary directory that is not a symlink (on macOS `/tmp` and `/var` are):

```sh
mkdir -p "$PWD/runs/tmp"
TMPDIR="$PWD/runs/tmp" PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests -p 'test_r11*.py'
```

## Limits

The models generate synthetic organizations from declared assumptions; behavior paths with literature support are marked as such in the thesis, and the rest are assumptions. The enterprise market uses a fixed price, so the simulated firms produce at their capacity limit. Demographic and origin labels never enter equations for skill, honesty or eligibility. The local contract and signature checks do not establish independently operated consensus. Results support conditional comparisons under these assumptions, not predictions for a particular organization.

## Licence and citation

MIT (see `LICENSE`). Cite the exact commit and run manifest you used (`CITATION.cff`). `SECURITY.md` describes the research security boundary; `THIRD_PARTY_NOTICES.md` lists third-party components.
