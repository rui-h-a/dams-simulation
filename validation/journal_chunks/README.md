# Bounded Linux journal validation

This engineering kit extends public commit `ddd185d0d96abae6c07d1e1fb5f9e57f7dc974cb`. All 65 original runtime files retain their exact bytes and executable modes. The core remains `b763b85e34164095911ae1cb85efca123b11205f305cd47e950cf9e0493e0b36`; the longitudinal driver remains `81edc3f731f39157ae562c0ec920416c8c1fe3b642d8938bdd3d99a48d37ecd3`. `runtime-pins.json` binds every original file and the separate schema-1 ledger fixture.

The workflow runs only on explicit `workflow_dispatch`. Its existing filename is registered on the default branch; dispatch selects this validation branch. It uses a standard `ubuntu-24.04` runner, pinned official checkout/setup-uv actions, uv 0.9.26 and Python 3.14.2. It grants only `contents: read`, uses no supplied secrets or cloud configuration, and uploads no artifacts or caches. Test logs and bounded JSON receipts are printed to the workflow log. No automatic push, pull-request, schedule or retry trigger is configured.

The system `/usr/bin/python3` observation and uv-managed Python observation report their own actual Python/SQLite versions and `octet_length` capability. Schema 2 requires that capability; a missing capability must refuse schema 2 while the tiny schema-1 control remains usable. The managed interpreter must actually be Python 3.14.2 and pass schema 2 before the suite can run. A successful Actions runner test does not establish GCP guest compatibility.

The suite preserves these existing controls:

- Six mocked process-ownership controls reject root/child PID reuse, PPID mismatch and identity changes during observation; normal descendants retain fixed births. They do not launch or signal actual processes. The wrapper pins the root PIDfd immediately after launch, retains its original birth, never overwrites enrolled births, and checks the child PPID plus a still-matching parent before enrollment.
- Nine ledger tests compare exact logical rows, payloads, sequence state and SHA with the original schema-1 ledger; test atomic rollback, queues, duplicates, Unicode fragments, self-contained restore, option/output refusal and malformed archives.
- Sixteen legacy longitudinal-model tests and nine checkpoint-codec tests exercise the default storage behavior. Ten benchmark tests are registered; the host-Mach accounting test is intentionally skipped on Linux, with the skip recorded.
- Six model integration controls compare flat and chunked storage daily, across checkpoint/restore, lifecycle/merger/shock, duplicate backlog, exact shared-history fork, raw journal/CSV readers and runtime refusal.
- The scheduler control actually spawns a worker to resume an immutable previous attempt and then run a parent-preserving fork. It verifies schema-2 routing and absence of owned workers after each scheduler run.

Generated fixtures live only under `runs/journal-chunks/`. The Linux wrapper allows at most 180 seconds per stage, 1 GiB observed process-tree RSS, 512 MiB total `runs/` files, 2 MiB per printed stage log, and requires 1 GiB free space. Each owned test process inherits a 2 GiB address-space ceiling, 128 MiB individual-file ceiling and 180 CPU-second ceiling. The job itself ends after 20 minutes. It preserves failure receipts and performs no automatic retry. Memory/disk observations are sampled; process resource limits provide separate hard ceilings. These bounds apply to engineering fixtures and do not change scientific configurations.

Run once on Linux from the repository root:

```sh
/usr/bin/python3 validation/journal_chunks/capabilities.py --label system-python
uv run --no-project --python 3.14.2 python validation/journal_chunks/run_controls.py
```

The ledger fixture is the unchanged earlier schema-1 implementation, SHA-256 `89e7e25da46d14bb2ba2af418a03d6a447ba35e25d4f6b83a4fcc037967a27ac`, loaded under a separate module namespace for differential controls. Other legacy tests are copied exactly from the existing public tests. Integration scripts change only repository/output paths. No Model implementation, calendar, event, parameter, seed, scientific design or runtime file is changed by this kit.

All results are engineering validation. Added accepted scientific worlds: zero. This kit does not launch GCP, test full population workloads, establish N=100,000/N=1,000,000 admission, or complete the formal experiments or thesis. Linux results remain pending until an actual workflow execution and its logs have been verified.
