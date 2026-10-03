# Adding a source recipe

For raw-source import preparation and DataDict completion, also read
[import preparation](import-preparation.md). It separates source facts from IFs
settings and documents how to capture user-corrected metadata in scoped profiles.

For current delivery consolidation, follow [delivery workflow](delivery-workflow.md).
The delivery commands supersede legacy pre-merge approvals and numerical defaults;
this document retains reference details for the earlier request pipeline.

Recipes are JSON files. Existing requests retain snapshots, so edit the maintained
recipe for future runs and ingest again; do not edit archived request files.

## Shared fields

| Field | Meaning |
|---|---|
| schema_version | `1` |
| id | Stable descriptive source/indicator-family name |
| status | `active` to execute; draft recipes fail intake |
| adapter | `ifs_sqlite` or `csv_long` |
| kind | `monadic` (default) or `dyadic` for SQLite |
| merge_policy | `prefer_new_non_null` or `replace` |
| expected_country_count | Final monadic row count, or null to disable; null for dyadic |
| country_reference | Optional CSV path, relative to repository or absolute; default is configured baseline reference |
| large_revision_fraction | Relative change warning threshold, default 0.5 |
| allow_coverage_loss | Explicit permission for non-null cell removal, default false |
| allow_unit_change | Default false; even when true, changed units cannot be blended |
| tables | SQLite only: null for all source DataDict tables, or an explicit list |

The prepared-import recipes assume the source is already mapped to IFs. They are
not raw WDI or WPP download recipes. Review Source, Definition, Units, table names
and country codes before choosing one.

## Long CSV

The adapter reads one row per country/indicator/year. Its column mapping requires
`country`, `indicator`, `year`, `value`, and optionally `unit`. `encoding` defaults
to utf-8-sig; `delimiter` defaults to a comma. Missing value tokens are an explicit
list. Duplicate headers, malformed row widths and duplicate observations fail.

Each entry in `indicators` maps a source code to:

- `table`: exact IFs Series table name, distinct for each mapped indicator.
- `input_unit`: declared source unit, checked if a unit column is provided.
- `multiplier`: finite scaling factor, default 1.
- `min_value` / `max_value`: optional bounds on the converted value.
- `metadata`: Variable, Definition, Units and Source are required; other existing
  DataDict fields can be supplied. Units describes the converted output value.

Country reference CSV columns are `source_code,Country,FIPS_CODE`. Multiple source
aliases may point to the same canonical country; conflicting mappings fail.
Copy the exported baseline reference to a maintained source-specific reference
file if needed and add verified aliases. Intake freezes this mapping for the run.

Unmapped indicators/countries fail by default. Known aggregates or out-of-scope
indicators can be listed explicitly in `ignored_country_codes` or
`ignored_indicators`. Explain their exclusion in the recipe description/decision
log. Every mapped indicator must have at least one usable country observation row.

If the source has no unit column, its unit declaration relies on the source
documentation and recipe review. Header matching cannot validate an absent unit.

## Onboarding checklist

1. Preserve supplied downloads and documentation/URL/version. Do not fetch online
   without an explicit user request; a reference URL is not fetching permission.
2. Use the supplied DataDict Table/Variable list as indicator scope. Map it to raw
   columns and variants; use matching base metadata and source evidence to infer
   missing units/meaning, recording the reasoning and uncertainty.
3. Confirm source indicator mappings and country identities, including aggregates.
4. Define missingness, conversions, bounds, historical backfill/replacement policy.
5. Implement any unsupported transformation as reusable code with a source fixture.
6. Run against a stable baseline, inspect value and metadata differences, and
   compare to a known accepted result before treating the recipe as routine.
7. Record the decision, evidence and limitations in docs/decisions.md.
8. Default preparation output to a new versioned subfolder within the supplied
   source folder unless an alternative was explicitly requested. Exclude generated
   packages from source snapshots and never overwrite earlier outputs.

The included synthetic demo shows scaling, gap filling and review artifacts. It
validates pipeline mechanics, not the methodology of a real external dataset.

## Datagator concordance bundles

Generate a bundle with the concordance command described in [country concordance](country-concordance.md). Copy all generated recipe_fields.json fields into a CSV recipe: country_reference, concordance_manifest and ignored_country_codes. Keep columns.country consistent. Intake verifies the original source hash and freezes mapping evidence. Mapping cannot supply a geographic arithmetic rule.

## Prepared-import validation and decisions

Read [validation workflow](validation-workflow.md) for validation.defaults, table
overrides, explicit sum_rules, missing_values and missing_numeric_values. The
prepared-import run command now requires a completed review-bound decision file
with each table action. The archived merge_policy still defines recipe compatibility
(e.g. replacement required for changed units); the decision selects the actual
action per table. allow_coverage_loss and allow_unit_change remain independent gates.
CSV keeps its existing recipe-level merge policy.
