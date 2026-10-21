# Observed ACS population-structure diagnostic

This is a data-measurement diagnostic from the official 2023/2024 Rhode Island
one-year ACS person and housing files. It is not a DAMS simulation, behavioral
calibration, holdout validation or a firm sampling frame. Both years are seen.

`frozen-plan.json` records the specification fixed before acquisition.
`manifest.json` preserves the exact official URLs, original ZIP hashes and
schema/EOF/CRC observations. `summary-small.json` is an index;
`summary.json.gz` contains every diagnostic cell without loss.
`summary-compression.json` identifies both compressed and uncompressed hashes.
The raw ZIPs can be obtained directly from the pinned official URLs using
[the public tool](../../research_tools/acs_population_diagnostic.py);
[the instructions](../../docs/acs-population-diagnostic.md) specify the bounds,
universes, weights and measurement limitations. Use a fresh directory and
retain failed/partial evidence.

The preselected joint cell is age 25–34, SCHL=21 (bachelor degree), ESR=1
(civilian at work) and COW=1 (private for-profit employee). Its joint denominator
is all person records; the conditional denominator is the age/education group.
These are descriptive population distributions, not a DAMS effect or a general
employment rate. Independent raw-CSV arithmetic checked counts, person weights
and all eighty replicate numerators and denominators.
