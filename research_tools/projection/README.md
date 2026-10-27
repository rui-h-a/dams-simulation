# Partial rosters and projections (protocol Amendment 14)

Run with the repository environment (`.venv/bin/python3`), from the repository root.

1. Partial 300-member rosters (ten-year, no growth, adoption timing, record process). ROOT holds one directory
   per study (`tenyear`, `nogrowth`, `timing`, `process`) with `plan.json` and, per world and arm, `arm.json`,
   `summary.json` and `timeseries.csv`:
   `python research_tools/projection/partial_studies.py ROOT OUT`
2. Horizon check on the complete ten-year worlds (years 1-5 predict the ten-year contrast):
   `python research_tools/projection/horizon_projection.py ROOT/tenyear 1001,1002,1004,1005,1006 OUT/horizon-projection.json`
3. Size check on the first-year scale study (`scale-study.json`, one row per size and variant):
   `python research_tools/projection/scale_projection.py scale-study.json OUT/scale-projection.json`
4. Projections of the main study (MAIN holds `w<id>/n10000-*/<arm>/{summary.json,timeseries.csv}`):
   `python research_tools/projection/project_main.py MAIN OUT/horizon-projection.json OUT/scale-projection.json OUT`

Every output is a JSON file with the per-world values used, so each table entry can be recomputed.
Projected values are not simulated outcomes; their intervals combine the main study's sampling error with
the error of the rule in its check.
