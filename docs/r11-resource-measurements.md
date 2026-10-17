# R11 enterprise model: measured resources

Machine: Apple M2 Pro (8 performance + 4 efficiency cores), 16 GiB RAM, internal
SSD, CPython 3.14.2, one single-threaded process per world arm. Model
`longitudinal-enterprise-person-context-11-2` (multinational context, organic
growth), journal chunks 4 MiB. Measurements are short probes; long horizons are
extrapolations until the pilot and formal runs report their own totals.

| Initial population | Init (s) | Workday (s) | Non-workday (s) | Mean per calendar day (s) | Peak RSS (MiB) | Source |
|---|---|---|---|---|---|---|
| 10,000 | ~9 | ~11-13 | ~1 | ~8.9-9.7 | ~450 | R10/R11.1 probes, 30 days and pilot progress |
| 50,000 | 9.5 | 30-42 | 2.4 | ~26 | 630 | `r11-probe-n50000.json`, 10 days |
| 100,000 | 18.3 | 91-114 | 5 | ~76 | 1115 | `r11-probe-n100000.json`, 5 days |

Workday cost rises over the first days as review queues fill; long-run cost is
measured by the pilot. The SQLite ledger with compressed journal grows by roughly
2.6 MB (10k), 13-27 MB (50k) and 25-67 MB (100k) per simulated day, so a single
seven-year arm needs on the order of 7 GB, 30 GB and 60 GB of temporary disk.
After independent validation the driver keeps either the full journal (audit
worlds) or a compact state image; released journals are reproducible by
deterministic replay.

Consequences: memory is not the binding constraint at these scales; disk and
wall time are. 10,000-person studies fit a workstation; 50,000- and
100,000-person studies need parallel machines with per-arm disks (cloud or HPC).
GPU acceleration is not applicable to this sequential per-person event model.
