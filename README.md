# DAMS research reference implementation

This is a new research repository, separate from the thesis and product histories. It evaluates DAMS contribution-contingent **formal** authority, bounded observation, guild coordination, review/appeal capacity and conditional evidence-substrate costs. All current populations and numerical parameters are synthetic design or stress assumptions. No empirical calibration, field effectiveness, privacy deployment or real consensus performance is claimed.

Use Python 3.11 or newer. The simulation has no runtime dependencies, paid services, dataset download or GPU requirement. From this repository, one command completes a small world, analysis, an editable SVG diagnostic and a self-contained report:

```sh
python3 -m dams_sim smoke
```

```sh
python3 -m dams_sim doctor
python3 -m unittest discover -s tests -v
python3 -m dams_sim run --n 120 --days 60 --regime sublinear --backend central
python3 -m dams_sim reproduce --config configs/reference.json --worlds 3
python3 -m dams_sim benchmark --n 1000 --days 10
python3 -m dams_sim run --checkpoint-day 30
python3 -m dams_sim resume --checkpoint runs/RUN-ID/checkpoint.json
```

`reproduce` runs the five policies × three evidence scenarios on matched independent worlds. It starts at `world` in the configuration, preserves every world-policy row, and uses a fixed world count. It does not stop on significance. `benchmark` runs the complete enabled model and records initialization, simulation, statistics, plotting, total wall/CPU time, peak process RSS, output volume and hashes. Peak RSS is process-lifetime high water, so compare clean processes. Zero network I/O and zero compiled-kernel time describe this standard-library model, not external systems. Every run has a unique ID, immutable completed outputs, atomic JSON/CSV writes and source/config/output hashes. Interrupted or failed runs are marked failed and preserve a checkpoint; a partial horizon is marked checkpointed. `resume` rejects source changes. A new process can exactly reproduce state on the tested platform; cross-platform bitwise equivalence is untested.

Optional packaging, requiring build-tool network access on first use:

```sh
uv sync --locked
uv run dams-sim smoke
uv build
```

Runtime dependencies are empty and `uv.lock` locks the project. `hatchling==1.27.0` is the pinned build backend. The standard-library path works offline without installation. Containers, MPI, GPU and native Windows are untested; macOS arm64 Python 3.14 is tested. Other Python ≥3.11 and Linux are portability targets, not certified support. `doctor` records available host facts without changing settings.

Read [the executable model specification](docs/ODD.md), [configuration and metrics](docs/INTERFACE.md), [factor scope](docs/factor_disposition.json), [assumption registry](docs/parameters.json), and [security boundary](SECURITY.md). Research inference uses independent-world paired comparisons; members and events are not independent replications. The `central`, `witness` and `consensus` scenarios encode explicit delays, per-record resource multipliers and unilateral censorship assumptions. They do **not** execute a database, transparency-log protocol or BFT client. Source truth is fallible in all three scenarios. Central may be sufficient in a trustworthy single-operator setting; higher replication is not assigned a universal benefit.

Policies are equal eligible shares, linear decaying domain credit, sublinear decaying domain credit, performance-ranked tiers, and tenure-ranked local tiers. Allocation shape and update interval are separate controls. Tier sizes/weights are experimental choices. Exact ties average positional weights. Empty eligibility fails; all-zero eligible credit explicitly yields equal shares. Sublinearity compresses concentration and creates identity-splitting incentives; it does not establish fairness or unique identity. Dynamic attacks use equal declared budgets and are compared with their own policy's unattacked world. Analytical identity splitting is implemented separately in `authority.split_gain`; unique-person identity is assumed in the dynamic model.

This readable reference uses Python objects and keeps claims, delay samples and restart state. It is not optimized for ten million full agents. Memory grows with population and retained events; sorting and event queues add costs. Never silently replace this model with aggregation in a scale comparison. Configure `max_wall_seconds`, `max_output_mb`, `max_rss_mb` and `max_events` explicitly. Sampled RSS stops detect process high water at initialization/day/output boundaries; they do not guarantee no transient overshoot. An RSS/OOM failure may lack a new checkpoint; an explicit checkpoint remains available. No command purchases or starts remote compute.

Ownership: `contracts/` is managed by the thesis integration lead, independently of the Python package. It holds Solidity/EVM research regressions; it is not an ABM consensus backend. Publication/release tooling and scientific figures may be integrated by that lead. Current repository publication must be checked against actual remote evidence.
