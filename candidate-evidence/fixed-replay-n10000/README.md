# Temporal fixed-authority replay: complete synthetic evidence

This extends the existing static fixed-stream diagnostic. It is an isolated
authority mechanism experiment, not the10,000-person longitudinal main study,
a calibrated company, a new independent ABM world or evidence of fairness.
The shared tape has10,000 people, two domains,20,403 explicitly ordered events
and observations at days0,1,30,90,180,365. Every policy receives exactly the
same record/confirmation/revocation history, eligibility and attribution.

Complete input and all share vectors are retained as lossless gzip release
assets. `manifest.json` lists raw/gzip byte lengths, SHA256 and download URLs;
`protocol.json` gives the pre-execution design; `summary.json` is only a compact
view, not a replacement for the full raw output. All figures and values must
retain the deterministic, designed character of this evidence.

At this fixed source version, from the repository root:

```sh
python3 -B -m research_tools.fixed_replay_fixture --population 10000 --output NEW_INPUT.json
python3 -B -m research_tools.fixed_contribution_replay --tape NEW_INPUT.json --output NEW_RESULT.json
python3 -B -m unittest tests.test_fixed_contribution_replay
```

The generated input/result SHA256 must equal the raw hashes in the manifest.
Use a fresh output path each time; completed evidence is never overwritten.
The complete downloaded gzip files must pass gzip EOF/CRC and reproduce those
raw lengths/hashes. This does not assert clean Linux/Windows certification,
full-model recovery, whole-research custody or permission for paid compute.

An independent arithmetic implementation reconstructed the final day365 from
the raw tape:9,900 eligible members in each domain,104 revoked and9,896
confirmed records across both domains, no pending records. It matched every
eligible member's five policy shares and their normalization. This check does
not independently audit all other query days or identify behavioral effects.

Execution-source integrity for the reported run was observed before and after
replay by a separate source-pinning wrapper. The simple CLI itself attaches
post-execution file hashes and requires an immutable checkout. Atomic file
publication is not a power-failure custody guarantee: the CLI fsyncs its file,
not its parent directory. Extremely small decay factors may refuse even when
an algebraically rescaled calculation could represent the final product.
These limitations do not change the reported, independently reconstructed run.
