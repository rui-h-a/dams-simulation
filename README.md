# DAMS retained small-enterprise five-year study

This operational increment adds `small-enterprise-5y` to the existing `run.sh`
entry. It dispatches the retained `research_tools.small_enterprise` helper; it
does not introduce a new model, change the frozen study, or modify an active run.
The public default remains bounded local validation. No selector implicitly
creates paid cloud resources.

## Run or prepare a study

From the repository root, use an explicitly admitted runtime-limits JSON and a
persistent study output directory:

```bash
./run.sh --spec small-enterprise-5y --scale 30 --output /path/to/study-n30 --runtime-limits /path/to/runtime-limits.json
```

The small-study selector requires all three arguments. `--scale` accepts only
`30`, `120`, or `300`; no population, time horizon, or sample count is silently
reduced. It runs the original helper as:

```text
python -m research_tools.small_enterprise --population PEOPLE --output DIRECTORY --runtime-limits JSON
```

Every value option rejects a missing or empty value and a following option token
before bootstrap. For a file or directory whose name begins with `-`, use a
`./` prefix. A missing path cannot consume `--prepare-only` and start a case.

Add `--prepare-only` to write and verify the frozen study declaration and pilot
inventory without executing scientific cases. Unlike generic preparation, this
calls the same small-study helper with its existing `--prepare-only` flag.

Runtime limits are separate from scientific factors. Their schema and validation
are in [`dams_sim/runtime.py`](dams_sim/runtime.py). The retained helper requires
`journal_chunk_bytes: 1048576` and a non-null `per_world_rss_bytes`; CPU, memory,
event, timeout, checkpoint, storage, and absolute UTC deadline limits must be
admitted for the actual machine and complete workload. Do not copy another
machine's limits, bypass capacity guards, or treat a prepared inventory as an
executed case. This selector rejects `DAMS_CLOUD_PRIVATE_CONFIG`; paid execution
requires a separately admitted coordinator.

## Scientific identity and inference

The frozen scientific source is commit
`f9b8740bd21735ac9962d382fdf0ca652e07e917`. Its existing public source snapshot is
`30a604e776ecf1c5c0196e6ed44f6b93ac1312b5`. A subsequent operational release has
its own commit identity; it must not be reported as execution of either earlier
commit.

| Component | SHA-256 |
| --- | --- |
| Retained scientific core | `b763b85e34164095911ae1cb85efca123b11205f305cd47e950cf9e0493e0b36` |
| Longitudinal driver | `81edc3f731f39157ae562c0ec920416c8c1fe3b642d8938bdd3d99a48d37ecd3` |
| Small-study helper | `2732dc6ff3d73e9848c022522e02a744ca0e77b775d472227585e8229784494a` |

The conditional synthetic startup comparison retains seed `20261008`, Gregorian
calendar start `2020-01-01`, five complete observation years (1,827 calendar
days), and a 90-day settlement tail: 1,917 days per complete strategy case.
Each independent world pairs existing hierarchy with founding DAMS under the
same retained central backend. It retains adoption costs, personnel turnover,
the model's explicit exogenous workforce targets, four guilds, and two sites.
It is not an empirically calibrated or endogenous-growth model.

Four pilot worlds determine a separate fixed confirmation roster through the
predeclared precision rule, with 16–128 independent confirmation worlds. Pilot
worlds are excluded from confirmation inference; selection is not driven by
significance or whether DAMS wins. The existing N30 and N120 rosters each froze
66 confirmation worlds, giving 70 paired worlds and 140 strategy cases per
assigned study. That is an observed roster, not a universal hard-coded sample
count or a claim that N120 or N300 has finished.

The helper records source, driver, entry, runtime, specification, inventories,
case identities, raw-validated paired results, effects, and adjusted confirmation
intervals. Reusing an existing output requires its exact frozen runtime and
scientific identity. Completed cases are validated and retained rather than
generated again. A changed source, roster, or runtime is refused; do not edit
`study.json`, checkpoints, or `confirmation-roster.json` to bypass that refusal.
Failures, closures, adverse results, and undefined exposure remain evidence.

## Environment and verification scope

`run.sh` preserves uv `0.9.26`, managed Python `3.14.2`, `uv sync --locked --extra
analysis`, and the existing lockfile. First installation may download the pinned
uv release, Python, and dependencies. `DAMS_OFFLINE_DEPENDENCIES=1` requires the
fixed tool and dependencies to be present and adds `--offline` to locked sync.
Scientific execution uses `uv run --no-sync`; numerical thread limits are kept
at one. The wrapper retains its Linux/macOS POSIX guard; native Windows has not
been verified.

Run the finite entry-control tests without importing or executing the model:

```bash
python3 -m unittest discover -s tests -p test_small_enterprise_entry.py -v
bash -n run.sh
```

These tests substitute a recording uv executable. They verify argument routing,
required inputs, locked preparation, quoting, refusal, and exit propagation.
They do not certify clean-machine installation, platform compatibility,
checkpoint equivalence, statistical precision, or a completed scientific study.
Those require their own actual execution and raw-data evidence.
