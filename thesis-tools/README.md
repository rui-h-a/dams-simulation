# Thesis table and figure generators

Copies of the generators the thesis uses, in the thesis repository's layout: inputs under
`04_evaluation/r11/<study>/` (each study's `inference.json` from `./run.sh --spec enterprise-r11`, see
`docs/thesis-reproduction.md`), outputs under `05_thesis/generated/` (LaTeX tables and macros) and
`05_thesis/figures/results/` (vector PDF figures). Run from this directory with the repository environment,
for example `../.venv/bin/python 04_evaluation/r11/make_tables.py` and
`../.venv/bin/python 04_evaluation/figures/thesis_figures.py`.

| Generator | Output |
|---|---|
| `04_evaluation/r11/make_tables.py` | founding, context and acquisition tables |
| `04_evaluation/r11/make_sens_table.py` | behavioral sensitivity table |
| `04_evaluation/r11/make_demand_matched_note.py` | demand-matched note |
| `04_evaluation/r11/collect_resources.py`, `scale_points.py` | resource measurements (receipts read from `DAMS_STUDY_BUCKET`) |
| `04_evaluation/r11/make_main_macros.py` | main-study numbers (from `main-n10000/pooled.json`) |
| `04_evaluation/r11/projections/make_projection_tables.py` | partial-roster and projection tables (inputs from `research_tools/projection`) |
| `04_evaluation/figures/thesis_figures.py` | every result figure of the thesis |

Figures of the earlier reference model read CSV files under `05_thesis/generated/` produced by the historical
commits named in the thesis; those inputs are not regenerated here.
