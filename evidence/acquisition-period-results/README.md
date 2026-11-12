# Operating results by phase of acquisition

This supplement uses retained results from 120 synthetic worlds of a 300-member listed firm. Each world compares witnessed DAMS with the retained hierarchy under two integration arrangements. The arrangements centralize authority in the buyer or preserve the target's hierarchy and central record. Coordination demands and learning and memory retention differ between the arrangements.

Run `python3 verify.py` in this directory with Python 3.10 or later. It reads the released CSV locally. The 480 arms each have four rows:

| Period | Model days, inclusive |
| --- | --- |
| Shared pre-adoption history | 0–730 |
| Adoption before closing | 731–1096 |
| Closing through integration work | 1097–1122 |
| Integrated operations | 1123–2556 |

Day zero is 1 January 2020. The table reports arm totals of active member-calendar days, revenue and paid operating expense. The extractor checked the retained arm receipts and SHA-256 digests of each summary and daily series. It matched the logged closing and integration dates, summed 1,227,360 daily rows, reconciled the sums to the cumulative fields and summaries, and reproduced each arm's full-period operating rate in the [prior paired release](https://github.com/rui-h-a/dams-simulation/tree/ce8c43763b823c6636c56244ab4527563354a2d2/evidence/acquisition-matched-worlds).

The operating rate divides revenue less paid operating expense by active member-calendar days divided by 365.25. Consideration and separately booked transaction and integration costs are outside that numerator. `preserve` and `centralize` report DAMS minus hierarchy within each arrangement. `central_minus_autonomy` subtracts those contrasts. `mode_hierarchy` and `mode_dams` compare centralization with autonomy separately by policy. The retrospective, unadjusted 95% Student-t intervals use the 120 world-level differences. The paired analysis concerns modeled arrangements in which every deal closed and integrated at the specified days. The released data contain only aggregate synthetic outcomes.
