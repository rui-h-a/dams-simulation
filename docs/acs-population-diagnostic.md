# ACS Rhode Island population measurement diagnostic

This standard-library tool measures observed joint distributions. It does not
run DAMS, generate synthetic people, estimate behavioral coefficients, or admit
calibration, a holdout, a firm population, or a formal simulation world. Rhode
Island was selected because its state files are small, before observing results.
Both 2023 and 2024 become seen data after running this diagnostic.

Freeze a plan **before downloading**. Use a fresh data directory on storage with
at least 2 GiB free on that volume and the supplied safety-root volume:

```sh
python3 -B research_tools/acs_population_diagnostic.py plan --output plan.json
python3 -B research_tools/acs_population_diagnostic.py run \
  --plan plan.json --plan-sha256 PLAN_SHA256 --output NEW_DATA_DIRECTORY \
  --safety-root LOCAL_SAFETY_DIRECTORY --download-official-data
```

The plan pins the exact implementation, the four official 2023/2024 RI person
and housing ZIP URLs, fields, universes, aliases, bins and diagnostic status.
There are no credentials, paid services, redirects or retries. The entire run
has one 90-second I/O window, a 100 MiB compressed-body ceiling, 25 MiB per ZIP,
and 1 GiB cumulative decompression ceiling. Incomplete files retain `.partial`;
an existing output directory is refused. A failed run is not implicitly resumed
or retried. Do not remove a failed directory to manufacture a fresh budget.

`manifest.json` records SHA, bytes, normal HTTP EOF, Content-Length, CSV schema
and ZIP member CRC/EOF observations. A schema, join or universe failure retains
partial evidence and does not produce an accepted diagnostic. The raw ZIPs and
person-level derived CSVs belong in the data directory, not in the public code
repository. Summary and manifest use relative names and official URLs only.

The person key is `(year, SERIALNO, SPORDER)` within fixed state 44; household
references join many-to-one by SERIALNO within year. Housing-unit composition is
checked against NP/R18/R65. Group quarters remain a distinct context. Original
category codes, blanks and allocation flags are retained in the derived CSV;
blank is not zero, and an allocated observation is not a separate missing row.
Declared STATE/ST and TYPEHUGQ/TYPE header aliases are checked for ambiguity,
and the original header is recorded. They do not authorize arbitrary renaming.

Person statistics use PWGTP even after joining household data. All 80 signed
replicate weights are preserved. Every total and ratio is re-estimated using
each replicate; `Var = (4/80) sum((estimate_r-estimate)^2)`. Zero replicate
denominators yield an unestimated interval, not clipped weights. These are
survey sampling errors, not all measurement/model uncertainty. No assumption
that person records or co-resident household members are independent is made.

Three tables report full and conditional person-weighted distributions:

* Frozen age band × original SCHL × ESR × COW, all persons, including N/A.
* Original INDP × frozen usual-hours band among current civilian employed
  persons aged 16+, ESR 1/2. Hours describe the past 12 months, not the same
  reference period as current employment.
* Household child/older-person context × ESR, persons aged 16+ in ESR 1–6;
  institutional and noninstitutional group quarters remain separate.

Education codes are not years or ability. Age is not tenure; wages are not
productivity; household age composition is not observed caregiving hours.
No sex/background-to-ability, integrity, reputation or care-role mapping is
implemented. This tool does not undo rounding/topcoding, inflate missing values,
convert income into hourly productivity, or identify personality, trust,
motivation, daily fatigue, skills, causal treatment effects or firm networks.
It does not link people across years or demonstrate non-US external validity.

Official references:

* [2024 User Guide](https://www2.census.gov/programs-surveys/acs/tech_docs/pums/2024ACS_PUMS_User_Guide.pdf),
  2025-10-16, pp. 6–11: merge, universe/missing and weights.
* [2024 Accuracy](https://www2.census.gov/programs-surveys/acs/tech_docs/pums/accuracy/2024AccuracyPUMS.pdf),
  pp. 12–13: nonsampling limitations and signed replicate weights.
* [2023 dictionary](https://www2.census.gov/programs-surveys/acs/tech_docs/pums/data_dict/PUMS_Data_Dictionary_2023.pdf)
  (2024-10-17) and [2024 dictionary](https://www2.census.gov/programs-surveys/acs/tech_docs/pums/data_dict/PUMS_Data_Dictionary_2024.pdf)
  (2025-10-16): variable-specific definitions, allocation and universe codes.

Small offline arithmetic tests (no network/Model):

```sh
python3 -B tests/test_acs_population_diagnostic.py
```
