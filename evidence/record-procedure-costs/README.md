# Operating results and verification processing by record procedure

These 195 rows come from retained summaries for 65 matched traditional-firm
worlds, with 300 initial members, two shared years and five years after
adoption. The central, witnessed and consensus arms use the same DAMS
allocation. Censorship is inactive. Each row carries the original summary
fingerprint, which matches the earlier [three-procedure release](https://github.com/rui-h-a/dams-simulation/tree/e229ec5095ca25d9d803d55267753d131299581d/evidence/thesis-extended/record-procedure-three-arm).
The witnessed arm is from the earlier execution release described there.

Run `python3 -I -S -B verify.py` with both files in one directory. The
script reports paired differences per member-year. Net operating result is
revenue minus paid operating expense. Verification processing is a separate
model counter. Review hours and organizational verification hours are
reported separately. Intervals are exploratory pointwise 95% Student-t
intervals over worlds.

At the illustrative valuation of 0.1 operating resource unit per verification
processing unit, the break-even amount combines the operating shortfall and
the valued processing difference. Other separately valued resource changes
and the losses prevented by custody are outside that illustrative calculation.
The 0.1 valuation is a scenario input.
