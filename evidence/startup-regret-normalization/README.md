# Comparing decision regret across startup models

The bundle contains synthetic aggregate inputs for 300 enterprise-model pairs and 66 frozen-model pairs at each of 30 and 120 initial members. It reproduces a retrospective comparison of normalization choices without running either model. Each row records a world identifier and four or two governance arms, with total regret, completed decisions, available member-hours, present-member workdays, and scored proposals. No member records or event histories are distributed.

```sh
python3 -I verify_startup_regret.py
```

The paired contrast is the mean of the treatment-minus-reference differences between arm rates within each world. The 95% Student-t intervals are pointwise and exploratory. The original analyses retain their prespecified endpoints and adjustment rules. Scored proposals include completed and unresolved cases. The total regret charges unresolved cases their absolute synthetic proposal value. Available hours count modeled capacity before absences. The enterprise rates use the retained five-year calendar, including its settlement period; the frozen rates use the five-year common observation window, with an additional follow-up period outside that window. The within-pair identity for hourly regret splits the rate difference into a loss-per-proposal term and a proposals-per-hour term using symmetric midpoints. These are arithmetic components.

The four enterprise arms include retained hierarchy, a process change that retains the hierarchy, witnessed DAMS, and witnessed linear allocation. The frozen model compares the central-record hierarchy and central-record DAMS. World identifiers and model versions are separate across the studies, which also differ in decision schedules, organization and adoption paths. The bridge tests whether the direction survives using the same denominator; it does not attribute the cross-model difference to a single mechanism.

- [Enterprise simulation source](https://github.com/rui-h-a/dams-simulation/tree/2d7446a7aade860decd352839138e83b778d49e3)
- [Frozen longitudinal simulation source](https://github.com/rui-h-a/dams-simulation/tree/30a604e776ecf1c5c0196e6ed44f6b93ac1312b5)
