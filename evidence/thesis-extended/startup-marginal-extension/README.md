# Startup scale sensitivity and first-year operating totals

Run both checks from this directory with Python 3.10 or newer.

```sh
python3 -I -S -B verify.py
python3 -I -S -B verify_extensions.py
```

The first command reproduces the original adjacent-size comparisons. The second
recomputes two retrospective analyses from the same 60 arm summaries and compares
them with the retained results at eight decimal places. The script works offline
with the Python standard library. The data cover six initial sizes and five synthetic
worlds per size. Each world contributes first-year outcomes for witnessed DAMS and
the retained hierarchy.

## Size sensitivity

At each size, subtract the hierarchy rate from the witnessed DAMS rate within each
world. The weight is the inverse variance of that size's mean gap, estimated as five
divided by the sample variance of its five paired differences. Fit a slope against
the base-10 logarithm of initial membership in each world. Repeat after omitting
each size in turn and retain the weights at the remaining sizes. The mean slope
uses five world-specific estimates. The paired Student t intervals are pointwise,
with four degrees of freedom.

## First-year operating totals

For each arm, member-years equal active member-calendar days divided by 365.25.
Net operating total equals revenue less operating resource use. The total gap equals
the rate gap times the two arms' mean member-years plus the member-year gap times
their mean rate. Between adjacent sizes, split the change in the first product
using the midpoint identity. The three terms capture the changing rate gap, the
change in mean member-years and the change in the between-arm exposure term.
Each step divides the components by the difference in initial membership.
The paired 95% intervals apply Bonferroni adjustment across five steps and three
components. This decomposition describes the model's operating accounts.

The supplied CSV, provenance and original verifier are byte-for-byte copies of the
[published startup evidence](https://github.com/rui-h-a/dams-simulation/tree/3ce886a20d9a1f088814909922adffae08d6c5b8/evidence/startup-marginal).
The extension uses published summary fields. Validation of original event journals
and transfer to larger or different organizations require separate evidence.
