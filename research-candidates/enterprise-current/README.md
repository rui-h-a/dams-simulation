# Current enterprise research snapshot

The entire 400-file source tree is preserved under [source/](source/), from commit [`36dcbf7e580b081acb49c4d2c24d0f57fbee558b`](https://github.com/rui-h-a/dams-simulation/tree/36dcbf7e580b081acb49c4d2c24d0f57fbee558b). [manifest.json](manifest.json) gives every file hash.

This is the latest inspectable research candidate (branch research/enterprise-amendment8), including Amendment 8, batched complete-retention studies, the attack-cohort membership correction, full-retention R11 entry, full-history R12 mechanisms, ACS diagnostics, temporal fixed-contribution replay and the unintegrated observed-population provider. [Current evidence and pending work](source/docs/CURRENT_RESEARCH.md) distinguish execution from scientific acceptance.

For the existing enterprise local entry, start from this directory:

```sh
cd source
./run.sh --prepare-only
./run.sh --help
```

The data provider is not automatically selected by the enterprise entry. These commands do not constitute an accepted 10k/50k study or authorize cloud spending. Use the exact source required by each historical result.
