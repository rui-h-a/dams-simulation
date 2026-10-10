# Reproducing the causal candidate

This isolated candidate is a synthetic research version, not the frozen formal
study or an empirically calibrated enterprise reconstruction. The included
calendar configurations are the exact fixed-world inputs used for the bounded
1,917-day engineering profile (1,827 creating days and a 90-day tail).
They do not declare a new confirmation experiment or statistical stopping rule.

Run the meaningful controls from this repository root with Python 3.14.2:

```sh
python3 -B -m unittest discover -s tests -v
```

The opt-out differential control loads the complete, unmodified schema2 core
under `tests/fixtures/schema2-reference`, from the public commit recorded in
its `provenance.json`. It requires no private directory.

The existing local CLI accepts each complete configuration:

```sh
python3 -B -m dams_sim run --config candidate-evidence/calendar-control.json --output runs/causal-v3-control
python3 -B -m dams_sim run --config candidate-evidence/calendar-dams.json --output runs/causal-v3-dams
```

These commands create new execution identities; do not combine their worlds
with another model version. The CLI currently uses the default journal storage
format. The recorded engineering profile instead used schema2 with 1 MiB
journal chunks and actually restored its day-365 checkpoint. No profile raw
files, private operation records, or third-party full-text literature are
included here. A source packet authenticates code bytes; it does not establish
a completed run, external calibration, or a DAMS advantage.
