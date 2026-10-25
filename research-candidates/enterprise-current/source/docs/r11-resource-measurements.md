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

## Complete runs at 10,000 and 50,000 members (complete-retention pilots, 2026-10-22 to 10-25)

Source 3b4d446 (membership-set correction included), multinational / organic, 2 + 5 years, c4d-highcpu-16 Spot (16 vCPU, 30 GB), one process per prehistory or arm. Rates come from receipts (histories) and from modification times of the yearly checkpoints (arms; they include checkpoint writing).

| Phase | 10,000 members | 50,000 members |
|---|---|---|
| Two-year history | 8.4 s per simulated day (3 worlds in parallel) | 20.0 s per day (2 in parallel) |
| Arm, year 1 after adoption (incl. restore) | 27 s per day | -- |
| Arm, years 2-5 | 22, 28, 33, 36 s per day (12 arms on 16 vCPU) | 149 s per day over years 1-2 (4 arms) |
| Arm total, five years | about 15 h | at least 75 h (extrapolated, not observed) |
| Full validation per arm (fixed validator a4e1fe3, 12 in parallel) | snapshot 1.9-2.3 h, daily evidence 9.4-11.3 h, organization accounts 0.4-0.6 h, summary < 1 min | not run |
| Branch checkpoint (history end) | 2.0 GB | -- |
| Yearly arm checkpoint | 6-7 GB | -- |
| Final state per arm | 7.25 GB SQLite (complete journal, chunked and already compressed: zstd -19 saves about 12 %) + 60 MB JSON | -- |
| Disk per arm with every yearly checkpoint and interruption copy | about 44 GB | 455.7 GB for 4 arms at day 1,461 |

The per-day cost of an arm rises with simulated time because the review backlog grows under the default capacity (thesis Appendix C, Proposition 1). The validator's former occupied-slot check was quadratic in population per day; with it the 12 pilot arms stayed in validation for more than 22 hours without finishing. At 300 and 600 members the corrected validator scales with the number of journal rows (300 -> 600: snapshot 36 -> 67 s, daily evidence 23 -> 47 s).

Storage plan for the main study (10 worlds x 4 arms, Amendment 10): retained about 40 x 7.3 GB + 10 x 2.0 GB = 312 GB, plus about 14 GB of working space per running arm.
