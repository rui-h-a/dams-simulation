# Verify retained thesis results

Download [verify.py](verify.py) from this directory into an empty folder. With
Python 3.11 or later, open a terminal in that folder and run the following.

```sh
python3 -I -S -B verify.py
```

If you already have a repository checkout, the launcher is at
`evidence/thesis-verification/verify.py`.

The command downloads small public evidence files from fixed Git commits and
checks every byte count and SHA-256 before executing the verifiers. It compares
each report with its published values, allowing for floating-point rounding.
Python's standard library is sufficient. No package installation, cloud
account or simulation is required. The command exits successfully after
printing `Passed all nine retained-result checks.`

The first run needs access to `raw.githubusercontent.com` and a writable cache
and temporary directory. The cache defaults to `~/.cache/dams-thesis-verification`.
Select another directory with `--cache DIR`. After the first successful run,
reuse that cache without a network connection.

```sh
python3 -I -S -B verify.py --offline
```

An incomplete or altered offline cache causes a nonzero exit. A bad cache
entry is reported by name. Remove that entry or select a fresh cache directory,
then run online to obtain the pinned copy. The command uses temporary verified
copies for execution and leaves the checkout untouched.

| Check | Evidence recomputed |
| --- | --- |
| Main study | 75 paired outcome summaries and adjusted intervals for 12 primary comparisons across 10 worlds |
| Main-study precision | Half-width ratios, multiplicity and conditional detectable differences for 12 comparisons |
| Resource scheduling | Ideal schedules for 5, 6, 8 and 10 hosts from 40 arms and 10 prehistories |
| Record procedure | Four contrasts across 65 matched worlds |
| Startup size | Six size baselines and ten adjacent-size contrasts |
| Acquisition | Comparisons across 120 matched worlds and 480 arm aggregates |
| Startup regret | Comparisons with a common denominator at 30 and 120 initial members |
| Partial 50,000-member pilot | Elapsed checkpoint times for four arms and size of the recorded file inventory |
| Main-study terminal stock | Recorded terminal counts and 12 paired estimates from 40 arms |

These checks reproduce arithmetic from retained synthetic data. Revalidating
original events requires the original event journals. Generating new worlds
uses the separate [versioned research launcher and protocol](https://github.com/rui-h-a/dams-simulation/blob/bffe007794a022a2efb9dc9ce9d2c5c69d2e7c6a/docs/thesis-reproduction.md).
