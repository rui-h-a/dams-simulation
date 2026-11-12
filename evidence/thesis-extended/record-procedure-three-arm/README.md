# Paired comparison of three modeled DAMS record procedures

The comparison uses 65 synthetic traditional-firm worlds with 300 initial members,
two years of shared history and five years after adoption. All three arms adopted
on day 731. The endpoints cover the full 2,557-day trajectory. Each world applies
the same contribution-based policy and varies the record-procedure selector.
The comparison uses settings without attacks or scheduled quorum outages.

The witnessed arm comes from an earlier engine source. The central and consensus
arms come from the record-procedure experiment. The retained-source audit checked
byte equality of the entire hierarchy control series across the two sources in all
65 worlds, and of the pre-adoption rows across the three DAMS arms. The CSV includes
the matching SHA-256 labels and original summary identifiers. The manifest binds
the two plans and engine sources. The original summaries, daily series, and event
journals are outside this package.

## Recompute the paired estimates

Run with Python 3.11 or newer from this directory.

```sh
python3 -I -S -B verify.py
```

The verifier checks the packaged bytes, world and arm roster, and provenance-label
consistency. It recomputes the four endpoints and all twelve contrasts from the CSV.
Verification covers the supplied summary fields. It requires neither network access
nor a simulation run. Validation of original records requires the retained source data.

Net operating result divides operating revenue less operating resource use by
active member-calendar days over 365.25. Mean confirmation delay is defined over
records confirmed within each arm by the end of follow-up. The confirmed and
unfinished counts show how the populations underlying those means differ. Positive
contrasts indicate larger values in the first named arm. The two-sided paired
Student t intervals control the nominal familywise error rate at 5% by applying
Bonferroni adjustment across three pairs and four endpoints, with 64 degrees of
freedom. These exploratory estimates describe model behavior for the retained worlds.

The [two-arm evidence package](https://github.com/rui-h-a/dams-simulation/tree/3ce886a20d9a1f088814909922adffae08d6c5b8/evidence/record-procedure)
reports the central–consensus comparison with unadjusted intervals.
The [study protocol](https://github.com/rui-h-a/dams-simulation/blob/5c7aad5fc69043217d51f2ec9c657e14a7a6d318/docs/r11-formal-protocol.md#L256-L287)
specifies that experiment.
