# R11 enterprise study, 10,000 initial members

These synthetic results summarize ten paired worlds, numbered 1000–1009, with four arms per world. The arms share two pre-adoption years within each world and run for five more years after adoption. The reference arm retains the performance-tier hierarchy.

- `roster.json` lists assigned and completed worlds.
- `pooled.json` contains the outcome estimates and full-precision paired arm values. Each `world_values` row holds the world ID, intervention value and reference value.
- `figure.json` contains plot-ready contrasts. Annual or population projections are outside this bundle.
- `SHA256SUMS` identifies the retained versions of these three files.

Run `python3 verify.py` from any working directory to check the roster and recompute the paired means, standard deviations, and reported Student t intervals. The verifier uses the Python standard library. Run `shasum -a 256 -c SHA256SUMS` from this directory to check the file digests.

The [original execution revision](https://github.com/rui-h-a/dams-simulation/tree/956725516ddac7f7c2336b0ccc23a69c5d7a27c5) and [versioned guide](https://github.com/rui-h-a/dams-simulation/blob/d79a74eb18dbb6ab29559dccea1b0f12faafa863/docs/thesis-reproduction.md) identify the model and analysis procedure. The original event journals are required for independent event-level validation. This bundle supports statistical recomputation from retained outcomes.
