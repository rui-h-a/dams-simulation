# Submission exclusion and confirmation measures

This retrospective comparison uses 48 completed cases from the short DAMS model. Eight synthetic worlds each use a central, witnessed and consensus record process. In each world and process, the two matched cases keep the same eight hour-equivalent daily censorship-attack budget on model days 15–24. The only changed setting is the imposed censorship exposure, from zero for every process to one for every process. The three processes share the eight world identifiers.

## Recompute the comparison

Run `python3 -I -S -B verify.py` with Python 3.9 or later and compare its output with `results.json`. To verify the source file hashes, case manifests, original summaries and matched settings, pass `--source-dir` with the directory containing the retained stress ensemble. The standalone calculation requires no network access, external packages or simulation.

`world-outcomes.csv` contains the 48 cases. Subtract exposure zero from exposure one within each world and record process. Unfinished counts cover records awaiting initial review or settlement at day 60. The confirmation delay is a mean over confirmed records within each world. Censored claims are excluded before queue entry, and pending appeals are counted separately. Each world has 7,200 original claim opportunities (120 members × 60 days). This denominator includes attempts that were censored. The generated-work comparison uses the same 7,200 member-days.

Exposure one censored about 42 claims per world under each process. All 24 matched comparisons show both a lower unfinished count and a lower mean delay among confirmed records, across the same eight worlds. The comparison describes sensitivity to an imposed exclusion rule and identifies how queue measures can look better even when more claims are suppressed. The assumed exposure is not an estimate of the security of any deployed system.

`provenance.json` pins the retained `stress_summary.csv`, ensemble manifest, case status index and the 48 selected case manifests and summaries. The export verified source fingerprints and equality with the bound case summaries. No individual event files are needed for the paired arithmetic.
