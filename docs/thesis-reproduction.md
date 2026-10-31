# Reproducing the enterprise-model results of the thesis

Each row names the study, the simulation source that produced it, the command and the report the thesis
tables and figures are drawn from. Simulation results depend only on `dams_sim/*.py`, the world identity and the
arguments; `research_tools/source_commits.py HASH` lists every commit whose simulation sources match a recorded
`source_sha256`, and any of them reproduces the study. Commands run from the repository root; each first installs the locked environment (`uv sync --locked`,
Python 3.14.2), then simulates, validates and writes `OUTPUT/inference.json` (raw-evidence paired inference) and
`OUTPUT/effects.pdf`. Add `--output OUTPUT` (and `--jobs N`) to every command.

Seed 20261017 throughout. `W(a,b)` stands for the comma-separated worlds a to b, for example `$(seq -s, a b)`.
Arms: H = control-hierarchy (reference), P = control-process, D = dams-witness, L = linear-witness.

| Thesis item | Study (protocol) | Source | Command arguments after `./run.sh --spec enterprise-r11` |
|---|---|---|---|
| Founding studies, 30/120/300 members (Tables 14–16, Figure 10) | Amendment 1 | R11.3: 467d8b8, 2d7446a (30); source hash 487ab3fd (120, 300) | `--scale 30` (or 120, 300) `--context startup --growth organic --pre-years 0 --post-years 5 --worlds W(1000,1299)` (300 members: `W(1000,1056)`) `--arms H,P,D,L` |
| Mid-life adoption, four contexts (Table 17, Figure 11) | Amendment 2 | R11.3: 93e5b87; traditional source hash 487ab3fd | `--scale 300 --context traditional` (or listed, century, unicorn) `--growth organic --pre-years 2 --post-years 5 --worlds W(1000,1064)` (listed 1000-1119, century 1000-1010, unicorn 1000-1118) `--arms H,D` |
| Acquisition (Table 18, Figure 11) | Amendment 3 | R11.3: 93e5b87 | `--scale 300 --context listed --growth organic+acquisition --pre-years 2 --post-years 5 --worlds W(1000,1119) --arms H,D` with `--variant baseline` (preserve autonomy) or `--variant ma-centralize` |
| Assumption variants (Figure 12) | Amendment 4 | R11.3: 93e5b87 | founding arguments of Amendment 1 with `--variant NAME --arms H,D`; names in `research_tools/r11_design.py` |
| Sickness spells | Amendment 5 | R11.4: e027d28 | founding or traditional arguments with `--variant sickness-spells --arms H,D` |
| Demand matched to capacity | Amendment 6 | R11.4: 981bf50 (founding); source hash 5f0497a8 (traditional) | founding or traditional arguments with `--variant demand-matched --arms H,D` |
| 300-member rosters of Amendment 7 (Table 20): ten-year, no growth, timing, record; all 65 worlds complete | Amendment 7 | 5c7aad5 (source hash 5f0497a8) | `--scale 300 --context traditional --pre-years 2` with `--growth organic --post-years 10 --arms H,D`; `--growth none --post-years 5 --arms H,D`; `--growth organic --post-years 5 --arms H,dams-staged-witness,dams-triggered-witness`; `--growth organic --post-years 5 --arms H,dams-central,dams-consensus`; worlds `W(1000,1064)`; table from `research_tools/projection/partial_studies.py ROOT OUT` |
| Main study, 10,000 members (Figure 14; abstract and Section 6.9 numbers) | Table A, Amendments 8 and 12 | 9567255 | `--scale 10000 --context multinational --growth organic --pre-years 2 --post-years 5 --worlds 1000` (one world per machine, 1000-1009) `--arms H,P,D,L`; pool the saved arm receipts with `research_tools/projection/pool_validated_arms.py ROOT OUT` (Amendment 14 addendum) |
| Projections to ten years, 50,000 and 100,000 members (Table 21, Figure 15) | Amendment 14 | fd34f98 | `research_tools/projection/README.md` |

The 50,000- and 100,000-member comparisons and the 10,000-member supplementary studies of Amendment 13 were
stopped before their horizons (Amendment 14); their commands are in the README and in Amendments 12 and 13.

Machine time measured for complete runs (thesis Figure 25 and Table 31) is in `docs/r11-resource-measurements.md`; a 10,000-member arm needs
about 4 hours for the two years of history (run once per world) and 22 hours for five years after adoption, then
15 hours or more of full validation (main study, n2-standard-8 machines, four arms sharing four physical cores).

Table and figure numbers refer to the thesis PDF of 1 November 2026. Tables and figures are rebuilt from the study
outputs with the generators in `thesis-tools/` (see its README): `make_tables.py` (Tables 14–18),
`make_sens_table.py` (Table 19), `projections/make_projection_tables.py` (Tables 20–21), `make_main_macros.py`
(main-study numbers) and `figures/thesis_figures.py` (Figures 10–23 and 25–27; Figure 19 also needs the daily series of the reference-model runs).
