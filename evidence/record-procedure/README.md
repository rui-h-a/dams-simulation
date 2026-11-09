# Paired comparison of two modeled DAMS record procedures

This package recomputes four paired differences between the consensus and
central record settings of the same contribution-based DAMS policy. It uses
65 synthetic traditional-firm worlds with 300 initial members, two years of
shared history and five years after adoption. Both arms adopted on day 731.
They share the same configurations apart from the record-setting selector.
The comparison covers the entire 2,557-day trajectory in each world.

The consensus setting raises the initial-review multiplier from 1 to 1.5
and the settlement requirement from one to three model days. Neither arm
includes an attack or a scheduled quorum outage. This comparison estimates
the consequences of those model settings together. It supplies no estimate of
the security, cost or performance of an operating blockchain.

## Recompute the paired results

Run from this directory with standard Python 3.

    python3 -I -S -B verify.py

The CSV contains the retained source fields needed for the four calculations.
The manifest identifies the fixed study and records the CSV checksum. Each
CSV row also includes the SHA-256 of its original arm summary. The original
daily series, source summaries and event journals are outside this package.
The checker verifies the supplied field arithmetic and roster. It does not
independently verify the original journal or regenerate a world.

Each reported difference is consensus minus central in the same world.
Net operating result is operating revenue minus operating resource use,
divided by active member-calendar days over 365.25. Each confirmation-delay
mean includes only records confirmed within its own arm by the end of the
trajectory. Confirmed and unfinished record counts preserve that distinction.
Intervals are two-sided paired Student t intervals at 95%, descriptive and
without multiplicity adjustment.

The fixed experiment is described in [Amendment 7b](https://github.com/rui-h-a/dams-simulation/blob/5c7aad5fc69043217d51f2ec9c657e14a7a6d318/docs/r11-formal-protocol.md#L256-L287).
The [study driver](https://github.com/rui-h-a/dams-simulation/blob/5c7aad5fc69043217d51f2ec9c657e14a7a6d318/research_tools/r11_design.py#L403-L414)
assigns the model setting and adoption day. The
[review rules](https://github.com/rui-h-a/dams-simulation/blob/5c7aad5fc69043217d51f2ec9c657e14a7a6d318/dams_sim/longitudinal_model.py#L381-L426)
select the corresponding capacity and settlement parameters.
