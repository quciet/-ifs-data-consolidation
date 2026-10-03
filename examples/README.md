# Synthetic example

The `demo` command creates a small, isolated workspace and runs an explicit CSV
recipe. Country names and values are synthetic and unrelated to real IFs data.

| Country | Baseline 2022 | Baseline 2023 | Download 2023 | Download 2024 | Result 2022 | Result 2023 | Result 2024 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Example A | 100 | 110 | 115 | 120 | 100 | 115 | 120 |
| Example B | 200 | 220 | missing | 230 | 200 | 220 | 230 |

Expected: two new observations, one revision, zero removals, and three backfilled
observations (both 2022 values and Example B's 2023 value). Earliest values are
100/200 and MostRecent values are 120/230. An unrelated baseline table is preserved.

The generated workspace contains its baseline, source CSV, recipe, references,
archived request, and run report. It cannot affect the main configured baseline.
The demo refuses an existing destination; choose a fresh directory for another run.

## Datagator names example

The CSV datagator-country-names.csv has three names from the pinned lookup and synthetic example values. Run .\ifs.ps1 concordance --input examples/datagator-country-names.csv --country-column Country --source Demo from the repository root. Expected targets: AUT, BLR and CRI; the complete 188-country master is retained. This example demonstrates name mapping only and does not update the baseline databases.
