# Automation and sandbox study: how to run, reproduce and change it

This document describes the study of manual versus automated governance work, with and without a sandbox graph. It
covers what is compared, how to run it from a terminal on Linux or macOS, how to change its parameters, what the
model does each simulated day, how the output is validated and where the model stops. The mechanisms and their
parameter values are specified in [automation-arms.md](automation-arms.md); this document is the operating guide.
Commands are given relative to the directory that contains `run.sh` (`research-candidates/enterprise-current/source`
in the repository). No result is reported here.

## 1. Purpose and scope

The study compares governance arrangements of one simulated multinational firm. Every arm of a world starts from the
same simulated history, the same people and the same random draws up to an adoption boundary, and then diverges only by
the arrangement it applies. Four contrasts are built into the arm list:

1. Manual review against automated work: the same arrangement with and without automated first-pass verification of
   work records, automated coordination and reporting, and a processing charge for the automated work.
2. Authority by hierarchy against authority by contribution: members' formal shares follow performance tiers (hierarchy)
   or follow confirmed contribution with an exponent of 0.5 (contribution-based, "DAMS").
3. With and without the sandbox graph and scoped authority: guild and project sandboxes with processing quotas,
   approvals and a scope rule that shortens the reporting and coordination layers a member's own work passes.
4. Central against witnessed record: a central record has a lower per-review cost and a one-day settlement; a witnessed
   record carries a verification-unit cost and a two-day settlement.

The unit of inference is the world: arms are compared within a world, and worlds are independent replications. All
quantities introduced by the automation and sandbox model are declared design assumptions, not calibrated values
(section 10). Results are conditional on them.

## 2. Requirements

- Operating system: Linux or macOS in a POSIX terminal (arm64 or x86_64). Native Windows is not verified; WSL2 has not
  been validated.
- Tools: `bash`. On first use `curl` is also needed, unless `uv` 0.9.26 is already on the `PATH`.
- Python: managed by `uv`, pinned to 3.14.2 (`run.sh` calls `uv sync --locked --extra analysis --python 3.14.2`). The
  package metadata allows Python 3.11 or newer, but the entry point runs only the pinned version.
- Dependencies: `run.sh` uses `uv` 0.9.26. If that exact version is not on the `PATH`, it downloads the official 0.9.26
  release for the platform into `runs/.tools/uv/`, checks the SHA-256 of the archive and refuses a mismatch. It then
  syncs the locked environment (`uv.lock`) into `.venv/`. The model has no runtime dependencies; the analysis extra
  pins `matplotlib` for the figure. No root rights and no profile changes are needed.
- Offline use: with `DAMS_OFFLINE_DEPENDENCIES=1`, `run.sh` does not download `uv` (it stops if `uv` 0.9.26 is missing)
  and runs `uv sync` with `--offline`; Python 3.14.2 and the locked wheels must already be in the `uv` cache. A study
  itself needs no network access.
- Temporary directory: the storage layer opens every path component without following symbolic links. `run.sh` resolves
  `TMPDIR` before running; for other commands (for example the tests) point `TMPDIR` to a real directory, since `/tmp`
  and `/var` are symbolic links on macOS.
- Threads: `run.sh` sets `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and `NUMEXPR_NUM_THREADS` to 1. Each
  arm is a single-threaded process.
- Small run (section 3): about 340 MB of disk and about 80 MB of memory per process (60 members, seven arms, one year of
  shared history and one year after the boundary); the measured wall time is given in section 3.
- Formal design (section 4): about 28 hours and about 300 GB of disk for one world; see there for the conditions.

Check the toolchain without running anything:

```sh
./run.sh --spec enterprise-r11 --prepare-only
```

## 3. Quick start

One command runs the whole path for a small firm (60 members) with the seven study arms: shared history, the arms,
the independent validators, the inference report and the figure.

```sh
./run.sh --spec enterprise-r11 --scale 60 --context multinational --growth organic \
         --pre-years 1 --post-years 1 --variant full --worlds 1000 \
         --arms control-hierarchy,dams-witness,hierarchy-auto-coord,dams-auto-coord,hierarchy-isa-coord,dams-isa-central-coord,dams-isa-coord \
         --jobs 3 --output runs/automation-quick
```

Measured on a 12-core Apple-silicon computer with `--jobs 3`, while another long job kept that computer at a load average
of 17 to 50: two runs took 290 s and 225 s (4 min 50 s and 3 min 45 s) for the whole command. The command ends with exit status 0 and prints `Inference: runs/automation-quick/inference.json` and
`Figure: runs/automation-quick/effects.pdf`. A quieter machine will be faster. The directory must be empty or new: the
plan refuses to start in a directory that holds other files.

The output directory contains:

- `plan.json`: the bound plan (population, context, growth, horizon, seed, variant, worlds, arms, the source hash of
  `dams_sim/*.py` and the hashes of the design, execution and validation files).
- `events.jsonl`: one line per finished job (prehistory or arm) with peak memory (and wall time for the prehistory), and a
  final `batch-complete` line.
- `preservation-status.json`: the retention check of the finished batch (it lists each arm and states that complete
  preservation is not accepted; transport and off-site custody are separate steps).
- `<world>/prehistory/`: the shared history. `prehistory.json` is its receipt; `checkpoint-day-000366.json` and `.sqlite`
  are the state at the adoption boundary (day 366, because the calendar starts on 2020-01-01, a leap year); `progress/`
  holds yearly checkpoints and `work/` the working ledger. `<world>` is `n60-multinational-organic-pre1-post1-w1000`.
- `<world>/<arm>/`: for each arm `arm.json` (receipt), `summary.json`, `timeseries.csv` (one row per simulated day),
  `final_state.json` and `final_state.sqlite` (the final state and the ledger with the original journal), `progress/`
  (yearly checkpoints; empty in a run of one year) and the working directories `restored/` (the restored boundary
  checkpoint) and `work/`.
- `inference.json` and `effects.pdf`: the paired comparison against `control-hierarchy` and its figure; see the note
  below. With a single world there is no interval; intervals need at least two worlds.

The inference step rebuilds every arm's configuration from the shared-history receipt, including the study variant, and
verifies the arm's original journal again with the same validators before it reads a summary. The per-arm measures below
do not use the inference step.

The automation and sandbox measures of an arm are computed from its directory:

```sh
.venv/bin/python -m research_tools.r11_isa_measures \
    runs/automation-quick/n60-multinational-organic-pre1-post1-w1000/dams-isa-coord
```

The command prints the measures as JSON and writes `isa-measures.json` into every named arm that has an automation
block (all arms of the list except `control-hierarchy` and `dams-witness`). Pass several arm directories to compute
them together. The measures group governance cost (member-hours and processing units kept apart), collaboration across
sandboxes, structural change, scaling, safety, governance quality, record classes and, where an attack is configured,
collusion. They are descriptive; `r11_isa_measures.py` states each rate per realised member-year.

Primary outcomes are defined in code, not in this document. `PRIMARY_ENDPOINTS` in `research_tools/r11_world_count.py`
lists four endpoints with their units: net operating result per member-year, decision regret per available
member-hour, governance member-hours per member-year and witness resource units per member-year. `outcomes()` in
`research_tools/r11_outcomes.py` computes them from `summary.json` and `timeseries.csv`; `research_tools/r11_inference.py`
forms the paired differences against the reference arm `control-hierarchy` over worlds, with Student-t intervals and a
Holm adjustment over this family. The protocol text is Amendment 8 in `r11-formal-protocol.md`. In `outcomes()`
governance member-hours count review, appeal, governance and organizational-verification hours; coordination and
reporting hours are reported separately by `r11_isa_measures`.

## 4. The formal design as a command

Population 10,000 members, context `multinational`, organic growth, 2 years of shared history and 5 years after the
adoption boundary, variant `full`, seed 20261017, one world per machine on worlds 1000 to 1006, seven arms:

```sh
./run.sh --spec enterprise-r11 --scale 10000 --context multinational --growth organic \
         --pre-years 2 --post-years 5 --variant full --worlds 1000 \
         --arms control-hierarchy,dams-witness,hierarchy-auto-coord,dams-auto-coord,hierarchy-isa-coord,dams-isa-central-coord,dams-isa-coord \
         --jobs 7 --output /path/on/a/large/volume/world-1000
```

On the machine for the next world use `--worlds 1001` and its own output directory, and so on up to 1006. The seed is the
default of `research_tools/r11_study.py` (20261017); `run.sh` does not pass a seed option (section 6).

Each machine's directory gets its own `inference.json` (subject to the note in section 3). Reports that are fully
admitted and belong to one study (same source commit and study arguments except the world list; no world twice) are
combined with:

```sh
.venv/bin/python research_tools/r11_pool.py world-1000/inference.json world-1001/inference.json ... > pooled.json
```

Measured resource needs of one world, seven arms in parallel (`--jobs 7`): about 28 hours on a machine with 8 vCPUs
(4 physical cores), about 300 GB of disk for the original journals and the yearly checkpoints, and a peak memory of a few
hundred MB per process. These are measurements on one machine type and will differ on other hardware, disks and loads;
worker count changes wall time and simultaneous disk demand, not the declared design. The plan binds the arguments
(including `--jobs`), the interpreter and the hashes of the model, design, execution and validation files; a resume with
anything different is refused. An interrupted run is therefore restarted with the same command and output directory
(section 8).

## 5. Arms and variants

Arms are defined in `ARMS` in `research_tools/r11_design.py`; variants in `VARIANTS` in the same file. Each arm names a
regime (`hierarchy` or `sublinear`), a record backend (`central` or `witness`), the adoption rule (`never`, or `fixed`:
adoption at the boundary of every guild on one day) and an optional `automation` block (`dams_sim/automation.py`).

- Regime `hierarchy`: formal shares by performance tier. Members are ranked by confirmed credit into equal-sized tiers
  with weights 1, 2, 4, 8 (ties share the average weight). Regime `sublinear`: share of member i is
  C_i^0.5 / sum_j C_j^0.5 over the confirmed credits C of the guild (`authority.py`).
- Backend `central`: review cost multiplier 1.0, settlement 1 day. Backend `witness`: multiplier 1.2 (each human review
  takes `1/1.2` of the slots and adds 0.2 verification units), settlement 2 days.
- Automation block: first review of each claim is automated on the submission day (section 7). Whenever a block is
  present, verification is automated; the further keys switch features on.

### Study arms

| arm | authority rule | record | automation | sandbox graph and scope |
|---|---|---|---|---|
| `control-hierarchy` | performance tiers, no adoption (unchanged organization) | central | none | none |
| `dams-witness` | contribution, exponent 0.5, adopted at the boundary | witness | none (human review of every claim) | none |
| `hierarchy-auto-coord` | performance tiers | central | automated verification; coordination and reporting automation, share 0.5 | none |
| `dams-auto-coord` | contribution, exponent 0.5 | witness | same as above | none |
| `hierarchy-isa-coord` | performance tiers | central | same as above | guild and project sandboxes, scoped authority, core and allocation attempts |
| `dams-isa-central-coord` | contribution, exponent 0.5 | central | same as above | same as above |
| `dams-isa-coord` | contribution, exponent 0.5 | witness | same as above | same as above |

`control-hierarchy` has no adoption; every other arm adopts on the boundary day, `pre-years` calendar years after 2020-01-01 (day 366 for one year, 731 for two). Under variant
`full` every arm pays a processing charge for the processing units it accumulates (section 7); `control-hierarchy`
accumulates none. "Scoped authority" means `in_scope_layers` 1 and `cross_boundary_share` 0.25. "Sandbox graph" includes
quotas with borrowing, approvals, split and merge, read grants and self-modification of the local exception fraction.

### Further arms in `ARMS`

`dams-isa-coord-x50` (`cross_boundary_share` 0.5), `dams-isa-coord-unscoped` (sandboxes without the scope rule),
`dams-isa-coord-fault` (14-day verification outage in sandbox `g0`, 120 days after the boundary) and
`dams-isa-coord-saturated` (quota 0.6 units per member-day) are copies of `dams-isa-coord` with one change each, for
sensitivity studies. The file also holds the arms of earlier studies (`control-process`, `dams-central`,
`dams-consensus`, `linear-witness`, `equal-witness`, `dams-staged-witness`, `dams-triggered-witness` and the `-auto`,
`-auto-measured`, `-isa`, `-isa-full` and further `-isa-*` arms without `-coord`). They are not part of the study arm list.

### Variants

Variant `full` (`_FULL` in `r11_design.py`) turns on every base-level mechanism; it belongs to the shared history and
to every arm. The sensitivity variants differ from `full` in one setting each.

| variant | content |
|---|---|
| `full` | administrative work counts as workload with reference 0.14 workday; decision effect on output (gamma 0.05, memory 20 workdays); record classes 0.70 / 0.25 / 0.05 on arms with automation; processing charged to cash at 0.01 per unit for every arm (`processing_charge_rate`) |
| `full-collude05`, `full-collude20` | collusive review: a cohort of 5 % or 20 % of the initial slots forges records from the boundary to the horizon, spending 0.1 workday per cohort member and day |
| `full-g20` | decision effect gamma 0.2 |
| `full-charge10` | processing charge 0.1 per unit (base rate and automation blocks) |
| `full-coord25`, `full-coord75` | coordination automation share 0.25 or 0.75, set only on arms that automate coordination |
| `full-classes-503515` | record class mix 0.50 / 0.35 / 0.15 |
| `full-span4`, `full-span16` | management span 4 or 16 instead of 8 in the enterprise-operations and task-structure sections |

`VARIANTS` also holds the variants of earlier studies (`baseline`, `accounted-work*`, trait and turnover variants and
others); `python -c "from research_tools.r11_design import VARIANTS; print(sorted(VARIANTS))"` lists all of them.

## 6. Parameters and how to change them

The values of every parameter of the automation and sandbox model, with their role, are in the tables at the end of
[automation-arms.md](automation-arms.md) ("Configuration of the formal study" and "Parameters introduced by the
automation and sandbox model"). The defaults of the automation fields are in `AutomationConfig` in
`dams_sim/automation.py`.

### Changing a model parameter: add a variant

Parameters are changed by adding a variant to `VARIANTS` in `research_tools/r11_design.py` and selecting it with
`--variant`. Existing entries stay as they are, so earlier variants and their results are reproduced byte for byte. The
change lies in `research_tools/`, so the hash of `dams_sim/*.py` (the model source hash stored in every receipt) does not
change. The plan binds `r11_design.py` by file hash, so a directory started before the edit refuses to resume; start a
new output directory.

Keys of a variant entry:

- `automation.<field>`: set on the automation block of every arm that has one (for example
  `'automation.exception_fraction': 0.2`).
- `automation_coordination_share`: sets `coordination_automation` only on arms that already automate coordination.
- `enterprise_operations.management_span` and `organization_tasks.management_span`: span of control.
- `decision_effect`: the decision-effect block (`{}` uses the defaults, `{'gamma': 0.2}` overrides one field).
- `collude`: collusive review with this cohort fraction.
- `processing_charge_rate`, `administrative_fatigue`, `administrative_fatigue_reference` and other top-level
  configuration keys are copied into the shared configuration.

Worked example: a variant whose arms automate 40 % of coordination and reporting. Add one line after
`'full-coord75'` in `VARIANTS`:

```python
    'full-coord40': {**_FULL, 'automation_coordination_share': 0.4},
```

Check the resulting configuration without running a world:

```sh
.venv/bin/python - <<'PY'
from dams_sim.config import Config
from research_tools import r11_design as d
base, branch = d.base_config(scale=60, context='multinational', growth='organic', world=1000, seed=20261017,
                             pre_years=1, post_years=1, variant='full-coord40')
for arm in ('hierarchy-auto-coord', 'dams-isa-coord', 'dams-witness'):
    cfg = Config.from_dict(d.arm_config(base, branch, arm, 'full-coord40'))
    print(arm, cfg.automation.coordination_automation if cfg.automation else None)
PY
```

The expected output is 0.4 for the two arms that automate coordination and `None` for `dams-witness`. Then run the study
with `--variant full-coord40`. A test in the style of `tests/test_small_batch_variants.py` fixes the difference to
`full` in code.

### Changing the study from the command line

| what | how |
|---|---|
| population | `--scale N` (initial members; at least 2) |
| organizational context | `--context startup\|unicorn\|listed\|traditional\|century\|multinational` |
| growth | `--growth none\|organic\|organic+acquisition` (acquisition without organic growth is refused) |
| horizon | `--pre-years Y` (shared history before the boundary; 0 puts the boundary on day 0) and `--post-years Y` |
| worlds | `--worlds 1000,1001,...` (comma-separated integers; each world is an independent replication) |
| arms | `--arms a,b,...` (names from `ARMS`; the inference step compares every arm with `control-hierarchy`, so keep it in the list) |
| parallel jobs | `--jobs N` (processes; changes wall time and simultaneous disk use only) |
| variant, span | `--variant NAME`; `full-span4` and `full-span16` change the span of control, other spans need a new variant |
| seed | not exposed by `run.sh`; call the driver directly (below) |
| output | `--output DIRECTORY`; a relative path is relative to the directory of `run.sh` (to the repository root when called from the root) |

The driver accepts `--seed` and the inference and figure steps can be run separately:

```sh
.venv/bin/python research_tools/r11_study.py batch --scale 60 --context multinational --growth organic \
    --pre-years 1 --post-years 1 --variant full --seed 7 --worlds 1000 --arms control-hierarchy,dams-isa-coord \
    --jobs 2 --out runs/seed-7
.venv/bin/python research_tools/r11_inference.py runs/seed-7 --first-world 1000 > runs/seed-7/inference.json
.venv/bin/python research_tools/r11_figure.py runs/seed-7
```

Population, horizon and growth change the shared history; therefore every combination is a different world key
(`n<scale>-<context>-<growth>-pre<Y>-post<Y>-w<world>`) inside the output directory.

## 7. Model in brief

The engine is `LongitudinalEngine` in `dams_sim/longitudinal_model.py`. One call of `_step` is one calendar day and one
database transaction. Random numbers are counter-based and keyed by seed, world, process name and identity
(`dams_sim/randomness.py`), so an arm does not disturb the draws of another process and a restart reproduces them. The
list gives the order inside the day; the behavioural equations (effort, trust, fatigue, learning) are the same in every
arm.

1. **Day start and lifecycle.** Market orders, entrants, exits and growth steps are applied
   (`EnterpriseOperatingState.start_day`, `_enterprise_begin_day`, `_lifecycle`). Credits decay with half-life
   `credit_half_life_days` (730). Records that are ready are committed (`_commit`): credit = observed work x
   0.5^(age/730), trust +0.015. Guilds adopt on their adoption day and pay the migration cost (`_adopt`).
2. **Authority.** `_authority` recomputes formal shares from confirmed credit: performance tiers (`authority.tier_authority`)
   for `hierarchy`, C^0.5 normalised (`authority.authority`) for `sublinear`.
3. **Who works.** On a workday each active member is present unless on annual leave (probability
   `annual_leave_workdays` / workdays in the year) or sick (`person_context`). Available time is 1 minus care hours.
   Payroll, maintenance, legal, interest and training costs are due; the funding fraction is `min(1, cash / due)` and
   scales capacity.
4. **Coordination and reporting with scoped layers.** Coordination requirement per member is
   `0.02 x layers x (1 - s) + overload + communication`, with layers from the supervisory depth at span 8
   (`EnterpriseOperatingState.coordination_required`, `prepare_coordination`); reporting time is `depth x (1 - s) x
   hours per layer` plus collaboration and verification hours (`OrganizationTasksState.prepare`). `s` is
   `coordination_automation` (0 or 0.5) for members of adopted guilds; each such member-day costs 0.5 processing units.
   Under scoped authority the layers borne are `effective_layers`: `min(d, 1) + share x (d - min(d, 1))` for depth d, with
   `share` = `cross_boundary_share`; the report delay in whole days is `in_scope_delay_layers`
   (`dams_sim/automation.py`). Readiness falls with the layers borne; the delay applies to the record's ready day.
5. **Decisions.** Each guild decides one binary proposal per workday by weighted vote (`_step`, decision block); the
   regret of a wrong or unresolved decision is `|theta|`. With the decision effect, the guild's decision-quality debt
   `D <- D + (min(1, loss / 0.7979) - D) / 20` multiplies next day's potential output by `1 - gamma x D`
   (`decision_effect.update_debt`, `output_factor`).
6. **Effort, output and submission.** Effort is `0.5 + 0.35 trust + autonomy term - 0.4 fatigue - care hours`, clipped to
   the time left after reservations. Output multiplies skill, learning, effort, `(1 - fatigue)`, noise, transition cost,
   help from collaborators and the decision factor, then passes the task and market constraints
   (`OrganizationTasksState.task_allocation`, `EnterpriseOperatingState.allocate_production`). The observed work of the
   record is output plus review error; the record is ready after `1 +` reporting layers days. Collaboration across home
   sandboxes needs an approved project sandbox (`_project_collaboration`, `SandboxGraph.collaborate`); a refused pair loses
   the peer's help for the day.
7. **Automated verification by record class.** In an adopted guild with an automation block (`_automate`) each new claim
   draws a class (`automation.record_class`): objective 0.70, inferential 0.25, disputed 0.05. A disputed claim always
   goes to the human exception lane. Other claims are sampled to humans with `exception_fraction` (0.1), or are
   processed against the sandbox quota (1.35 units per member-day, borrowing from other sandboxes by approval; overflow
   goes to humans). The verifier abstains with probability 0.05 (0.20 inferential), otherwise applies the human rule
   (observed work at least 0.25 and no audit alarm) and errs with 0.05 (0.10 inferential) in each direction
   (`automation.verdict`). Units are 1.0 x the backend multiplier (x2 for inferential). Accepted claims settle after the
   backend's settlement days; rejected claims follow the human rejection path.
8. **Exceptions to people.** Human review and appeal (`_review`, `_review_one`, `_appeal_one`): slots =
   `floor(present x 0.9 x funding / backend multiplier)` for review, 0.1 per member for appeals; each review costs 0.1
   workday and each appeal 0.15, shared by the present members of the domain. A reviewer outside the claimant's sandbox
   needs a read grant (`_reviewer_may_read`). Sandbox self-modification of the local exception fraction runs at the end
   of `_automate` (`SandboxGraph.propose`, `observe`, `settle`): shadow evaluation for 14 days, adoption if health does
   not degrade, automatic rollback after a further 14 days if it does.
9. **Fatigue update.** Workload = effort + coordination + training + network, quality and learning hours; fatigue
   `<- clip(fatigue + 0.15 w - 0.1 (1 - w) - 0.03)`. Then, with `administrative_fatigue` on, the day's administrative
   hours h (review, appeal, verification and the 0.05 governance workday) add `0.25 (h - 0.14)` to fatigue. Absent
   members recover 0.1.
10. **End-of-day processing charge.** The day's processing units (automated verification, witness verification units of
    human reviews, coordination automation) x the rate (0.01) are paid from the cash left after production
    (`_step`, `charging` block); an unpaid part is recorded in `operating_unfunded_resource_units`. The charge enters the
    growth model from the next day.
11. **Day record.** Metrics, history row and journal entries (`day_end`) are written; the transaction commits.

Cross-checks of this order against the engine are in `tests/test_automation_arms.py`, `tests/test_decision_project.py`,
`tests/test_classes_coordination_collusion.py` and `tests/test_full_fixes.py`.

## 8. Validation and acceptance

Every arm run ends with independent validators (`_validate` in `research_tools/r11_study.py`); a failing arm raises, is
reported in `failure.json` and is not counted.

- `research_tools/validate_longitudinal.py` rereads the original journal and the daily CSV without constructing the
  model. It checks that the CSV, the stored state history and the journal agree day by day, that the observed stocks and
  cumulative metrics follow from the events, that the summary equals the retained state, and, for the automation blocks,
  that each automated verification, record class, sandbox request, approval, quota flow, project-sandbox lifecycle step,
  processing charge and decision-effect debt follows from the keyed draws and the recorded events.
- `research_tools/validate_organization_tasks.py` rebuilds the reporting, collaboration and verification topology
  independently of the production code and recomputes the network request, readiness, production potential (including the
  decision factor), help input with the recorded exclusions and the reporting automation of each present member, then
  compares them with the plan and work records.
- `verify_snapshot` checks the checkpoint envelope, the state hash and the SQLite ledger against the configuration.

Receipts: `prehistory.json` holds the source hash, the full configuration, the branch day and the checkpoint hash.
`arm.json` holds the arm and its specification, `source_sha256` of `dams_sim/*.py`, the hash of the parent receipt, the
full configuration, `state_semantic_sha256` and `ledger_semantic_sha256`, the result of each validator (`validators`),
the file hashes (`files`), wall times, peak memory, final membership and closure day.

Resume: the shared history and the arms write a checkpoint every simulated year (`progress/checkpoint-day-*.json`).
Running the same command again with the same output directory restores the newest verified checkpoint, keeps interrupted
attempts as evidence (`interrupted-*`, `failure-attempt-*.json`) and recomputes the rest; a finished prehistory or arm
(`prehistory.json` or `arm.json` present) is not run again. A changed source, argument (including `--jobs`) or
interpreter refuses to resume (`bind_plan` in `research_tools/r11_retention.py`). Retention: original journals, prehistory, checkpoints and
interrupted evidence are all kept; `./run.sh --spec enterprise-r11 --inspect-retention --output DIR` reports what is
present, and `--recover-checkpoint` restores a checkpoint into a new directory without advancing a day.

A third party can check from the small files alone (`plan.json`, `events.jsonl`, `prehistory.json`, each `arm.json`,
`summary.json` and `timeseries.csv`): that all arms of a world share the parent receipt hash and source hash, that the
configuration of each arm equals `arm_config` (in `research_tools/r11_design.py`) applied to the configuration in
`prehistory.json`, that the validators report `pass`,
and that the outcomes follow from `summary.json` and `timeseries.csv`. Rechecking the journal, the daily accounts and the
state requires the large files of an arm and the validators above.

## 9. Tests

From the directory that contains `run.sh`, after `./run.sh --prepare-only`:

```sh
mkdir -p "$PWD/runs/tmp"
TMPDIR="$PWD/runs/tmp" PYTHONPATH=.:tests .venv/bin/python -m unittest \
    test_small_batch_variants test_full_fixes test_automation_arms test_decision_project \
    test_classes_coordination_collusion
```

The five modules ran 121 tests without failure in about 22 minutes on the computer described in section 3 (under load);
most of the time is spent in small validated engine runs.

| module | covers |
|---|---|
| `test_small_batch_variants` | configuration of the span variants, of the unscoped, fault and saturated arms, adoption at founding (`--pre-years 0`); earlier variants unchanged |
| `test_full_fixes` | shadow evaluation of self-modification, reporting delay under scoped authority, symmetric processing charge, sensitivity variants of `full`, small validated engine runs |
| `test_automation_arms` | automated verification, error model, quotas, sandbox lifecycle and approvals, scoped authority, fault study; earlier arms produce byte-identical daily series |
| `test_decision_project` | decision effect, project sandboxes, core and allocation attempts, checkpoint round trip, tampering the validators must refuse |
| `test_classes_coordination_collusion` | record classes, coordination automation, processing charge, collusive review, a checkpoint inside an arm, tampering controls |

The entry, retention and inference code are covered by `test_run_r11_entry`, `test_r11_preservation`,
`test_r11_study_resume`, `test_r11_inference`, `test_r11_world_count_a8`, `test_r11_pool` and `test_r11_figure`; the
command `... -m unittest discover -s tests -p 'test_r11*.py'` runs the `test_r11*` group.

## 10. Known limits

- The constants introduced by the automation and sandbox model (error rates, record class shares, quotas, charge rate,
  coordination share, attempt probabilities, decision-effect gamma, administrative reference of 0.14 workday) have no
  field calibration. They are declared design assumptions; the sensitivity variants vary some of them.
- The record class of a work record is assigned by an exact keyed draw. The verifier is never misled about the class and
  the draw does not depend on the evidence, on the audit alarm or on whether the record is forged.
- Sandbox bookkeeping has no processing cost and no member time: requests, approvals, grants, lifecycle changes, shadow
  evaluation and logging consume nothing. Only verification and coordination automation are charged.
- Core-modification and allocation-rule attempts are never carried out; they are blocked or sent to the parent and counted.
  Their only effect is that the sandbox's ordinary local proposal for that round is not made.
- Account manipulation and money flows between members are not modelled. Credit is not transferable and members hold no
  assets, so a bribe in the collusion variants has no payment to represent and no laundering model exists.
- Scoped authority is a rule on the layers a member's work passes; team sandboxes are represented by this rule, while
  guild sandboxes are graph objects. Homes of members are leaf guild sandboxes.
- The shared history is simulated, not observed; the firm is a reference multinational, not a reconstruction of any
  company. Results support conditional comparisons under these assumptions.
- Populations above 10,000 members were not simulated for this study. Memory and disk grow with population and with the
  retained journals.
- Exact state reproduction is tested on the measured platform; cross-platform bitwise equality is not tested.
