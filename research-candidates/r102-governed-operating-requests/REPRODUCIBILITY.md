# Bounded local engineering reproduction

From this candidate directory, with CPython 3.11+ on a POSIX host and an existing
output parent on a filesystem with at least 80 GiB physically available:

```sh
python3 verify_source.py
python3 run_engineering.py --mode daily --output /absolute/nonexistent/r102-daily
```

Replace the output argument with a new directory outside this candidate. Its
parent must already exist. Existing output paths, symlink ancestors, hardlinked
or special input/output files and a changed complete source roster are refused.
The command imports the exact model only after the publication roster, profile
and entry bytes pass preflight. It performs both arms sequentially, snapshots
each at day 90, restores into a separate fresh namespace and compares the original
and restored records and each firm's semantic digest at each of the next ten
days. Both histories remain in the output; recovery copies are not new worlds.

To run the full unchanged calendar instead, choose another fresh output:

```sh
python3 run_engineering.py --mode full --output /absolute/nonexistent/r102-full
```

Full mode uses the original `run_joint_world` and checkpoint schedule through
1947 days, restores each final `joint-day-001947.json` through `JointRun.restore`,
validates joint metadata and compares exact firm summaries. Both modes retain
actual governed proposal/outcome/consumption journal traces. No provider, lease,
service, paid-resource or authorization action is performed. Inherited transfer
configuration is removed for this local run; an inherited phase-storage override
is refused before output creation. An inherited package identity is
also removed; the raw model's ordinary Git/dirty provenance and the receipt's
exact candidate source hash identify the actual source rather than impersonating
an older commit.

`profiles/engineering-pair.json` is the pinned actual effective engineering
configuration, not a new scientific scenario. Buyer and target each start with
2 people per arm: 8 fresh initial people across the sequential hierarchy and DAMS
arms. Original seeds, world IDs, caps, opt-in governance, joint terms and the
1947-day calendar are unchanged. The operational `max_wall_seconds` was explicitly
increased from 120 to 600 seconds per firm. This entry cannot override the profile.

The entry checks the whole output namespace against 2 GiB, physical output-volume
free space against 80 GiB, process peak RSS against 1 GiB, and its overall monotonic
lifetime against 1800 seconds. It checks full source/profile/entry SHA and native
file identity before import, at arm/preservation boundaries and after receipts;
intermediate compute boundaries also recheck identity, output capacity, free
space, RSS and lifetime. These are cooperative checks; a stuck interpreter or
single indivisible operation requires an external process supervisor. They are
not a hard OS memory sandbox, measured minimum hardware, or paid-compute permission.

RSS conversion explicitly uses macOS bytes and Linux KiB. Actual Model engineering
was measured on CPython 3.14.6/macOS arm64; Linux unit conversion has pure controls
but actual Linux execution and cross-platform byte equality remain unverified.
Packed journal schema 2 requires SQLite's `octet_length` capability. The actual
joint runs use ordinary full SQLite checkpoints. Inclusion of the optional
committed-WAL-page backend does not certify it for this driver; that prototype
requires its exact SQLite 3.53.4 codec and additional original restore gates.

A successful process prints `LOCAL_ENGINEERING_VERIFIED` only after the final
post-receipt guard. Retain exit status, stdout/stderr, `engineering-receipt.json`
and all raw files. The receipt records the full raw-file SHA/native roster (it
excludes itself), all input SHA/native pins and measured host/resources. A receipt
requires successful process exit and absence of `engineering-failure.json`;
pending receipts or partial trees are not successful execution evidence. Failure diagnostics preserve the full exception and cause chain up to a strict
64 KiB encoded bound; an oversized diagnostic is refused while complete stderr
remains the required evidence. Failures
are preserved, never overwritten into a new run or counted as independent worlds.

Pure checks, without constructing/restoring/ticking a Model:

```sh
python3 -m unittest discover -s entry_tests -v
cd source
python3 -m unittest tests.test_governed_operating_requests_r100 tests.test_governed_request_binding_r101 tests.test_governed_backend_replay_r102 -v
```

The runtime model uses the standard library. The included research tools and font
license close inherited lazy dependencies; optional inherited reporting uses the
locked Matplotlib/NumPy environment. A historical driver or core-only wheel is not
this candidate's engineering entry. The generic repository default remains a
separate source and study contract.

Large original engineering raw trees are not shipped in this packet. Published
receipt hashes identify retained evidence but do not replace access to those
bytes or independent full raw/economic revalidation. These executions and pure
controls add no formal sample, empirical calibration, OOS validation, population-
scale study, cloud certification or completed paper.
