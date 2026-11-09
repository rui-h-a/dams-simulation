"""Compute supplementary reporting from the pinned ten-world paired results."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys


PUBLIC_COMMIT = "2c921307a0aad405b515426c43c8ab156762fcd0"
PROTOCOL_COMMIT = "956725516ddac7f7c2336b0ccc23a69c5d7a27c5"
PROTOCOL_SHA256 = "1487eb9ab3aa64a6437db25710d72a92b3e082a5be25c9da4a8a18ca9fbefc67"
FILES = {
    "pooled.json": "9507497f0e295c93fcbf31ed6d29a9a02cb2532dbd10ab63d07973420de1eecc",
    "roster.json": "ee1c8bc5378089291b9ffde35a97c860ad5ddc500714355bd5e8ee037ed377c9",
    "figure.json": "60a4971aaaec50eac87582438c3e34617447114a4c6b080afba269db9bb5df01",
    "SHA256SUMS": "f86119af3b19194193b6702cb96fc7ff59548859b64b0c8b5cd4cee033ed8dfb",
    "verify.py": "24bfe241654ba295ea04d18786d649e2d61eee33de1c731d6667faf8d1f40e25",
}
ENDPOINTS = {
    "net_operating_result_per_member_year": ("E1", 1, "resource units per member-year"),
    "decision_regret_per_available_member_hour": (
        "E2", 1e6, "regret units per million modeled available member-hours"
    ),
    "governance_member_hours_per_member_year": (
        "E3", 1, "modeled member-hours per member-year"
    ),
    "witness_resource_units_per_member_year": (
        "E4", 1, "verification processing units per member-year"
    ),
}
ARMS = ("dams-witness", "control-process", "linear-witness")
WORLDS = list(range(1000, 1010))
T_MARGINAL = 2.262157162798205
T_BONFERRONI = 3.110934823179468


def require(condition, description):
    if not condition:
        raise ValueError(description)


def close(actual, expected):
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-14)


def holm(probabilities):
    # Preserve the slot of an undefined test, and retain null in the output.
    ordered = sorted(
        enumerate(probabilities),
        key=lambda item: (1 if item[1] is None else item[1], item[0]),
    )
    adjusted = [None] * len(ordered)
    previous = 0.0
    for rank, (index, p_value) in enumerate(ordered):
        if p_value is not None:
            previous = min(1.0, max(previous, (len(ordered) - rank) * p_value))
            adjusted[index] = previous
    return adjusted


def quadrature(upper, count):
    step = upper / count
    return [
        (i * step, (1 if i in (0, count) else (4 if i % 2 else 2)) * step / 3)
        for i in range(count + 1)
    ]


def scale_density(value, df):
    # Density of sqrt(V / df) for V ~ chi-square(df).
    coefficient = 2 * (df / 2) ** (df / 2) / math.gamma(df / 2)
    return coefficient * value ** (df - 1) * math.exp(-df * value**2 / 2)


def normal_sf(value):
    return math.erfc(value / math.sqrt(2)) / 2


def null_two_sided_p(statistic, df):
    # For large statistics, scale the integration axis so the integrand does
    # not become concentrated in a tiny neighborhood of zero.
    rescale = max(1.0, statistic)
    return 2 / rescale * math.fsum(
        weight * scale_density(value / rescale, df)
        * normal_sf(statistic * value / rescale)
        for value, weight in quadrature(12 if statistic > 1 else 6, 8192)
    )


def noncentrality_for_power(critical, df, count):
    weights = [
        (critical * value, weight * scale_density(value, df))
        for value, weight in quadrature(6, count)
    ]

    def power(noncentrality):
        return math.fsum(
            weight * (normal_sf(value - noncentrality) + normal_sf(value + noncentrality))
            for value, weight in weights
        )

    require(abs(power(0) - null_two_sided_p(critical, df)) < 1e-10, "power at the null")
    low, high = 0.0, 12.0
    require(power(low) < 0.8 < power(high), "80% power must be bracketed")
    for _ in range(50):
        mid = (low + high) / 2
        if power(mid) < 0.8:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def precision_record(values, source, figure, endpoint, noncentralities):
    code, scale, unit = ENDPOINTS[endpoint]
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    n = len(values)
    require(close(mean, source["mean_difference"]), f"mean mismatch for {code}")
    require(close(sd, source["sd_difference"]), f"SD mismatch for {code}")
    require(source["delta"] > 0 and source["delta"] == figure["delta"], f"threshold mismatch for {code}")
    require(source["role"] == "primary", f"wrong primary family for {code}")
    row = {
        "endpoint": endpoint,
        "code": code,
        "display_scale": scale,
        "unit": unit,
        "world_count": n,
        "mean_difference": mean * scale,
        "sample_sd_paired_differences": sd * scale,
        "design_threshold": source["delta"] * scale,
        "design_half_width_target": source["delta"] * scale / 2,
        "prespecified_marginal_precision_met": source["half_width_within_delta_over_2"],
        "p_unadjusted_two_sided": source["p_two_sided"],
    }
    for key, critical, ncp in zip(
        ("marginal", "bonferroni"), (T_MARGINAL, T_BONFERRONI), noncentralities
    ):
        bounds = source["ci95"] if key == "marginal" else figure.get("ci_bonferroni")
        if sd == 0:
            require(all(value == 0 for value in values), "unexpected constant nonzero contrast")
            require(bounds is None and source["p_two_sided"] is None, "degenerate inference mismatch")
            row[key] = {
                "interval": None,
                "half_width": None,
                "half_width_ratio_to_design_target": None,
                "minimum_detectable_difference_80pct": None,
                "interval_relation_to_threshold": "undefined for degenerate differences",
            }
            continue
        se = sd / math.sqrt(n)
        width = critical * se
        lower, upper = mean - width, mean + width
        require(close(lower, bounds[0]) and close(upper, bounds[1]), f"{key} interval mismatch for {code}")
        delta = source["delta"]
        if upper < -delta:
            relation = "wholly below the negative threshold"
        elif lower > delta:
            relation = "wholly above the positive threshold"
        elif lower >= -delta and upper <= delta:
            relation = "wholly within the design thresholds"
        else:
            relation = "crosses at least one design threshold"
        row[key] = {
            "interval": [lower * scale, upper * scale],
            "half_width": width * scale,
            "half_width_ratio_to_design_target": width / (delta / 2),
            "minimum_detectable_difference_80pct": ncp * se * scale,
            "interval_relation_to_threshold": relation,
        }
    if sd == 0:
        require(not source["half_width_within_delta_over_2"], "degenerate precision must stay false")
        row["status"] = "all ten paired differences exactly zero; Student-t inference undefined"
    else:
        p = null_two_sided_p(abs(mean) / (sd / math.sqrt(n)), n - 1)
        require(math.isclose(p, source["p_two_sided"], rel_tol=1e-8), f"p-value mismatch for {code}")
        require(
            (row["marginal"]["half_width_ratio_to_design_target"] <= 1)
            == source["half_width_within_delta_over_2"],
            f"prespecified precision mismatch for {code}",
        )
        row["status"] = "defined"
    return row


def report(bundle):
    for filename, expected in FILES.items():
        require(hashlib.sha256((bundle / filename).read_bytes()).hexdigest() == expected, filename)
    verified = subprocess.run(
        [sys.executable, "-I", "-S", str(bundle / "verify.py")],
        check=True, capture_output=True, text=True,
    )
    pooled = json.loads((bundle / "pooled.json").read_bytes())
    roster = json.loads((bundle / "roster.json").read_bytes())
    figure = json.loads((bundle / "figure.json").read_bytes())
    require(roster["assigned"] == roster["finished"] == pooled["worlds"] == WORLDS, "ten-world roster")
    require(not roster["missing"] and pooled["reference"] == figure["reference"] == "control-hierarchy", "reference or missing")
    require(figure["K"] == len(ENDPOINTS) and set(pooled["comparisons"]) == set(ARMS), "family or arms")
    df = len(WORLDS) - 1
    require(abs(null_two_sided_p(T_MARGINAL, df) - 0.05) < 1e-10, "marginal t critical")
    require(abs(null_two_sided_p(T_BONFERRONI, df) - 0.05 / len(ENDPOINTS)) < 1e-10, "Bonferroni t critical")
    ncps = []
    for critical in (T_MARGINAL, T_BONFERRONI):
        ncp = noncentrality_for_power(critical, df, 8192)
        require(close(ncp, noncentrality_for_power(critical, df, 4096)), "quadrature convergence")
        ncps.append(ncp)
    rows = []
    for arm in ARMS:
        group = []
        for endpoint in ENDPOINTS:
            source = pooled["comparisons"][arm][endpoint]
            pairs = source["world_values"]
            require([w for w, *_ in pairs] == WORLDS, f"{arm}/{endpoint} has a nonmatching roster")
            require(source["assigned_worlds"] == len(WORLDS) and not source["missing_worlds"], f"{arm}/{endpoint} missing pair")
            plot = figure["arms"][arm][endpoint]
            row = precision_record([a - b for _, a, b in pairs], source, plot, endpoint, ncps)
            row["arm"] = arm
            row["contrast_status"] = "primary" if arm == "dams-witness" else "supportive"
            group.append(row)
        for row, adjusted in zip(group, holm([r["p_unadjusted_two_sided"] for r in group])):
            pinned = pooled["comparisons"][arm][row["endpoint"]].get("p_holm")
            require((pinned is None and adjusted is None) or (pinned is not None and close(pinned, adjusted)), f"within-arm Holm mismatch for {arm}/{row['code']}")
            row["p_holm_within_arm_four"] = adjusted
        rows.extend(group)
    for row, adjusted in zip(rows, holm([r["p_unadjusted_two_sided"] for r in rows])):
        row["p_holm_study_wide_twelve"] = adjusted
    return {
        "schema": "dams-ten-world-supplementary-precision-v1",
        "source": {
            "public_commit": PUBLIC_COMMIT,
            "public_path": "evidence/r11-10000-paired-results",
            "source_sha256": FILES,
            "original_verifier_output": verified.stdout.strip().splitlines(),
            "protocol_commit": PROTOCOL_COMMIT,
            "protocol_path": "docs/r11-formal-protocol.md",
            "protocol_sha256": PROTOCOL_SHA256,
            "protocol_lines": "294–310, 348–355",
        },
        "worlds": WORLDS,
        "reference": pooled["reference"],
        "assumptions": {
            "scope": "Retrospective numerical reporting on fixed synthetic outcomes; original scientific and precision gates are preserved.",
            "precision": "Design criterion uses marginal paired 95% half-width <= threshold/2, not the adjusted interval.",
            "multiple_testing": "Within-arm Holm families have four slots; the study-wide family has twelve. The all-zero test reserves a slot but has null p-values.",
            "interval": "Paired Student-t marginal 95% and Bonferroni simultaneous 95% within each arm, conditional on the simulation.",
            "detectable_difference": "Plug-in achieved sensitivity using retained paired SD, independent normal paired differences, two-sided tests of zero, n=10. Marginal alpha=.05; Bonferroni per-test alpha=.05/4. This is not realized power or empirical validation.",
            "degenerate": "No Student-t interval, test or minimum detectable difference is identified from all-zero paired differences.",
        },
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="directory of the pinned public evidence files")
    args = parser.parse_args()
    print(json.dumps(report(args.bundle.resolve()), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
