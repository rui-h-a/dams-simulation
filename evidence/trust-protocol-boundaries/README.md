# DAMS local record protocol checks

These checks reproduce the original 17 test groups and 64 quorum-intersection cases, then probe progress, namespace separation, and recovery using the same serialized-slot implementation. They run locally and generate no organizational simulation data.

From the root of this checkout, install the locked dependencies and print the result.

```sh
npm ci --prefix contracts --ignore-scripts --omit=optional --no-audit --no-fund && node evidence/trust-protocol-boundaries/protocol-boundaries.cjs . .
```

The checker verifies all six source and reference file digests against [the pinned source commit](https://github.com/rui-h-a/dams-simulation/tree/968ecadacedd88ad815a5f19de51de2011e853d0/contracts) and requires ethers 6.13.5. The stored result uses Node 23.11.0. Test credentials are generated for the run. The output records no credential material.

The verifier counts distinct signing credentials. A quorum count does not by itself ensure that signers converge on one proposal. Two honest signers can lock each of two proposals and leave both short of a certificate. A restarted signer that loses its lock can join an equivocating signer in accepting conflicting heads. Reusing the signer objects across different scopes can also block the second scope at the same sequence and epoch.

Restoration in this harness takes the roster and threshold from the supplied snapshot. Restoring a valid prefix does not establish completeness. With the same scope, roster and initial state, the local signatures can be reused across instances because the signing domain has no instance identifier. An invalid extra signature also makes the checker reject a certificate with enough valid signatures.

These results concern the research harness. They do not establish failures or performance for a deployed consensus protocol.
