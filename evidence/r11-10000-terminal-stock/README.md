# Terminal recognition in the 10,000-member enterprise study

This supplement describes recognition at the end of the published synthetic
enterprise comparison. The roster contains ten paired worlds (1000–1009)
and four arms per world. It uses the [as-run enterprise source](
https://github.com/rui-h-a/dams-simulation/tree/956725516ddac7f7c2336b0ccc23a69c5d7a27c5)
and the same roster as the [published primary comparisons](
https://github.com/rui-h-a/dams-simulation/tree/2c921307a0aad405b515426c43c8ab156762fcd0/evidence/r11-10000-paired-results).
Each run covers two shared pre-adoption years and five post-adoption years.

- **Unfinished records** are the terminal review backlog. The summary count
  equals the count on the final daily row in each of the 40 retained arms.
  The daily series records appeals separately.
- **Confirmed records** are the cumulative confirmed count in the retained
  arm summary. They are a flow accumulated over the study rather than the
  stock on its final day.
- **Confirmation p95** is the per-arm 95th percentile of delay among records
  that reached confirmation, in days. The paired difference in these
  percentiles is averaged across worlds.

`terminal.csv` contains the sanitized counts, matching producer receipt and
summary digests, the full daily-file digest, and the digest of the exact final
daily row including its newline when present. Each daily file has 2,557 ordered
observations (days 0–2,556). Before extraction, the summary and daily-file bytes
were matched to each arm's producer receipt. The shared prehistory hash,
source, assignment and three recorded validation statuses also matched.
`results.json` preserves the paired arithmetic with one difference per world.
The two-sided 95% Student t intervals are exploratory and unadjusted. The
relative change divides the mean paired difference by the mean of its
reference arm. No model steps were executed for this supplement.

From this directory, using standard Python:

    shasum -a 256 -c SHA256SUMS
    python3 -I -S -B verify.py

The verifier checks the extracted roster, digests listed for the public packet
and paired summaries. Rechecking the recorded producer hashes or independently
validating the original event histories requires access to the corresponding
source files and event journals.
