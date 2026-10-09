# Root handoff

Review activation.patch; source/research_tools/compute_only_lifecycle.py and new source/tests/test_compute_only_activation_uncertainty.py are the only changes. Exact parent preservation freeze23ae0fee4db698396e4616b542946936fcb5241ea0ae983c686fc2ba4f0919e2 remains unchanged.

Run `PYTHONDONTWRITEBYTECODE=1 python3 -B run_controls.py NEW_EXCLUSIVE_ROUND` from this namespace. It only uses owned opaque native codec/Collector/spool filesystem I/O, fake guest/SDK and explicit fixture-only scientific gate; no actual Model/auth/network/provider/ledger. Final stable evidence is round-2 and round-3; round-1 failure is preserved. It checks original21/preservation12/new3 and records literal old test12 unchanged as historical FAIL, not PASS.

Root new package must incorporate this lifecycle byte pin as well as preserved remotee3ab0ac521a69a59336b641ad06d5a33250862a4342fb9e807767a5978358cb7 and existing Collector/transport/runtime pins. No new schema, stage, hold, ledger, authorization, deadline, network or machine assumption is introduced. Unknown activation cannot be retried with a new operation/spool; reconcile only the original provider ID/source/runtime/counters and keep evidence. Normal closeout remains original accepted preservation closure before active DELETE. Provider D still deletes at its original absolute time; begin transport/stop sufficiently early, do not treat D failure as safe successful preservation.

All producer writers/processes stop at this freeze. Root must delegate a different owner for finite unknown-started/noDELETE/no retry and success receipt recheck before actual paid activation. No other framework or tests requested.
