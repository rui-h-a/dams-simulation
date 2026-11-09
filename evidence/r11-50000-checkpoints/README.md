# Checkpoint evidence for the 50,000-member pilot

This pilot used the multinational organic-growth scenario with 50,000 initial members. Two simulated worlds (0 and 1) each had a retained hierarchy arm and a witnessed DAMS arm. The plan called for two years of shared history and five years after adoption. The last finalized checkpoint recorded for all four arms is at model day 1,461, the end of two years after adoption. The later day 1,826 entry is a temporary SQLite file in each arm. The inventory has no completed arm receipts for this pilot.

## Recompute the measurements

Run `python3 verify.py` with Python 3.9 or later. The four case results and the inventory totals should match `results.json`.

The elapsed interval subtracts the recorded modification times of the finalized **JSON** checkpoint files at model days 1,096 and 1,461, then divides by 365 simulated days. The exported times are rebased to the first timestamp among the selected checkpoint files, which leaves every interval unchanged. The range is 97.3260 to 98.0986 seconds per simulated day. These are elapsed file timestamp differences from a C4D pilot, including checkpoint writing and any intervening waits or interruptions. Process CPU time and full validation time would require separate receipts. The inventory also records the corresponding SQLite entries, whose modification times differ from the JSON times.

The stored file sizes are grouped by byte count and scope in `inventory-sizes.csv`. The full inventory records 455,731,206,463 bytes across 2,732 files. The batch tree records 455,502,065,385 bytes. It includes the four comparison arms, the shared histories, and working, restored and temporary copies. These sums describe files in the retained inventory. Peak concurrent disk use would require a separate measurement.

## Evidence and provenance

`checkpoint-files.csv` contains inventory metadata, including the recorded byte count and SHA-256 for each of the 16 finalized files and four temporary files. The data omit storage locations and absolute modification times. During extraction, the original plan and inventory were rehashed, the plan was matched to its inventory entry, and the finalized checkpoint rows were cross-checked against the retained checkpoint receipt. Checkpoint file bodies were outside that check. The byte and hash declarations are provenance for the stored objects.

The plan names the same engine digest independently recomputed from the 36 Python source files in Git revision [3b4d446](https://github.com/rui-h-a/dams-simulation/tree/3b4d44676872fff8d602cafbc47b49f0df6f79b9). Run `python3 verify.py --source-repo /path/to/dams-simulation` to verify the engine digest against a local Git repository. The revision is pinned by content and carries no Git signature. The pinned [measurement note](https://github.com/rui-h-a/dams-simulation/blob/d79a74eb18dbb6ab29559dccea1b0f12faafa863/docs/r11-resource-measurements.md#L28-L42) identifies the machine class and describes a broader timing window. This packet computes the interval between the first and second finalized post-adoption checkpoints.
