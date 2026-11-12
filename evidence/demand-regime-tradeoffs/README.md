# Operating and decision changes under matched demand

Run `python3 -I -S -B verify.py` here with Python's standard library. It reads the 360 synthetic arm-level rows and reproduces `results.json` offline.

The analysis compares all 57 founding worlds and the [33 retained traditional worlds](../traditional-demand-matched) from the 65-world assignment. For each outcome, subtract the hierarchy from witnessed DAMS, then subtract that difference under baseline demand from the difference under matched demand within the same world. A strictly positive operating change means a higher operating contrast, and a strictly positive regret change means a larger regret penalty. The joint count requires both. There is no imputation for missing traditional worlds.

The input file records net operating result per member-year, decision regret per available member-hour and confirmation delay in calendar days. The operating denominator uses realized active member-days divided by 365.25. Regret divides by realized available work hours. Each arm's execution receipt and summary hashes are included. The comparison reports means and paired Student-t intervals by study. The traditional results describe the retained subset.
