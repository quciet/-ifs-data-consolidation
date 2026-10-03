# Repair formatted imports before consolidation

This command is one component of the broader
[validation workflow](../workflows/validation/WORKFLOW.md). The four-stage strategy
assigns numeric-text conversion, infinity normalization, schema enforcement and
supported unit naming to validation. These are implemented by the command below.

Use this preparation subworkflow when supplied IFs imports have fixable formatting
problems. It creates audited corrected copies; it does not edit source imports,
the base case, a registered batch, or delivery outputs. Finite stored numerical
observations on retained rows are preserved; conversions and exclusions have original evidence.
Preparation is not a substitute for semantic metadata or anomaly review.

```powershell
.\ifs.ps1 repair-imports --base 'IFsHistSeries 8.73 20260814' --input 'PATH\IFsDataImport' --output 'reviews\source-format-review'
.\ifs.ps1 repair-verify --preparation 'reviews\source-format-review'
```

`--base` resolves release names under `D:\IFsHistSeries`, like delivery commands.
Input and output paths resolve relative to the current repository directory.
`--input` accepts one import directory, or a list of `IFsDataImport*.db` files.
Directory discovery reads direct matching files only, excluding Working Files
and all nested folders. `--output` must be a new directory outside the import
directory and base. This can be under repository `reviews/`, or the delivery's
`Consolidation Report/preparation/`. Keep the complete preparation evidence with
the final delivery. No overwrite or implicit merge occurs.

## Automatic repairs

- Enforce the [schema contract](schema-contract.md), even when no cell needs repair.
  Identity fields are VARCHAR(255); year values and endpoints are DOUBLE(53).
  Both declared types and actual stored values are verified after writing.
- Convert strict decimal/scientific text to DOUBLE, including signs and zero.
  Reject grouping separators, percentage strings, unknown tokens, overflow,
  underflow and precision loss under the documented round-trip rule.
- Convert stored positive/negative infinity and the case-insensitive text tokens
  `inf`, `+inf`, `-inf`, `infinity`, `+infinity`, `-infinity` to NULL. Record raw
  representation and storage type; do not convert NaN or finite numeric sentinels.
- Rebuild import DataDict using the base's full field-specific schema. Normalize
  numeric representations for numeric fields and finite numbers to text for text
  fields (for example CURRENCY integer 1 becomes text `'1'`). Missing source columns
  retain the exact matching baseline value or NULL for new metadata, never defaults.
  Old DataDict DEFAULT clauses are removed to match the base schema; their expressions
  are never executed and source NULLs remain NULL.
- Retain the exact baseline Units label when it differs only in case/whitespace.
  Currency, scale, price basis and other meaning changes return to preprocessing.
- Use the base's canonical roster (normally 188 countries), not a hard-coded count.
  `--country-table` selects another baseline reference when needed.
- Add missing monadic country rows with NULL year values; preserve existing data.
  Report original/output row counts separately from non-null observation counts.
  Do not pad dyadic tables to 188 rows or generate an actor/partner cross product.
- Exclude extra unlabeled/non-IFs monadic rows, including populated rows, if every
  canonical entity is already present exactly once in the source. NULL roster
  padding cannot satisfy this precondition. Keep row-level identities, raw values,
  reason and populated-year counts in the audit and summarize exclusions in report.md.
  Duplicates and recognizable conflicting canonical identities remain unresolved;
  do not choose which canonical observation to retain. This rule is not dyadic.
- Remove entirely empty padding rows regardless of roster completeness. Every field,
  including endpoints and identities, must be NULL or blank for this separate rule.
- Correct misspelled country names using an unambiguous canonical code. Recover
  missing codes from canonical names. Normalize case/whitespace for identity lookup.
  A recognized conflicting name/code or convergence onto an already-used country/pair
  remains unresolved. Unknown extra codes follow the complete-roster rule above.
- Add or correct Earliest/MostRecent by copying the first/last non-null year
  observation in chronological order. These columns contain values, not years.
- Convert empty/whitespace year cells to SQL NULL and record the exact original
  string. Metadata blanks remain untouched. Zero and finite numeric sentinels
  retain their original values.

Series repairs are atomic per table; DataDict normalization is atomic as a whole.
An unresolved repair unit stays unchanged in a
corrected full import while other tables can be repaired. A file with no repairs
does not get a redundant corrected copy. Empty tables without identified source
rows are unresolved, rather than filled with an invented source roster.

## Country identity review

The default uses the pinned DataGator lookup with its fingerprint and checks that
it matches the baseline universe. Alias-only matches require a source-specific
reviewed mapping because the lookup includes historical and compound territories.
It does not infer geographic equivalence from an exact alias or fuzzy suggestion.

```powershell
.\ifs.ps1 repair-imports --base 'BASE FOLDER' --input 'PATH\IFsDataImport_Source.db' --output 'reviews\source-reviewed' --mapping 'reviewed_mapping.csv' --reviewed
```

The mapping uses DataGator's `original_name,matched_name` format, is tied to the
original labels in the supplied inputs, and is frozen in the evidence. Only pass
`--reviewed` after actual identity review. This flag does not authorize geographical
aggregation, splitting, allocation or duplicate-value selection.

`--canonical-only` explicitly limits matching to baseline identities when a
DataGator lookup is inapplicable, such as a synthetic fixture. It is never an
automatic fallback for a mismatched or modified lookup.

## Unresolved problems

The command reports unresolved identities, duplicate keys, extras without a complete roster,
unsupported schemas/custom constraints, ambiguous numeric text, NaN, BLOBs and
numbers that cannot be represented under the conversion contract. It does not
change scale, select duplicates or guess metadata. These need preprocessing rework.

Missing metadata, table/metadata name mismatches and unresolved unit meanings are
reported in `preprocessing-issues.json` with the responsible file/table and owner
stage. Unsupported schema/storage is also routed there. A formatting repair is not
a claim that every table in the file can be merged. Review optional metadata field
applicability separately; no blanket metadata completeness claim is made.

## Evidence and verification

The preparation folder contains:

- `originals/`: frozen complete inputs and their fingerprints.
- `corrected/`: new `IFsDataImport*_formatted.db` versions only where repairs occurred.
- `evidence/`: per-file JSONL recording table, original row ordinal, original/output
  identities, applied rule, missing representations, and added/removed empty rows.
- `master.json`, DataGator snapshots, optional reviewed mapping, and repair code.
- `schema-contract.json` and `normalization-code.py`: frozen declarations and
  conversion implementation; schema source is the fingerprinted base DataDict.
- `preprocessing-issues.json`: evidence-dependent rework and structural problems.
- `manifest.json`: baseline/input/output fingerprints and per-table outcomes.
- `report.md`: readable outcomes and unresolved reasons.

Original row ordinals are relative to the frozen database's scan order; they are
not purported country identities. Verification rereads the original and saved
tables, checks every year value against its original or declared conversion through
one-to-one row lineage, checks added rows contain only NULLs, and checks endpoints.
Excluded surplus rows have separate lineage/evidence: verification independently
rechecks complete source roster membership and every recorded excluded row.
It verifies saved DataDict schema/types/values against the normalization plan.
Unresolved repair units and unrelated tables are compared for preservation.
SQLite integrity is checked.
Base and live-source fingerprints must remain unchanged during preparation.

`repair-verify` checks the frozen artifacts against their recorded fingerprints
and the current baseline against its recorded pair fingerprints. It does not
rerun semantic country review or certify a file manually modified after repair.
Regenerate preparation after input, mapping, baseline or preparation-code changes.
Fingerprints detect changes; they are not signatures or access controls.

Status `prepared_with_unresolved` includes metadata/link/unit findings, has exit code 1 and may still contain useful
corrected files; read the per-table report. Failed/interrupted preparation is not
eligible for use. Completed preparation is reusable; rerunning on already repaired
imports should produce no further formatting changes.

## Select corrections and consolidate

Before first registration, select each corrected file instead of its original in
the delivery's direct IFsDataImport folder, keeping the originals in preparation
evidence. Copy unmodified sources for files with no correction. Verify the
preparation before selecting files. Do not leave original and corrected copies
together as unexamined competing imports.

For already registered batches, keep historical evidence and use delivery-discard
with a reason if replacing a batch, then register the corrected file. Previously
skipped tables do not silently become active on replay. Newly overlapping sources
still require explicit precedence through `--order`; do not guess an order to
bypass that check. A corrected version is a new batch with its own fingerprint.

Run delivery-merge and read its observations, metadata, missingness, anomalies and
skips by batch. Preparation may fix formatting while substantive problems remain.
Do not weaken the consolidation exact-value checks to accommodate unresolved files.
