# Sensitivity to individual worlds in the 10,000-member study

This retrospective check uses the four prespecified witnessed-DAMS minus hierarchy outcomes from the ten retained synthetic worlds. It computes the full-roster results and ten nine-world results, each omitting a different world from both arms. It retains the original thresholds and uses the appropriate Student-t critical values for the number of pairs. The Bonferroni adjustment is over the four endpoints of one comparison within each retained roster. The ten overlapping omissions are descriptive sensitivity checks. They leave the confirmation roster and estimand unchanged and provide no new independent repetitions.

With Python 3.10 or later, download the pinned input and run the local verifier:

```sh
curl -fL https://raw.githubusercontent.com/rui-h-a/dams-simulation/2c921307a0aad405b515426c43c8ab156762fcd0/evidence/r11-10000-paired-results/pooled.json -o pooled.json
python3 -I -S -B verify.py pooled.json
```

The verifier requires the exact input digest and the ten assigned world identities. It reports the unadjusted and Bonferroni half-widths divided by the original half-width target, and the Bonferroni interval relative to the original design threshold. E1 is net operating result in resource units per member-year, E2 is regret per million available member-hours, E3 is governance member-hours per member-year, and E4 is verification processing units per member-year. All outcomes cover the seven-year observation window, including the two shared pre-adoption years.
