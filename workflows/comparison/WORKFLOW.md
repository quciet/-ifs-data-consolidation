# Workflow 4 — Post-merge comparison

## Mission and inputs

Verify the delivery and explain what changed in updated series and their DataDict
entries. Inputs are the saved database pair, original base, ordered incoming
imports, validation/preparation evidence and the latest consolidation run.

Read [delivery workflow](../../docs/delivery-workflow.md),
[database guide](../../docs/database-guide.md), and the
[shared contract](../shared/HANDOFF.md).

## Procedure

1. Identify the actual latest run and its applied/skipped table scope. Do not infer
   updated tables from filenames or metadata dates. Exclude unrelated base tables
   from statistical conclusions; preserve and report skipped-table status.
2. Run `delivery-compare` to recheck saved pair/source/evidence fingerprints and
   exact origins. A modified pair is not a traced result. This command re-verifies
   and refreshes the recorded report; it does not import new files or regenerate
   every diagnostic under new settings.
3. Read report.md and detailed changes, coverage, metadata_changes,
   incoming_missing, findings and processed_tables CSVs. Use the registered batch
   IDs and preparation lineage to identify responsible imports and original sources.
4. Compare **base, incoming and final** data. Report added/revised observations,
   retained history and incoming missingness separately. Separate country rows
   from countries with observations and year/observation coverage. Missing values
   backfilled from the base must not conceal source gaps or infinity normalization.
5. Review DataDict changes alongside series meaning: units, currency/price basis,
   formula, definition, source and IFs configuration. Use actual coverage rather
   than silently rewriting metadata Years/date fields.
6. Distinguish patterns already present in the base, findings present in incoming
   data, and discontinuities introduced by blending. Match country/year context
   and supporting values, not only total flag counts. Attribute multi-source
   findings to all relevant batches when a single winner is insufficient.
7. Use source-appropriate frequency, bounds and relationships. Large revisions,
   flat runs and gaps are screening leads, not automatic proof of error. Do not
   invent sum relationships or remove flags merely to produce a clean report.
8. Report findings and route next actions through issues.csv. A suspicious source
   value goes to validation/preprocessing; a reporting error stays here; precedence
   or replay errors go to merger. Never modify delivered observations directly.

## Outputs and completion

Generate a separate `delivery-quality --delivery ... --output NEW_FOLDER` package for
the authorized comparison scope. It uses recorded applied tables and batch order,
verifies native evidence, and provides base/incoming/final calculations, matching
findings and blending-boundary origins. See [quality evidence](../../docs/quality-evidence.md).
Read full CSV/JSON detail and unavailable-check statuses; the offline HTML viewer
supports direct human review. Record judgments separately using `quality-review`.
Never overwrite native reports or historical manifests with a diagnostic rerun.

Produce a concise readout with exact file links, verification result, updated-table
scope, observation/metadata changes, coverage, findings, skips and batch identifiers.
Keep detailed evidence inspectable. Open issues do not disappear because a merge
completed. Separate verification success from unresolved statistical/semantic review.

For an explicitly requested log-format update on an existing delivery, run
`delivery-logs --delivery 'DELIVERY'`. It verifies the original run and refreshes
the brief official root log and detailed Consolidation Report log, with a separate
log evidence bundle. Do not rerun consolidation or rewrite historical manifests
just to change the presentation of logs.
Check that the official source-count section uses MediaWiki table syntax (`{|`,
header `!`, row `|-`, cells `|`, closing `|}`), not a Markdown pipe table. Tell the
user to paste into Edit source with line breaks intact and preview before saving.

## Existing implementation and improvement points

- Cleanup runs only on request through the separate [janitor role](../janitor/WORKFLOW.md).
  Comparison does not record release approval or trigger cleanup.

- `delivery-compare` verifies existing evidence and output. `delivery_report.py`
  creates base/final CSV comparisons during merge; `diagnostics.py` supplies screens.
- `findings.csv` has incoming/final phases; raw before/preparation evidence remains
  outside the merged database. The current report does not automatically classify
  all flags as inherited, incoming or blending-introduced.
- `delivery-quality` provides supplemental three-way statistical evidence and an
  initial routed issue export. Read-only analysis must not
  overwrite fingerprinted run evidence. New diagnostic settings require a documented
  rerun or separately versioned analysis with fresh verification.
- No IFs preprocessor/application execution or validation of every untouched series
  is implied by database integrity checks.

“Use comparison on [delivery] against its recorded base; inspect updated tables and
metadata, and report anomalies without changing the databases.”

When a run includes `baseline_names.json`, review those explicit name mappings
separately from numerical changes. Comparison reconstructs the canonical-name view
from the original baseline and verifies the mapping; a renamed country must not be
reported as a newly added country or as newly added historical observations.

Check final import packaging as well as the database pair. `import-packages.json`
identifies each output file's applied-table scope and source/package fingerprints.
Skipped tables and orphan metadata belong only in the frozen source archive and
reports. For base/incoming/final comparisons and skipped-table investigation, read
full frozen imports (restored from lossless archives when needed), not the filtered delivery copies. Applied-but-unchanged tables
remain in the packages even though the official change log excludes unchanged tables.
