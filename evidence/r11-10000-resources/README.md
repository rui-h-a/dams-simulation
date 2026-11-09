# Resource measurements for the 10,000-member enterprise study

`arm-resources.csv` contains the recorded phase times, worker CPU times, worker peak resident memory and declared final journal sizes for the four arms in each of the ten main-study worlds. The study used [DAMS simulation revision 9567255](https://github.com/rui-h-a/dams-simulation/tree/956725516ddac7f7c2336b0ccc23a69c5d7a27c5). World and arm identifiers match the [paired-outcome evidence bundle](https://github.com/rui-h-a/dams-simulation/tree/2c921307a0aad405b515426c43c8ab156762fcd0/evidence/r11-10000-paired-results).

Run `python3 verify.py` with Python 3.10 or newer to recompute the resource figures from the CSV. The verifier checks the complete world-by-arm roster and the rounded manuscript figures using only the standard library. The observations come from retained arm receipts. The CSV contains measurements only, with recorded values preserved at the precision of those receipts.

| Column | Meaning |
| --- | --- |
| `world`, `arm` | Main-study world and governance arm |
| `sim_wall_seconds` | Recorded simulation-phase wall time in seconds |
| `validation_wall_seconds` | Recorded validation-phase wall time in seconds |
| `recorded_worker_cpu_seconds` | Recorded worker CPU time in seconds |
| `recorded_peak_rss_mib` | Recorded worker peak resident set size in MiB |
| `journal_bytes` | Final original-journal size in bytes, as recorded in the receipt |

For each arm, the simulation and validation wall times define its duration in an idealized schedule. The total duration divided by four is a lower bound for four identical slots. For twenty slots, pairing the shortest duration with the longest and repeating minimizes the largest pair sum. Any faster schedule would have to use two jobs per slot because even the three shortest jobs exceed the reported pair limit. If the longest remaining job is not paired with the shortest, swapping their partners cannot increase the largest pair sum. Applying that exchange repeatedly proves the pairing result.

The timing figures exclude shared prehistory, final output writing, journal hashing, copying and retries. The ideal schedule also assumes that concurrency does not alter throughput. Four times the largest per-arm memory or journal observation gives an allowance for four concurrent workers or journals. Host memory and disk sizing also need the operating system, checkpoints and validation working copies. The receipt size fields describe the original journals. This bundle supports arithmetic from those fields.
