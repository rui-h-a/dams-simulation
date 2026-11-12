# Recomputing three sets of DAMS results

Run this command from the repository root with Python 3.11 or newer.

```sh
python3 -I -S -B evidence/thesis-extended/verify.py
```

The command checks file hashes and runs the four verification scripts offline using the standard library.

- [Startup scale](startup-marginal-extension/) covers the six tested first-year sizes and five synthetic worlds per size. It recomputes the original marginal estimates, the sensitivity to omitting each size and the contributions of operating rates and exposure to whole-firm gaps.
- [Main-study backlog](backlog-history/) covers 2,557 days for each of four arms in ten worlds. It recomputes the shared stock, the first- and third-year differences, the decline after work creation stops and the terminal contrasts.
- [Record procedures](record-procedure-three-arm/) compares central, witnessed and consensus procedures in 65 paired traditional-firm worlds. The package records the matching and release checks for the three arms.

The directories give the source bindings, units and interval definitions. These checks use retained synthetic summary fields and daily aggregate columns. Raw-event validation requires the original journals.

The [five-year post-adoption endpoints](https://github.com/rui-h-a/dams-simulation/tree/fc83b14aa3f1cf29fd9c71017825c1ea6ab87503/evidence/r11-10000-post-adoption) have their own arm-total verifier.
