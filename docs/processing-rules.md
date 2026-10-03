# IFs processing rules

The [four-workflow guide](../workflows/README.md) assigns source transformations to
preprocessing, representation/metadata normalization to validation, exact copying
to merger, and anomaly investigation to comparison. Rules below describing
rejection of text/nonfinite values apply to consolidation and legacy engines;
validation now creates new normalized imports under the
[schema/conversion contract](schema-contract.md), preserving original evidence.

For current delivery consolidation, follow [delivery workflow](delivery-workflow.md).
The delivery commands supersede legacy pre-merge approvals and numerical defaults;
this document retains reference details for the earlier request pipeline.

These rules were extracted from the existing repository notebooks on 2026-09-08.
They are the implemented defaults, not a claim that every IFs source uses the same
methodology. Source-specific decisions belong in a recipe and the decision log.

| Convention | Implementation | Existing reference |
|---|---|---|
| Monadic identity | `Country`, `FIPS_CODE`, non-empty text; unique country and code | DataTypeConversion, SQLiteMerge |
| Dyadic identity | `Actor`, `Actor_FIPS`, `Partner`, `Partner_FIPS` | DataTypeConversion |
| Numeric declarations | `DOUBLE(53)` for year values and derived fields | DataTypeConversion |
| Key declarations | `VARCHAR(255)` | DataTypeConversion |
| Blending | New non-null value wins; old value fills a missing new value | DBlending |
| Year span | Include intervening year columns, with nulls where absent | DBlending |
| Derived fields | Earliest/MostRecent are the first/last non-null values in year order | DBlending |
| Metadata | Update selected DataDict rows together with their Series tables | SQLiteMerge |
| Metadata defaults | Missing UsedInHistAnalog/UsedInFunctions become 0; Decimal Places becomes 5 | SQLiteMerge |
| Expected monadic roster | 188 by default in the prepared-import recipe; configurable | SQLiteMerge |

Year columns supported by the core are four-digit years from 1000 through 2999.
Other value columns require an explicit extension rather than silent dropping.
Country mappings are exported from a configured baseline reference table. Preserve
the actual values under FIPS_CODE: do not infer an external standard from its name.

## Missingness and revisions

Zero is an observation. Blank/SQL NULL is missing. Source-specific text tokens for SQLite/CSV must
be declared in `missing_values`. Non-numeric text and infinite/NaN numbers fail.
No automatic interpolation, zero filling or imputation is performed.

`prefer_new_non_null` preserves baseline-only countries, years and observations.
`replace` uses the new table without backfilling. Lost non-null cells fail unless
the recipe explicitly sets `allow_coverage_loss: true`, and then generate warnings.

Large-revision warnings use `abs(new-old)/abs(old) > large_revision_fraction`;
any changed non-null zero baseline is flagged. The default threshold is 0.5.
This is a screening rule, not a domain-specific anomaly model.

All year cells in the selected tables are compared. Coverage counts non-null cells
within each table/year, not the age of a table's metadata. Empty country rows do
not count as coverage. `backfilled` counts output observations recovered from the
baseline where the source did not supply a non-null value, including old-only years.

## DataDict

Prepared imports can contain multiple DataDict rows for a table, with distinct
Variable values. Duplicate Table/Variable identities fail, and existing Variable
entries cannot disappear silently. Unknown source metadata columns fail instead
of being dropped. Missing source fields inherit the matching baseline row.
The generic CSV path requires at most one baseline metadata row per target table;
more complex metadata relationships need a source adapter.

For updated rows, Last IFs Update is the run date in UTC, and Years is the span of
years that contain at least one non-null output observation. Metadata definitions,
units, source, formula or aggregation changes generate review warnings. A unit
change is blocked unless explicitly permitted and the merge policy is `replace`;
blending different units is always blocked. Prefer conversion into baseline units.

## WPP decisions requiring a dedicated recipe

`SQL Scripts/WPP Update.ipynb` contains release-specific table renaming, forecast
preservation, five-year summation, and a migration-rate conversion between per-100
and per-1,000 measures. These have been documented, not automatically generalized.
Before implementing a WPP adapter, establish the exact indicator, scenario, revision,
aggregation window, unit and target table from the new download's documentation.

`WDIBlending.ipynb` contains earlier source-specific filling logic. The generic
engine follows the more explicit key-based behavior in DBlending; validate any
WDI-specific exceptions against a previously accepted result.

## Validation and publication

Updated tables must have unique and mapped identities, finite numeric observations,
the declared country count when applicable, expected output types and consistent
Earliest/MostRecent values. Saved data and metadata are reread and compared with
computed outputs. Both candidate databases must pass SQLite integrity_check.

Validation does not run the IFs application/preprocessor or certify semantic
correctness for an unfamiliar source. A known accepted source fixture is needed
to establish that a new adapter reproduces the intended IFs transformation.

## Validation before blending

Prepared SQLite imports now require the [six-stage workflow](validation-workflow.md).
Incoming country coverage, missing representations, internal anomalies and differences
from the baseline are reported before blend. Table decisions explicitly select
backfill, replacement, retention or hold. Reviewed runs repeat anomaly/declared-sum
checks on the candidate and report before/incoming/after coverage. The legacy
large_revision_fraction candidate-change warning remains alongside configurable
pre-merge jump/revision screens; neither automatically repairs values.
