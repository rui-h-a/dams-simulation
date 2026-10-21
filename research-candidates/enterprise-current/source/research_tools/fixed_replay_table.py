"""Format an already executed fixed-tape result; never run or alter the model."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    raw = args.result.read_bytes()
    item = manifest["assets"]["result.json"]
    assert len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"]
    result = json.loads(raw)
    assert result["roster_count"] == 10000 and result["event_count"] == 20403
    assert result["new_formal_abm_worlds"] == 0 and not result["behavior_simulated"]
    rows = {(x["day"], x["domain"]): x for x in result["observations"]}
    policies = [("Equal eligibility", "equal"), ("Linear credit", "linear"),
                ("DAMS, $\\alpha=0.5$", "sublinear"), ("Performance tiers", "performance_tiers"),
                ("Appointment-tenure tiers", "tenure_tiers")]
    text = [r"% Generated from complete fixed-replay result SHA256 " + item["sha256"],
            r"\begin{table}[htbp]", r"\centering", r"\small",
            r"\caption{Formal-share concentration in the designed fixed-tape experiment. Gini is calculated over the eligible population: 10,000 people per domain on day 1 and 9,900 on day 365. Only 323 of the 10,000 records are confirmed on day 1; by day 365, 9,896 are confirmed and 104 revoked, with none pending. Record counts cover both domains. The dates are the first-confirmation and final observations; all six query dates and complete vectors are public. These deterministic values have no Monte Carlo interval and are not measures of fairness or behavioral effects. Source: \texttt{ed82a5817158}.}",
            r"\label{tab:fixed-temporal-replay}", r"\setlength{\tabcolsep}{8pt}",
            r"\begin{tabular}{@{}lrrrr@{}}", r"\toprule",
            r" & \multicolumn{2}{c}{First confirmations: day 1} & \multicolumn{2}{c}{Final observation: day 365} \\",
            r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}",
            r"Authority rule & Domain 0 & Domain 1 & Domain 0 & Domain 1 \\", r"\midrule"]
    for label, policy in policies:
        values = [rows[key]["policies"][policy]["formal_share_gini"] for key in [(1, "d0"), (1, "d1"), (365, "d0"), (365, "d1")]]
        assert all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in values)
        text.append(label + " & " + " & ".join(f"{x:.3f}" for x in values) + r" \\")
    text += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    args.output.write_text("\n".join(text))


if __name__ == "__main__":
    main()
