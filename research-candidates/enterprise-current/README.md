# Current enterprise research snapshot

The entire 400-file source tree is preserved under [source/](source/), from commit [`36dcbf7e580b081acb49c4d2c24d0f57fbee558b`](https://github.com/rui-h-a/dams-simulation/tree/36dcbf7e580b081acb49c4d2c24d0f57fbee558b). [manifest.json](manifest.json) gives every file hash.

This is the latest inspectable research candidate (branch research/enterprise-amendment8), including Amendment 8, batched complete-retention studies, the attack-cohort membership correction, full-retention R11 entry, full-history R12 mechanisms, ACS diagnostics, temporal fixed-contribution replay and the unintegrated observed-population provider. [Current evidence and pending work](source/docs/CURRENT_RESEARCH.md) distinguish execution from scientific acceptance.

For the existing enterprise local entry, start from this directory:

```sh
cd source
./run.sh --prepare-only
./run.sh --help
```

The automation and sandbox study (manual against automated governance work, with and without the sandbox graph and scoped authority) has its own guide, [source/docs/automation-study.md](source/docs/automation-study.md), with a quick start that finishes in minutes:

```sh
cd source
./run.sh --spec enterprise-r11 --scale 60 --context multinational --growth organic \
         --pre-years 1 --post-years 1 --variant full --worlds 1000 \
         --arms control-hierarchy,dams-witness,hierarchy-auto-coord,dams-auto-coord,hierarchy-isa-coord,dams-isa-central-coord,dams-isa-coord \
         --jobs 3 --output runs/automation-quick
```

The data provider is not automatically selected by the enterprise entry. These commands do not constitute an accepted 10k/50k study or authorize cloud spending. Use the exact source required by each historical result.
