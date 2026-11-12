# Outstanding records by year after adoption

This packet uses the ten retained 10,000-member multinational worlds from the
main study and all four arms in each world. Each annual value counts end-of-day
records awaiting first review or settlement. Appeals are counted separately in
the model and are outside these comparisons. The year windows are 2022 through
2026. The final 60 days are the period after new work stops.

The annual record-day totals and year-end stocks were derived from 40
retained daily series, each checked against its arm's archived SHA-256 and the
independently published daily backlog series. All five annual totals sum to
the previously reported post-adoption integral for each arm. The packet
recomputes paired differences in annual daily means and year-end stocks,
plus the change in each paired gap over the last 60 days. Intervals are
pointwise, exploratory paired 95% Student-t intervals over ten worlds.

Download all three files into one directory and run:

```sh
python3 -I -S -B verify.py
```

This command reads the fixed annual aggregates. The original [daily backlog
series and provenance](https://github.com/rui-h-a/dams-simulation/tree/e229ec5095ca25d9d803d55267753d131299581d/evidence/thesis-extended/backlog-history)
remain available for deriving the annual stocks.
