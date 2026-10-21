# Complete-history two-firm counterfactual candidate

This branch adds a separately versioned engineering candidate. It does not
replace the frozen R11 studies, reconstruct their discarded journals or supply
new formal samples. The R11 `enterprise-r11` command still uses its documented
single-company recipe and reduced acquisition mechanism.

## Intervention and evidence

Both firms evolve as distinct operating engines. At the original completed
offer boundary, before any offer or ownership transfer, the same complete pair
can branch into `no-acquisition`, `preserve-autonomy` and `centralize`. The
target continues under independent ownership in the first branch. Acquisition
changes ownership of the complete target engine; it does not replace its
people with a selected employee capsule.

```python
from dams_sim.joint_firm import JointRun
from dams_sim.joint_counterfactual import CounterfactualPlan

plan = CounterfactualPlan.from_checkpoint(common_checkpoint)
branch = JointRun.fork_counterfactual(
    common_checkpoint, fresh_directory, plan=plan, arm="centralize"
)
# Keep the externally fixed plan and arm with the complete checkpoint group.
recovered = JointRun.restore(
    branch_checkpoint, another_fresh_directory,
    expected_plan=plan, expected_arm="centralize"
)
```

The immutable plan binds the original terms, source, two configurations, states,
common records and complete original file group. New intervention checkpoints
have a distinct schema. Restore requires the separately fixed expected plan
and arm; the checkpoint cannot authorize a new intervention by itself. Ordinary
and intervention entry points share the same cross-firm calendar and identity
admission. Incompatible work weeks are refused before model restoration.

Centralization of an unadopted target uses its complete state fork and the
ordinary funded adoption path. Migration payments, training and dual-running
costs apply. Requested conversion time is separate from actual per-guild
adoption dates. Insufficient cash cannot activate a free policy override.
Original journal prefixes and parent provenance remain available for checking.
`configuration_history(run, company)` supplies the original configuration
segment to the daily raw validator.

`ownership_scoped_totals` reports additive flows separately for the buyer's
corporate group and the fixed two-firm system. Target flows before ownership
are excluded from the former. The complete prefix is checked even when
reporting a later interval; negative, invented initial or discontinuous
cumulative counters are refused. Seller consideration, intercompany treasury
transfers and provider expenditure retain different meanings. Ratios and
concentration measures are not summed.

## Executed engineering checks

The standard-library tests are in `tests/test_joint_counterfactual_pure_r12.py`
and `tests/test_joint_counterfactual_r12.py`. Pure checks need no model run:

```sh
python -B -m unittest discover -s tests -p test_joint_counterfactual_pure_r12.py -v
```

The real-model file uses eight initial people in each firm and eight calendar
days. It requires `DAMS_R12_TEST_TMP` to name a fresh, owned temporary directory
on suitable storage. The executed checks used a supervisor with a cumulative
90-second allowance, a 90-MiB early storage boundary, a 100-MiB hard boundary
and a 2-GiB free-space floor. The test command was:

```sh
python -B -Werror::ResourceWarning -m unittest discover -s tests -p test_joint_counterfactual_r12.py -v
```

Checks covered complete joint/person-context recovery, common histories,
pending obligations, three branches, paid conversion, final per-company raw
daily/summary checks and recovery, immutable terms/arm admission, completion
event labels and incompatible native calendars. Initial-counter and calendar
defects were reproduced before correction. An independent review checked the
fixed source/raw bytes and the counterexamples. Separate scalar arithmetic
over original compressed journals checked daily cash, intercompany cancellation,
seller receipts and ownership-scoped revenue; it is not an independent
implementation of behavior or production.

The retained engineering evidence distinguishes a day-6 joint checkpoint from
day-8 company final checkpoints. No day-8 joint terminal is claimed. These are
engineering cases, not calibration observations, precision pilots or evidence
of an organizational policy effect. A clean full-workload platform reproduction
has not been established for this candidate.

## Remaining scientific integration

Different-policy re-adoption of an already adopted target, a separately defined
zero-cost transition ablation, heterogeneous local integration calendars and
cross-firm task/knowledge links remain unfinished. They are not silently
approximated by the candidate. The formal driver, complete execution manifest,
external validation, pilot precision and full preservation/resource admission
must be integrated before this candidate can support a new scientific study.
