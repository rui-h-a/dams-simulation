"""Small dependency-free vector report; no interpolation or unobserved results."""
from __future__ import annotations
from html import escape
from pathlib import Path
from .storage import atomic_bytes


def report(path: Path, history: list[dict], summary: dict) -> None:
    panels = [("review_backlog_records", "Unfinished review/settlement records", "records"),
              ("authority_contribution_tv", "Formal authority / linear credit share deviation", "TV [0,1]")]
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="760" height="490" viewBox="0 0 760 490">',
           '<rect width="760" height="490" fill="white"/>',
           '<style>text{font-family:serif;font-size:14px;fill:#202020}.line{fill:none;stroke:#303030;stroke-width:1.8}</style>',
           f'<text x="70" y="24">DAMS model {escape(str(summary["model_version"]))}: one synthetic world, {escape(str(summary["regime"]))}/{escape(str(summary["backend"]))}</text>']
    for panel, (key, title, unit) in enumerate(panels):
        top, bottom = 60+panel*210, 210+panel*210
        values = [float(row[key]) for row in history]
        ymax = max(values+[0.001])*1.05
        xmax = max([row["day"] for row in history]+[1])
        svg.append(f'<text x="70" y="{top-12}">{escape(title)} ({escape(unit)})</text>')
        svg.append(f'<path d="M70,{top}V{bottom}H720" fill="none" stroke="#404040"/>')
        for fraction in (0, 0.5, 1):
            y = bottom-(bottom-top)*fraction
            svg.append(f'<text x="60" y="{y+4}" text-anchor="end">{ymax*fraction:.3g}</text>')
        points = " ".join(f'{70+650*row["day"]/xmax:.3f},{bottom-(bottom-top)*float(row[key])/ymax:.3f}' for row in history)
        svg.append(f'<polyline class="line" points="{points}"/>')
        svg.append(f'<text x="70" y="{bottom+22}">0</text><text x="720" y="{bottom+22}" text-anchor="end">{xmax} days</text>')
    svg.append('<text x="70" y="480">Recorded daily snapshots; line connects observations. No empirical calibration or uncertainty interval.</text></svg>')
    atomic_bytes(path/"report.svg", "\n".join(svg).encode())
    text = (f'# DAMS synthetic-world report\n\nModel {summary["model_version"]}; policy {summary["regime"]}; stylized evidence scenario {summary["backend"]}; independent world {summary["world"]}.\n\n'
            f'Completed {summary["days_completed"]} daily steps for {summary["n"]} synthetic agents. Remaining records: {summary["unfinished_records"]}; remaining appeals: {summary["unfinished_appeals"]}.\n\n'
            'All population distributions, behavioural coefficients, backend costs and delays are design or stress assumptions. This run is theoretical mechanism exploration. Formal weights, normalized participating weights, contribution credit and output remain distinct. Conditional completed-case delay excludes unfinished cases; the count of unfinished records is reported separately.\n\n'
            'See summary.json for raw units, timeseries.csv for snapshots, manifest.json for source/config/output hashes, and final_state.json for the complete restart state. report.svg is a single-world diagnostic, not an ensemble result.\n')
    atomic_bytes(path/"report.md", text.encode())
