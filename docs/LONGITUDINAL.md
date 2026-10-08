# Calendar-linked adoption specification

The schema-3 specifications are executable through the shared entry:

```sh
./run.sh --spec longitudinal-adoption-5y --scale 1000
./run.sh --spec longitudinal-adoption-10y --scale 10000
```

These are full scientific runs and may exceed available resources. `--prepare-only` prepares the locked environment, validates the requested scientific specification and population, and runs the environment doctor without simulating. Availability of the command is not a completed experiment or a certified hardware requirement.

`dams_sim/longitudinal_design.py` declares the incremental arm roster, contexts, paired contrasts, calendar, independent pilot and confirmation worlds, units and precision rules. The principal follow-up is five post-adoption Gregorian years; the second specification extends selected comparisons to ten. Organization age is an initial-condition label, distinct from generated prehistory and observed time. The common strategy end permits fixed adoption through year five, followed by the declared post-adoption horizon; a common 90-day tail records settlement without creating new work or decisions. Thirty-day and sixty-day validation are separate short specifications.

The four primary endpoints are work per recorded present-member workday, total recorded decision regret per completed decision, net synthetic resource gain per present-member workday, and closure at the common end. Unresolved-decision loss stays in total regret. People and days share a world and are not independent replications. A disjoint pilot fixes the confirmation roster before confirmation runs. A failed precision target, missing adoption, closure, zero exposure or resource stop is retained rather than removed to improve the estimate.

The disk-backed implementation retains people, skills, memory, membership, procedures, queues, finances and event history. A policy branch inherits complete state; it cannot replace an established organization with fresh agents. The state-hash codec, JSON descriptor and SQLite byte hashes are separate checks. Source changes require a new output/version; an old-source checkpoint may be inspected with explicit historical identity but cannot be resumed as current-source evidence.

The complete gate rebuilds the prescribed inventory, Gregorian daily records, event accounting, observation windows, paired endpoints and intervals. Publication version 2 additionally rebuilds founding/entrant cohort risk sets, earliest competing outcomes, all departure memory losses, common-calendar differences and actual-adoption alignment from verified raw state/events. Means use `math.fsum/n`; empirical distribution percentiles use linear order-statistic interpolation at `(n-1)q`. Relative alignment retains every assigned world: a missing anchor or observed day makes the all-assigned estimate undefined. Exported per-world anchors include actual Gregorian dates and organization age; the matched control uses the treatment's observed anchor.

```sh
uv run --locked --extra analysis python research_tools/validate_longitudinal.py --output runs/longitudinal-adoption-5y-n1000 --spec longitudinal-adoption-5y --scale 1000
```

The shared `validate_pipeline_output` dispatches schema 3 to the same independent gate and returns the exact child-origin roster for cloud acceptance. The validator constructs no Model. Its event/coordinate checks do not independently replay the random generator, certify input truth, or interpret the meaning of replacement SVG/PDF/TeX content. Figure and text meaning require source inspection and actual rendered review in addition to their byte binding.

Publication uses validated raw observations without rerunning worlds. It saves vector PDF/SVG, CSV coordinates, explicit definitions and limitations, LaTeX fragments and a complete generation manifest. It refuses symlinked or unrecorded destinations, loaded-source drift and concurrent changes to an existing publication. A failed final rename attempts to restore the previous publication and retains the new candidate plus a failure record. This is a verified rollback path, not a claim of a cross-filesystem transaction.

A bounded single-world completion or checkpoint-recovery test contributes no formal Monte Carlo worlds. Current-source long-run cloud acceptance, confirmation precision, all planned scales and complete cleanup require their own actual evidence. The historical published short study retains its original source and scope.
