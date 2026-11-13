# Operating results and review backlog over ten years

Run `python3 -I -S -B verify.py` with Python 3.11 or later in this directory. The 1,300 rows cover 65 paired traditional-firm worlds, two policies and ten post-adoption years from 2022 to 2031. The rows are synthetic aggregates selected from the calendar-corrected retained annual series (source SHA-256 `ed80b05bbfbb9521ade4f501bb9a47d7f9d0f8b8cd6fa1e973bf778be25b1d4d`). Each model year ends on 31 December. Exposure is active member-calendar days divided by 365.25. Operating results use recorded revenue less recorded operating expense per member-year. Year-end review backlog divides records awaiting review or settlement by active membership on that day.

Within each arm, combine operating rates using the arm's member-year exposure. Subtract the hierarchy rate from the witnessed DAMS rate in each world. The two window means use equal weights across worlds. For the change in that gap, the paired pointwise Student-t interval uses the 65 world-level late-minus-early differences. The year-end gap is the mean of the two arm rates' difference in each world. This supplement checks retained aggregates. The original event journals are outside this calculation.

## Retrospective projection check

Run `python3 -I -S -B verify_projection.py` in the same directory. For each arm, compare three rules for years 6–10: carry forward the exposure-weighted rate of years 1–5, hold the rate at the mean of years 2–5, or extend a least-squares line fitted to years 2–5. Each rule uses that arm's year-5 exposure for the projected years. The ten-year predicted contrast retains the observed first five years. The late-period check compares only the predicted years with their observed simulated values.

Leave out each paired world in turn. Select the rule with the lowest squared error on the other 64 worlds using either the ten-year or the late-period criterion. The script checks root mean square error, mean error, observed dispersion and sign classification against the reported values. Both criteria use the same synthetic organization context.
