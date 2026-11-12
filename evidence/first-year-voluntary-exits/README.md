# First-year voluntary exits

Run `python3 -I -S -B verify.py` from this directory to reproduce the thesis percentages. The Python standard library suffices. The calculation reads frozen aggregate counts and runs offline.

- The 300-member founding study starts with an initial roster and follows 57 simulated worlds with four arms each. Its observation window is 2020–2024.
- The 10,000-member main study follows ten simulated worlds with four arms each from 2020–2026. Its shared prehistory ends at the 1 January 2022 branch.
- An early quit is an exit recorded as `voluntary_exit` fewer than 365 calendar days after roster entry. The share of quits divides early quits by all recorded voluntary exits and includes quits from the initial roster.
- The recruit incidence divides early quits among eligible recruits by all eligible recruits, including recruits with another exit cause. Eligibility requires at least 365 days between entry and the study end. The founding cohort excludes the day-zero roster. The main-study cohort includes entrants on or after the branch date.
- Each percentage pools counts across worlds within one arm. The ranges give the descriptive minimum and maximum over the four simulated arms.

`arm-counts.csv` contains only arm-level counts and hashes of the state and execution receipt. The whole-study voluntary exit counts reconcile with the separately released [monthly quit-flow evidence](../voluntary-quit-observation). For the main study these whole-study counts include the shared prehistory in each arm and are never pooled across arms.

Readers who have the original state archives can also run `python3 -B recount.py --founding STUDY_DIRECTORY --main ARCHIVE_DIRECTORY` with either or both source directories. That command checks each raw SHA-256, the arm identity and roster exposure, then re-counts the recorded events and eligible recruits. It reads retained outputs without executing the simulation. The frozen aggregate inputs suffice to reproduce the displayed percentages.
