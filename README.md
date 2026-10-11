# DAMS integrated organization/task r7 engineering candidate

This isolated derivative of `r6-joint-1` adds an **optional**, causal organization/task mode: distinct reporting, collaboration and verification networks; prior-day task dependencies; and competing departmental production, quality and learning allocations. Enable it with the typed `organization_tasks` configuration. Omitting that field preserves r6's daily states, ledger rows and results exactly; it adds no state fields or random draws. The enabled version is `longitudinal-enterprise-integrated-7-networks-tasks-1`.

This is a finite engineering candidate. It adds **zero formal study worlds**. Empirical calibration, parameter identification, held-out enterprise validation, a complete long-term experiment and Linux certification remain incomplete. It does not replace the frozen N30/N120/N300 or original longitudinal studies. Time fractions, deterministic role contacts, local objective weights and a simple acyclic dependency chain are declared synthetic assumptions, not estimates of real companies. A separate graph does not by itself establish independent reviewers or protection from collusion.

[Mechanisms, equations, units and limitations](candidate-evidence/ORGANIZATION_TASKS_R7.md), [versioned factor coverage and provenance](candidate-evidence/R7_FACTOR_COVERAGE.json) and [actual engineering validation](candidate-evidence/R7_ENGINEERING_VERIFICATION.json) describe the implementation. `source-manifest.integrated-r7.json` is the active source inventory. Inherited r3/r4/r5/r6 manifests and coverage reports retain their original bytes as historical provenance; their statements do not describe r7's implementation status.

The existing command executes the complete sixty-calendar-day engineering profile, including operations, bounded M&A and the new mechanism, with lossless schema2 journal chunks:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m dams_sim run --config candidate-evidence/calendar-networks-r7.json --journal-chunk-bytes 1048576 --output /absolute/real/r7-evidence
```

For an intentional completed-day interruption, add `--checkpoint-day 23`; continue the retained checkpoint through the same entry:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m dams_sim resume --checkpoint /absolute/real/r7-evidence/<run>/checkpoint.json --journal-chunk-bytes 1048576 --output /absolute/real/r7-resume
```

Both completed and resumed sixty-day raw trajectories were validated and had identical complete state and semantic digest. The checkpointed twenty-three-day run is a recovery control, not an additional completed case. Tests distinguish graph roles, reporting depth from guild count, time tradeoffs, paid verification and explicit deferral, absent/free-riding collaboration, prior-day inventory, closure, lifecycle, acquisition knowledge, source drift and coherent raw/state tampering. The additional accountant reconstructs these paths without invoking producer network/task transitions. Shared parsers, calendar utilities and keyed RNG remain disclosed limitations; internal correctness is distinct from empirical validity.

Run the meaningful controls with an existing real, nonsymlinked temporary directory:

```sh
TMPDIR=/absolute/real/test-tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python3 -B -m unittest test_organization_tasks_r7 test_joint_population_r6 test_enterprise_integrated_r6 test_enterprise_operations_r4 test_enterprise_acquisition test_acquisition_correction test_longitudinal_checkpoint test_enterprise_growth -v
```

The r6 operations and acquisition mechanisms remain intact: destination-aware capacity and resources, paid coordination, owned assets/debt, personnel, source knowledge and unresolved evidence are preserved. M&A still uses a finite immutable target-history capsule, rather than two independently evolving enterprise ABMs. The controlled task departments are separate from installed infrastructure departments, guilds, sites and management depth. Joint entry dependence retains its original optional Gaussian-copula assumptions; [its mechanism and limits](candidate-evidence/JOINT_POPULATION_MECHANISM.md) and the inherited [M&A mechanism](candidate-evidence/MA_MECHANISMS.md) remain relevant. No demographic label directly determines skill, honesty or capability.

This implementation is not admission to the full 10,000-person or 50,000-person formal enterprise study. Short controls and sparse topology measurements do not establish long-term CPU, RAM, I/O, storage, restoration costs, statistical precision or scientific suitability at those scales. Default disabled equivalence preserves results; it does not imply that enabling an uncalibrated mechanism improves validity or favors DAMS.
