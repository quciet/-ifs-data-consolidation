# Local quality evidence and independent review

The quality engine runs entirely locally using Python's standard library. It reads
SQLite inputs, freezes byte-identical copies, calculates evidence, and writes a new
package. It does not repair observations, merge data, contact a model, or fetch data.
Human review and optional AI interpretation are separate, append-only records.

## Before consolidation

Copy `workflows/comparison/quality-spec.template.json`, supply actual paths, exact
table names and dataset IDs, and configure meaningful table settings. Paths are
relative to the specification file unless absolute. Specify one base, incoming
datasets in explicit first-to-last precedence order, and optionally one final.
An incoming ID should be its registered batch ID when available.

```powershell
.\ifs.ps1 quality-evidence --spec 'quality-spec.json' --output 'reviews\quality-001'
.\ifs.ps1 quality-verify --evidence 'reviews\quality-001'
```

The output directory must not exist. A changed input, setting or code version needs
a new run, not an overwrite. Inputs must be closed/checkpointed. Frozen copies may
require substantial disk space, especially when the base database is large.
Tables are processed individually, but exported evidence is currently held in memory;
select bounded table groups for very large updates.

## After consolidation

```powershell
.\ifs.ps1 delivery-quality --delivery 'DELIVERY' --output 'reviews\quality-002'
# Optional: --settings 'quality-settings.json'
```

This verifies native fingerprints and exact origins, then selects the recorded
applied tables and active batches in recorded precedence order. It reads full frozen
imports, not trimmed delivery packages. Skipped/excluded outcomes remain in the
copied `native-context/delivery.json` and `processed_tables.csv`. Skipped content is
not certified by statistical analysis. Recorded baseline name modernization is
applied as a comparison-only view, with the original frozen input unchanged.
If legacy archives need restoration or publication is pending, use the native recovery
workflow first. This command neither recovers a publication nor refreshes native reports.

`delivery-compare` keeps its existing verification role. Quality output is supplemental
and must not be written into historical `runs` or `imports`. Normal validation and
comparison tasks should generate this package for their authorized scope; workflow
development does not authorize analysis of old real deliveries.

## Calculations and interpretation

- Annual coverage records row count, populated countries, missing cells, and exact
  membership. Adjacent periods additionally record entering/leaving countries.
- Available-country mean/median and changes are always screening statistics.
  Totals are calculated only with explicit `aggregation: "sum"`. Unknown additivity
  is not inferred from DataDict labels. No weighted world rate is manufactured.
- Matched-country totals/means/medians use the intersection of countries populated
  in both periods. The membership is saved. Aggregate jump flags use this cohort;
  available-cohort movements are retained for comparison without conflating coverage.
- Country absolute change is `new-old`; relative change is `(new-old)/abs(old)`.
  Zero/near-zero denominators produce no percentage or ratio; absolute changes remain.
  A jump exceeds both the absolute floor and relative threshold (or has an undefined
  relative change). Frequency defaults to one year; gaps are explicit and not annualized.
- Baseline comparisons include unchanged cells, revisions, additions and incoming
  missingness for the same identity/year. Missingness never implies withdrawal.
- Scaling screens measure the share of eligible nonzero pairs with `new/old` within
  a relative tolerance of a specified factor. Results include denominator, support,
  contributing evidence IDs and minimum-sample status, both across time and against base.
- A spike/reversal requires a large first movement, an opposing next movement and
  return residual at most `reversal_tolerance` times the first movement.
- Historical variability compares the current relative change with preceding changes
  only: distance from median divided by `1.4826 * MAD`. Minimum history applies.
  Constant history is explicitly identified; no infinite score is manufactured.
- Swap searches evaluate country pairs. For each complete year, errors are normalized
  by the largest absolute value among the four values (with a near-zero floor), then
  averaged across countries/years. Crossed assignment must meet the tolerance and
  improve sufficiently on direct assignment. Baseline candidates require multiple
  years; temporal candidates compare one configured interval. All candidate matches,
  including ambiguous alternatives, retain actual supporting values. Noncandidate
  pair counts are retained and raw observations permit recalculation. A candidate is
  never an instruction to reassign values. Pair search is quadratic in country count.
- Explicit applied-table scopes enable replay of non-null precedence to compare final
  observations and assign origins. Boundary jumps identify both origins. Generic spec
  replay is a check of the declaration, not native delivery certification.
- Matching findings across datasets require matching rule and numerical evidence;
  matches in base or incoming are identified. Others remain unresolved. No claim is
  made that unmatched findings necessarily originated in the update.

Settings are in `DEFAULTS` in `quality.py`, validated strictly, and snapshotted as
resolved per-table settings. `settings.defaults` applies globally; `settings.tables`
overrides by exact table name. These are screening defaults, not source-reviewed
domain judgments. Bounds remain unset unless supplied. Dyadic statistical checks,
weighted rates and inferred cross-table sum relationships are not implemented here.
Existing declared-sum checks in the pipeline remain available separately.

## Package and review

Open `report.html` locally: select a table/country to see trajectories, select an
evidence collection, search, sort column headings, and page through rows. It has no
CDN, network call, server, or runtime installation requirement. Numeric and metadata
content is rendered as text. Charts use common axes per view, plot missing values as
gaps, and show each dataset separately. Country means are explicitly unweighted.

`evidence.json` and collection CSVs contain full calculations, metadata, original
missing representations, observations and findings. `checks.csv` distinguishes
calculated, not applicable and insufficient evidence. Empty findings do not establish
a pass. `issues.csv` provides initial routing for the shared workflow contract.
`manifest.json` hashes inputs, frozen copies, artifacts and implementation files.
`quality-verify` checks the package against its manifest; hashes detect changes, not
malicious replacement of the entire package. Original external files can subsequently
move without invalidating the frozen package.

```powershell
.\ifs.ps1 quality-review --evidence 'reviews\quality-001' --finding 'FINDING_ID' `
  --reviewer 'Reviewer name' --decision requires_investigation `
  --rationale 'The source notes do not explain the common scaling factor.'
```

Decisions: `explained`, `requires_investigation`, `confirmed_problem`. Each new record
references an evidence-package ID and finding ID, reviewer, author type and rationale.
Later records do not erase earlier decisions or suppress calculations. Review files
are intentionally outside the immutable calculation manifest.

AI is optional. `interpretation-input.json` points to numerical evidence and metadata
and provides the interpretation contract. An explicitly invoked model can summarize
these artifacts and record suggestions with `--author-type ai`. It must cite evidence,
label hypotheses, and never silently treat its suggestion as a human decision. The
engine itself never calls an AI service.

## Development verification

`tests/test_quality.py` uses isolated synthetic SQLite databases. Run it with the
repository's working Python, then run the full unittest suite. No real releases
are needed for development or regression tests.

To create an inspectable synthetic package with scaling, swaps and coverage loss:

```powershell
python scripts/demo_quality.py --output examples/quality-evidence-demo-new
```

The output directory must be new. Open its `evidence/report.html` directly. A
temporary localhost server may be used for developer browser testing but is not
needed by the report or installed by the quality commands.
