# Precision and multiplicity in the 10,000-member study

This supplement completes the statistical reporting for the ten fixed synthetic
worlds and four prespecified endpoints. It retains the primary DAMS-versus-
hierarchy comparison, the two supportive comparisons, the original thresholds
and the original marginal precision criterion. It adds achieved half-width
ratios, study-wide Holm adjustment, and plug-in 80% detectable differences.

Download the five files from the
[pinned paired-results bundle](https://github.com/rui-h-a/dams-simulation/tree/2c921307a0aad405b515426c43c8ab156762fcd0/evidence/r11-10000-paired-results)
into a directory. From this supplement's directory run:

    python3 -I -S -B verify_precision.py PATH_TO_PAIRED_RESULTS

The verifier checks the five source hashes, reruns the original verifier, and
prints the supplementary report. It uses only standard Python. No simulation,
original event journal or cloud resource is needed for this calculation.

The test sensitivity conditions on the estimated paired standard deviation and
independent normal paired differences. The detectable differences refer to
two-sided tests of zero, using either a 5% per-test level or the 1.25% level
for the four-endpoint Bonferroni family. The study-wide Holm p-values concern a
separate family of twelve tests. The zero-variance process-change comparison
for E4 keeps its place in that family and has no Student-t estimate of an
interval, p-value, or detectable difference. The observed mean remains zero.

The calculations describe conditional simulation precision. They do not
measure empirical power or test the external validity of the model.
