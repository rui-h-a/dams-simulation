# Zero-day existing-common-pair entry fixture

These are the complete original day-three two-firm checkpoint group under
`lineage-parent/` and the newly staged preserve-autonomy checkpoint group. Each
firm has eight synthetic members. `manifest.json` distinguishes exact file
hashes, executed commands and pending validation. Both entry commands exited
zero; no simulated day or independent world was added.

Use the original checkpoint at `lineage-parent/common-parent.json` in an
explicit roster for the adapter's `prepare` command, then supply the returned
complete plan SHA to its `stage` command. See
[the adapter instructions](../../docs/r12-joint-stage-adapter.md). Use fresh
output directories and keep each native JSON/SQLite group together.

The fixture supports checking entry and lineage preservation. It does not
contain a completed full-horizon comparison. An additional branch restore,
full-horizon raw validation, independent accounts and empirical validation
remain separate checks.
