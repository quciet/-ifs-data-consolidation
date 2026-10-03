# Initial verification — 2026-09-08

## Automated tests

22 unittest tests passed using the bundled Python runtime. They cover:

- CSV and prepared SQLite ingestion through candidate creation.
- Expected values, historical gap filling, zero versus missing, explicit scaling.
- Duplicate keys, unmapped identities/indicators, invalid years/numbers/units.
- Country counts, coverage-loss policies, incompatible unit changes.
- Source/recipe/reference and candidate hash checks.
- Repeat runs without duplicate rows, preserved unrelated tables and original files.
- Output schema, derived values, metadata and release promotion behavior.
- Dyadic keys, intervening year columns and quoted SQL table names.
- Rejection of table rebuilding when custom indexes would be lost.
- Rejection of live SQLite imports with a WAL sidecar.

Command:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
```

## Synthetic demonstration

The completed demo in `examples/demo-workspace/` produced two added observations,
one revision, zero removals and three backfilled observations. Its result and
interpretation are in `examples/README.md`.

## Real baseline compatibility

The check script created a prepared import containing unchanged SeriesPopulation
data from `D:\IFsHistSeries\IFsHistSeries 8.67 20260527` and processed it in an isolated
workspace at `runs/baseline-compatibility-check/`.

| Check | Result |
|---|---|
| Countries | 188 |
| Original year columns | 74 |
| Candidate validation | Passed |
| Added/revised/removed observations | 0 / 0 / 0 |
| Both original database hashes unchanged | Passed |

Evidence is in `runs/baseline-compatibility-check/compatibility.json` and its linked
run report. No candidate was promoted and the main baseline configuration remains
8.67. Reproduce with a fresh destination:

```powershell
python scripts/check_baseline.py --baseline 'D:\IFsHistSeries\IFsHistSeries 8.67 20260527' --directory 'runs\another-baseline-check'
```

This establishes compatibility with the real IFs schema for the tested table.
It does not establish correct processing of an unfamiliar WDI/WPP/raw source or
validate the whole IFs preprocessor. Add a source-specific accepted-result fixture
when onboarding each new raw-source adapter.

## Datagator concordance integration — 2026-09-08

The suite now contains 40 passing tests, including 18 concordance tests. New checks
cover exact aliases, reviewed export ingestion, unresolved/ambiguous identities,
exclusions, preservation of the complete master country roster, converging labels,
rejected one-to-many mappings, source/column binding, modified evidence, and frozen
mapping records carried through a run and release. Baseline databases remain
unchanged in these integration tests.

The command-line demo using examples/datagator-country-names.csv also returned
ready: three mapped names (AUT, BLR, CRI), no unresolved names or collisions, and
185 IFs countries correctly reported as absent from this small synthetic source.
No data values were transformed and no real IFs database update was performed.

The pinned Datagator lookup matched all 188 country name/code pairs in the current
configured IFs master. Its 567 alternative-name entries produce 754 distinct lookup
keys when combined with names and codes. No conflicting normalized aliases were
found in this snapshot. See docs/country-concordance.md and the pinned provenance
in reference/datagator/ for the source and limitations of these checks.

## Prepared-import validation workflow — 2026-09-09

The complete isolated suite passes **68 tests** (27.97 seconds in the recorded run):
the previous 40 pipeline/concordance tests plus 28 prepared-import validation tests.

New coverage includes incoming country completeness before backfill; exact identities;
SQL NULL, empty strings, whitespace, text tokens, numeric sentinels and real zeros;
invalid text/BLOB values; aggregated table errors; metadata/unit compatibility;
incoming gaps, bounds, jumps, flat runs and declared frequency; heuristic scale,
country-swap and year-shift screens; and explicitly declared sum consistency with
missing components skipped.

Integration tests verify review-bound per-table decisions, unaccepted findings and
hold behavior, old-data retention versus replacement, explicit coverage-loss policy,
batch overlaps, stale source/baseline/code/evidence rejection, frozen release
evidence, three-way coverage, and post-merge screening. One fixture proves that
backfill can introduce a jump absent from the incoming table and that the candidate
report flags it. The unchanged-baseline compatibility script was also exercised
against an isolated synthetic baseline with the new review gate.

Tests verify that their original baseline files and source imports stay unchanged.
The previous real 8.67 compatibility check above remains historical evidence; it
was not rerun against the full real baseline for this feature. No real new import
has been consolidated or promoted through the new review workflow. The repository
inbox currently has no supplied import database. Statistical screens are investigation
aids and do not establish the correctness of a real source's methodology.

See [validation workflow](validation-workflow.md) for commands, actual defaults,
decision requirements, evidence files and known limits.

## Delivery-folder consolidation — 2026-09-15

The full isolated suite passes **96 tests** in 41.77 seconds: the previous 68 tests
plus 28 delivery tests. No real release or source database was processed.

Delivery checks cover preparation without source modification, retained work files,
refusal to overwrite existing outputs, exact observation provenance (including
unchanged supplied values), missingness before backfill, zero preservation,
metadata copied without defaults/dates, cumulative imports, explicit overlap order,
stable reruns, and monadic/dyadic identities.

Discard tests cover removal of an earlier/later overlapping batch, restoration of
the preceding accepted value, removal of a batch-only new table and its metadata,
return to the exact base pair when no batches remain, and preservation of skipped
table outcomes. Source imports and original baselines remain unchanged.

Failure checks cover bad units/values/keys, unrepresentable large integers,
duplicate imports, registered-source changes, external output edits, altered
evidence and interrupted publication of the database pair. Independent verification
rejects both a deliberately invented observation and an invented metadata default.
Progress records are checked as well as final reports.

Commands: delivery-prepare, delivery-merge, delivery-compare, delivery-discard.
Read [delivery workflow](delivery-workflow.md) for usage, exact-value requirements,
report interpretation, statuses and recovery. Actual-source testing will follow
when the user supplies the intended base and delivery inputs.
