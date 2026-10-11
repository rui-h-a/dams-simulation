# Complete paired arms in sequential producer waves

This operations version changes only the small-enterprise entry and its recorded provenance. Its scientific parent is `30a604e776ecf1c5c0196e6ed44f6b93ac1312b5`; the retained model source is `b763b85e34164095911ae1cb85efca123b11205f305cd47e950cf9e0493e0b36`, and the scheduler/raw-validation driver is `81edc3f731f39157ae562c0ec920416c8c1fe3b642d8938bdd3d99a48d37ecd3`. Model, seeds, initial worlds, complete calendar, endpoints and precision rule remain unchanged.

The N300 design compares hierarchy and founding DAMS under the same central evidence scenario for 1,827 observation days plus a 90-day settlement tail. Four pilot pairs assign a separate fixed confirmation roster of 16-128 worlds, using the existing precision rule; pilot worlds are excluded from confirmation. Workforce targets are explicitly exogenous. This package establishes neither endogenous enterprise growth nor empirical calibration, and publishing it does not establish a completed N300 world.

Run from a clean checkout of this operations snapshot:

```sh
python3 -m research_tools.small_enterprise --population 300 --output results/n300-wave-v1 --runtime-limits docs/frozen-studies/small-enterprise-five-year/n300-wave-runtime.json --producer-wave-width 1
```

The recorded execution deadline is finite. For later reproduction, set a new explicitly recorded operational deadline and stop cutoff in a copied runtime file before first admission. This does not alter the scientific calendar or permit shortening a case. The entry freezes that runtime's digest and refuses subsequent changes to its output directory. An existing study made with another entry or producer-wave setting must retain its original namespace; this operations version refuses to overwrite it.

Each producer retains the full 16 GiB case cap and actual 80 GiB free-space floor. The unchanged Scheduler measures real current free space and retained pair bytes before starting a producer. The entry waits for the first producer's ownership to close and validates its full raw outputs before submitting the second arm. If either admission or validation fails, no complete paired receipt is produced. A completed first arm is retained for exact continuation. A cached closed case does not require a new producer reservation. Recovery copies, engineering controls and unpaired trajectories are not independent worlds.

Sequential execution reduces concurrent reservations; it does not guarantee that both complete cases fit. Pair storage remains capped at 32 GiB plus the existing 256 MiB metadata allowance. No original evidence is removed by this change. The default wave width remains two for compatibility, with its original concurrent reservation gate.

Run the orchestration and differential controls:

```sh
python3 -m unittest discover -s tests -p test_small_enterprise_wave.py -v
```

These twelve controls exercise sequencing, full caps, raw validation and ownership failures, second-arm capacity refusal, cached results, selected manifest/attempt references, parallel compatibility and unchanged scientific configuration. They use simulated producer receipts and do not run formal models. Complete-case runtime, raw recovery and final statistical acceptance remain separate requirements.
