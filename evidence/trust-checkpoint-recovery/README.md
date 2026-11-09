# Checkpoint-anchored recovery of the local record harness

This deterministic comparison uses the unchanged `Ledger` from the
[pinned original research harness](https://github.com/rui-h-a/dams-simulation/blob/968ecadacedd88ad815a5f19de51de2011e853d0/contracts/trust-protocol-test.cjs).
The candidate takes an externally authenticated roster, threshold, scope,
epoch, sequence and head. It compares the snapshot configuration with the
trusted configuration, replays all supplied records through the original
`Ledger.accept`, and requires the resulting sequence and head to match the
trusted checkpoint exactly. The fixture supplies the checkpoint independently
of each snapshot under test. A real client must authenticate its checkpoint
and establish its freshness through an independent process.

The two versions accept the intact two-record history and reject an unsigned
alteration. The candidate additionally rejects six variants the original
restorer accepts. These substitute the roster, lower the threshold, truncate a
valid prefix, replace the history at the same height with another fully signed
history, change the scope, or change the epoch. The alternate history is signed
with disposable fixture credentials to test the checkpoint check. A fresh
ledger with the same trusted configuration and checkpoint still accepts the
same bundle. This candidate evaluates restoration only. Instance binding,
persistent signer locks, agreement progress, source appraisal and production
consensus are separate questions.

## Reproduce the nine cases

Place the pinned `trust-protocol-test.cjs`, `package.json` and
`package-lock.json` from the original source's
[contracts directory](https://github.com/rui-h-a/dams-simulation/tree/968ecadacedd88ad815a5f19de51de2011e853d0/contracts)
in a local directory. From this package's directory run the following with
Node and npm. The recorded checks used Node 23.11.0 and ethers 6.13.5.

```sh
npm ci --prefix PINNED_CONTRACTS_DIRECTORY --offline --ignore-scripts --omit=optional --no-audit --no-fund
node verify-recovery.cjs PINNED_CONTRACTS_DIRECTORY
```

For the first install on a system without a populated npm cache, omit
`--offline`. The verifier checks the length and SHA-256 of all three original
files, requires the locked ethers version, runs the cases, and compares the
full report with `recovery-results.json`. The tests use synthetic disposable
credentials and generate no organizational simulation data.
