# Evidence chronology and current release

`source-freeze.json` is the **initial pre-review snapshot**, core 142331..., with
19 tests. It is retained historical evidence, not the current model. Independent
counterexamples led to fixes for expired daily capacities, actual attendance,
fixed attack cohorts, review lotteries and provenance/integrity boundaries.
`release-source-map.json` identifies the release files and current executed core.
The original manifests' commit/dirty fields are never retrospectively rewritten.

`validation-tests-final-core.log` records 26 core regressions. The CLI resume test
later exposed a test-fixture directory-order assumption under a faster locked
interpreter; the test now identifies the result by `restart_origin`, retaining the
same state comparison and exact parent/checkpoint provenance checks. No model or
experimental output was changed for this test fix. Full-entry logs retain both
its first failure and subsequent actual passing execution.

Benchmark directories preserve chronology. Only
`20261007T143303-cpu-benchmark-6ad2db33c118` is the final core-3d67... batch.
`20261007T141530-cpu-benchmark-2ae7d6ba8f34` is the older-core preliminary batch;
`20261007T141428-cpu-benchmark-04a950dda22c` is source-invalidated and is not final
performance evidence. The million-person stop and ten-million-event preflight
refusal remain failures/refusals. They are never plotted as completed worlds.

Compact metadata, plans, measured/derived CSVs and exact source snapshots are in
Git. Full world states, all trace CSVs and reports are in the hashed release raw
archive. Private layout-validation links were excluded without following them.
These outputs are synthetic research data and measured local hardware evidence;
there are no customer datasets, private keys or restricted literature fulltexts.
