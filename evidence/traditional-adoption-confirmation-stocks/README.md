# Confirmation delay and unfinished records under staged adoption

This companion to the [traditional-firm adoption timing comparison](https://github.com/rui-h-a/dams-simulation/tree/6d267d1ee99aaa7d60147d11a1300c987d4c505d/evidence/traditional-adoption-timing) reports confirmation counts and delay summaries for 65 staged-versus-triggered pairs. Both policies use independently witnessed records. The results cover the full history, including two years before the common adoption boundary.

All 130 compact SQLite states were decompressed and matched against their original arm receipts. Confirmation counts, sums of delays and 95th percentiles come from the retained delay histograms. Unfinished counts come from the three distinct terminal queues. Review and settlement contribute to the total unfinished-record measure. Appeals are counted separately. These states summarize the event history. The public data supply world-level aggregates for recomputation.

Run `python3 -I -S -B verify.py`. Each output lists the staged-minus-triggered difference and an unadjusted exploratory 95% paired Student-t interval. Counts are records per world. Delays are days, conditional on confirmation.
