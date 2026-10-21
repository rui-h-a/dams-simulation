# Current enterprise research snapshot

The entire 395-file source tree is preserved under [source/](source/), from commit [`3b4d44676872fff8d602cafbc47b49f0df6f79b9`](https://github.com/rui-h-a/dams-simulation/tree/3b4d44676872fff8d602cafbc47b49f0df6f79b9). [manifest.json](manifest.json) gives every file hash.

This is the latest inspectable research candidate, including full-retention R11 entry, full-history R12 mechanisms, ACS diagnostics, temporal fixed-contribution replay and the unintegrated observed-population provider. [Current evidence and pending work](source/docs/CURRENT_RESEARCH.md) distinguish execution from scientific acceptance.

For the existing enterprise local entry, start from this directory:

```sh
cd source
./run.sh --prepare-only
./run.sh --help
```

The data provider is not automatically selected by the enterprise entry. These commands do not constitute an accepted 10k/50k study or authorize cloud spending. Use the exact source required by each historical result.
