# Exploratory decision execution in the 300-member founding startup

This packet preserves the world-level inputs to the paired estimates and within-world changes reported in the thesis. The six settings share ten worlds numbered 2000–2009. Each row is [world, DAMS with independent witness, hierarchy]. The model uses founding adoption, organic growth and a one-year observation period.

The [Amendment 9 protocol](https://github.com/rui-h-a/dams-simulation/blob/084e60862cdfa199c3641d0cc76142f03d799138/docs/r11-formal-protocol.md#L318-L330) specifies the original four settings. The [implementation of the two smaller settings](https://github.com/rui-h-a/dams-simulation/commit/e0f216ee55ad5af0d0b357d3112aa72c2d6b579d) is fixed separately.

Run `python3 -I -S -B verify.py` to reproduce unadjusted paired 95% Student-t intervals. Contrasts are DAMS minus hierarchy. Changes subtract each world's zero-effect contrast from that world's nonzero-effect contrast. Net operating result uses resource units per member-year, decision regret uses regret units per available member-hour, operating margin uses the ratio of revenue and output uses work units per available member-hour.

The four original settings were cross-checked against hash-verified arm summaries and receipts. The two smaller settings use archived world-level inference and diagnostics. The verifier recalculates summaries from retained inputs. It does not execute the model, reconstruct work journals or establish effects in operating firms.
