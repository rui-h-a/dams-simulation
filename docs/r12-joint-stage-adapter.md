# Existing-common-pair engineering stage adapter

This separate candidate adds `research_tools/r12_joint_study.py`. It does not
change `run.sh`, the R11 studies, a governance policy or the core. It binds
already existing complete original common checkpoints and stages only the
existing `no-acquisition`, `preserve-autonomy` and `centralize` interventions.
No target or parent is generated, and no simulated day is advanced by this
adapter. It is the first stage of a potential study adapter, not a formal
driver, full horizon run, independent raw admission or inference entry.

The input is an explicit roster. `world` is the original `JointTerms.global_world`,
not a new repetition assigned to a reused checkpoint. Each complete parent must
be at its original completed offer-day boundary, unoffered and still independently
owned. Both firms, their configurations, terms and complete native JSON/SQLite
group must be present. An R11 `arm.json` or reduced acquisition capsule refuses.
The caller must establish closed ownership of the original group. Hash and
identity rechecks do not establish a continuous writer fence. Actual staging
restores Models and copies complete native groups; it is not an I/O-only or
no-Model operation. It has no integrated preservation lease or resource grant,
so a real stage still needs Root's separate bounded execution arrangement.

```json
{"schema":"r12-existing-common-pair-roster-1","worlds":[
  {"world":91000,"parent_checkpoint":"/ABSOLUTE/EXISTING/common.json"}
]}
```

From the separately frozen and reviewed source, the two explicit commands are:

```sh
python -B -m research_tools.r12_joint_study prepare \
  --roster ORIGINAL_ROSTER.json --output NEW_STUDY_DIRECTORY
python -B -m research_tools.r12_joint_study stage \
  --study NEW_STUDY_DIRECTORY --plan-sha256 EXTERNALLY_FIXED_PLAN_SHA256 \
  --world 91000 --arm preserve-autonomy
```

Preparation uses the existing `CounterfactualPlan.from_checkpoint` native
verification. It fixes the complete core/design/execution source manifest plus
this adapter, ordered world roster, original configurations and terms, native
counterfactual plan, full parent file hashes and file identities. Retain the
printed complete plan SHA outside the study directory for review. Staging requires
that exact SHA and the same source. A source change requires a new reviewed
candidate; the adapter supplies no migration or hash-rebinding bypass.

Stage each assigned world/arm explicitly. It calls only the existing
`JointRun.fork_counterfactual` and `snapshot` APIs, with a single-writer lock. The
new directory contains `staged-common.json`, both native company snapshots and
SQLite sidecars, immutable `lineage-parent` files and the engineering `stage.json`.
Original parent files are read only; their hashes and identities are rechecked
before and after staging. Keep the complete directory group, not only its JSON.

An existing output, changed source, changed original identity, missing sidecar,
different plan/terms or invalid action refuses. No stage is overwritten, silently
resumed, relabelled or deleted. Interrupted files, pending publication files and
partial branch directories remain evidence. Capture stderr and the exit code;
an interrupted stage requires explicit inspection and a separately chosen new
output, not an automatic retry or regenerated original parent. No archive,
retirement, cloud lifecycle, spending or model execution allowance is implied.

Every result keeps `raw_admitted: false`, `formal_worlds_added: 0` and
`inference_authorized: false`. Native integrity and existing source-dependent
lineage verification do not establish full-horizon daily validation, independent
scalar accounting, external validity, full recovery or preservation acceptance.
The staged checkpoint has not completed its horizon.

There is deliberately no `run` command yet. The next integration can restore
`staged-common.json` with `JointRun.restore(checkpoint, fresh_directory,
expected_plan=fixed_counterfactual_plan, expected_arm=arm)`, then call the existing
`dams_sim.cli.run_world(buyer_config, fresh_directory,
restored=run, joint_target_config=target_config, joint_terms=run.terms,
checkpoint_interval_days=..., stop_requested=..., joint_preservation_guard=...)`.
The guard must be an actual valid save lease; a JSON flag is not authority.
`run_joint_world` currently emits joint terminal/runtime records and complete
joint checkpoints, but not the separate final per-firm CSV/native-final/summary
group expected by `r11_inference`. A future runner must retain both terminal
firm groups, actual configuration history, interrupted checkpoints and full
source before adding any raw-admission step. The existing R11 inference adapter
cannot accept these engineering checkpoints under an invented R11 receipt.

Only the standard-library argument/schema/file-boundary tests are runnable in
this source-only phase. They use bounded tiny synthetic JSON, no native research
checkpoint, SQLite, Model or simulated world:

```sh
DAMS_R12_ADAPTER_TEST_TMP=OWNED_REAL_DIRECTORY \
PYTHONPATH=.:tests python -B -m unittest discover -s tests \
  -p test_r12_joint_study_pure.py -v
```

A separately bounded integration on 21 October 2026 exercised both commands
against the retained eight-person-per-firm common checkpoint at day three.
Preparation and the preserve-autonomy stage each exited zero, without advancing
a simulated day. Independent file and exact-value inspection confirmed the
unchanged original group, day, terms and company states; it did not execute a
new branch restore or a full-horizon validator. The public fixture manifest
identifies these original and staged native files separately. This is a local
engineering entry check, not a formal population study or a new independent
world. Actual common-pair preparation and staging still require the caller's
bounded execution and preservation arrangement. No pilot precision rule or
full-horizon study driver is supplied here.
