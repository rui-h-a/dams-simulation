# Enterprise main-study backlog history

Run with Python 3.11 or later from this directory.

```sh
python3 -I -S -B verify.py
```

The command runs offline with the standard library. It verifies the packet
hashes and reads 2,557 simulated calendar days for each of four arms in ten
worlds. It recomputes the common pre-adoption stock, first- and third-year
contrasts, final stocks and net stock decline over the last 60 days. Each arm's
percentage uses its own stock immediately before those 60 days as the
denominator. The final DAMS-minus-hierarchy interval uses paired world-level
differences and an exploratory, unadjusted 95% Student t interval.

`daily.csv.gz` contains selected columns from the retained daily series. The
original series digests and terminal stock counts match the pinned public
`terminal.csv` copy. The release identifies the model version and retained
terminal records. [Published terminal-stock packet](https://github.com/rui-h-a/dams-simulation/tree/416d6de4325fe5dea722634ed3106c08b83f86e9/evidence/r11-10000-terminal-stock)

The stock includes records awaiting review or settlement. Its change measures
net entries and exits. Event-level validation requires the original journals.
