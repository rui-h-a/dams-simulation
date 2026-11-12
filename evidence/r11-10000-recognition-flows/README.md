# Main-study recognition stock and flows

Run `python3 -I -S -B verify.py` here. The standard-library verifier recomputes all 40 balances and the three paired comparisons offline. The CSV binds each arm to its summary, execution receipt and shared-prehistory receipt. It contains synthetic aggregate counts.

Backlog comprises records awaiting review or settlement. Submissions enter it. First-review rejections and confirmations leave it. Successful appeals return to settlement, and appeals awaiting action are tracked separately. In these runs each work event submits one original record. The executed configurations and summary counters were checked for the absence of censorship, duplicate injection and acquisition. Review rejection is a first-stage action. A rejected claim can later return to settlement through appeal.

The processed appeal count is total appeal hours divided by the fixed 0.15 hours per processed appeal, checked against an integer within the cumulative floating-point tolerance. The original implementation describes [confirmation, review and appeal transitions](https://github.com/rui-h-a/dams-simulation/blob/3b4d44676872fff8d602cafbc47b49f0df6f79b9/dams_sim/longitudinal_model.py#L361-L439).

Arm flows are cumulative over two shared pre-adoption years and five post-adoption years. The common history cancels in each within-world comparison. The results average the ten differences with equal weights and reproduce the [retained terminal stocks](https://github.com/rui-h-a/dams-simulation/tree/416d6de4325fe5dea722634ed3106c08b83f86e9/evidence/r11-10000-terminal-stock). The decomposition is an accounting identity for aggregate stocks. Event-level verification requires the original journals.
