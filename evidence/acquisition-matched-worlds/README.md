# Paired acquisition comparisons

The retained synthetic study models a 300-member listed acquirer. The data contain 120 matched worlds, each with four arm aggregates for two policies and two integration modes. Each world has the same seed and target assignment across both modes. Eight outcomes are supplied in the order declared in the JSON. The source commit fixes the original outcome definitions.

With Python 3.10 or later, run:

```sh
python3 verify_ma.py
```

The checker calculates the original within-mode differences as witnessed DAMS minus the retained hierarchy. It then subtracts the preserved-autonomy difference from the centralized-integration difference, using the same world in both modes. It also reports the centralized-minus-autonomous difference for each policy separately. The 95% Student-t intervals are paired and unadjusted. The mode comparison is retrospective and describes Monte Carlo variation within the synthetic design. Coordination, learning and memory retention, and the scope of policy and record processing vary together across the integration packages.

The aggregate file contains world identifiers and arm-level outcomes. The extraction checked the retained arm receipts, summary and time-series hashes, and matched pre-adoption rows and target inputs. The original event ledgers are separate from this release. The checker reproduces the displayed arithmetic without running the model.
