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
each report with its published values or with the digest of its rounded output.
Python's standard library is sufficient. No package installation, cloud
account or simulation is required. The command exits successfully after
printing `Passed all 38 retained-result checks.`

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
| Record procedure costs | Paired operating, verification processing and review measures from 195 arm summaries, with a conditional break-even calculation |
| Startup size | Six size baselines and ten adjacent-size contrasts |
| Acquisition | Comparisons across 120 matched worlds and 480 arm aggregates |
| Startup regret | Comparisons with a common denominator at 30 and 120 initial members |
| Partial 50,000-member pilot | Elapsed checkpoint times for four arms and size of the recorded file inventory |
| Main-study terminal stock | Recorded terminal counts and 12 paired estimates from 40 arms |
| Five years after adoption | Four outcomes across 40 arm totals, with adjustment over 12 comparisons |
| Outstanding record-days | Daily backlog sums and paired record-day estimates for 40 arms |
| Outstanding records by year | Annual and year-end stocks and the final 60-day gap from the pinned daily series across 40 arms |
| Recognition flows and stages (two checks) | Submissions, confirmations, first-review rejections, appeal returns and terminal stocks |
| Operating break-even (two checks) | Five observed model years and five projected years, with exposure and discount-rate sensitivity |
| Lower demand (two checks) | Retained traditional-firm pairs and joint operating and decision contrasts |
| Voluntary exits (two checks) | Monthly public and modeled flows, and first-year modeled exit counts |
| Record intake | Paired censoring and intake outcomes from retained aggregate cases |
| Founding closure | Public employer-birth and survival cohort arithmetic |
| Extended evidence (three checks) | Startup size decomposition, daily backlog history and three record procedures |
| Acquisition periods | Operating contrasts before and after integration from 480 retained arms |
| Main-study world influence | Leave-one-world-out checks of the four primary outcomes |
| Short-model confirmation volume | Delay curves, cumulative confirmations and unfinished counts from 96 arms |
| Short-model formal authority | Ranked shares and paired lower-half share contrasts in 32 worlds |
| Short-model turnout | Attendance and unexercised formal weight from 9,600 daily observations |
| Short-model work | Paired work differences, Monte Carlo intervals and the prespecified primary criterion |
| Short-model quorum outage | Initial-review and settlement stocks in 24 retained cases |
| Formal 50,000-member comparison | Ten retained prehistories and completion status for 30 assigned arms |
| Exploratory decision execution | Paired operating and decision contrasts and changes from the zero-effect setting in ten worlds |
| Traditional-firm adoption timing | Fixed and backlog-triggered timing agreement and paired staged-adoption contrasts across 65 worlds |
| Traditional-firm confirmation stocks | Confirmed records, delay and terminal stocks from 130 retained arm summaries |
| Traditional-firm adoption phases | Paired operating results and outstanding record-days before and after the second guild adopts |
| 100,000-member scale check | Three assigned worlds, two completed prehistories and the count of complete five-year pairs |
| Short-model workplace disruption | Three output recovery cutoffs and cumulative deficits in 40 retained pairs |

The checks recompute published estimates from retained aggregates and daily series. The exit and founding-closure comparisons also use public statistical data. The 50,000-member checkpoint check covers a partial pilot. The separate formal comparison supplies prehistory resource measurements and zero complete policy worlds. Revalidating original events requires the event journals. Generating new worlds
uses the separate [versioned research launcher and protocol](https://github.com/rui-h-a/dams-simulation/blob/bffe007794a022a2efb9dc9ce9d2c5c69d2e7c6a/docs/thesis-reproduction.md).
