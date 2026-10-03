# Workflow 3 — Data merger

## Mission and inputs

Merge selected formatted imports into a versioned IFsHistSeries.db / DataDict.db
pair with exact-source provenance. Inputs are the intended base/delivery, selected
imports and their validation evidence, unresolved-table scope, and explicit
precedence for newly overlapping sources.

Read [delivery workflow](../../docs/delivery-workflow.md) and the
[shared contract](../shared/HANDOFF.md). This stage uses the existing delivery
commands; it does not create another merge engine.

## Procedure

1. Inspect ownership/state of any existing output pair and recorded base. A new
   delivery uses `delivery-prepare`. Never overwrite an unexplained existing pair.
2. Check the validation handoff and native preparation verification. Select each
   corrected version instead of its original; preserve all originals and evidence.
   Discovery reads only direct IFsDataImport*.db files under IFsDataImport. Honor
   exclusions such as Working Files. New raw data does not enter this stage.
3. Check competing tables. Newly overlapping sources require explicit first-to-last
   precedence via `--order`; do not infer priority from filename/date alone.
4. Run `delivery-merge`. Non-null incoming observations win; missing values retain
   preceding/base observations. Zero is a value. Metadata comes from the import,
   retaining baseline fields absent from source. Do not invent numeric defaults.
5. Let structural failures skip their tables while valid tables proceed. Route
   fixable import issues to validation and source problems to preprocessing;
   do not cast numeric text, normalize infinity, relabel units or repair identities
   inside the merger. These arrive as new validated imports.
6. Require the existing exact-source and SQLite-integrity verification before the
   output pair is saved. Preserve batch fingerprints, ordered replay and logs.
   Publish a brief root change log counting distinct created/updated tables by
   final DataDict Source, and keep the detailed batch/table log under Consolidation
   Report. Exclude unchanged/skipped tables from brief counts; include metadata-only
   changes. See the delivery guide for the two-log definitions.
   The official source-count table uses native MediaWiki wikitext, with literal
   source labels and no Markdown code fence, ready for the wiki's Edit source mode.
7. Include the supplied update's Working Files folder in the final delivery. Copy
   all files and nested folders unchanged into the delivery's Working Files; accept
   Working File as the source folder name when that is how it was supplied. Copy
   from the current update, not the previous base release. If source and destination
   are already the same folder, preserve it in place. Preserve existing destination
   files; reuse identical copies and resolve differing same-path files without
   silently overwriting them. Verify copied relative paths and file fingerprints,
   and record the source, destination, file count and verification in Consolidation
   Report. If the source folder is absent, note that in the final summary.
   An instruction to ignore Working Files excludes its contents from processing,
   not from delivery packaging, unless the user explicitly excludes copying too.
   This is a packaging step; never execute its scripts or discover imports there.
8. Hand the saved pair and native run evidence to comparison. `completed_with_skips`
   is a usable partial result and must be reported as such, not as full completion.

## Corrections and repeatability

Registered imports are immutable. Use new filenames for corrections and the
delivery-discard/rebuild workflow with a reason when replacing/removing a batch.
Keep historical evidence. Never restore whole tables or edit output cells by ad hoc
SQL. Changed output fingerprints cannot be certified as traced without a valid
rebuild. Reruns start from the recorded base and accepted ordered sources.

## Commands and outputs

```powershell
.\ifs.ps1 delivery-prepare --base 'BASE RELEASE' --delivery 'NEW DELIVERY'
.\ifs.ps1 delivery-merge --delivery 'NEW DELIVERY'
```

Outputs: database pair, copied/preserved Working Files folder, change log, delivery.json, run.json, frozen imports,
processed_tables.csv, provenance.db and generated comparison evidence. Native
comparison generation remains inside delivery-merge for correctness; workflow 4
owns interpreting and following up on that evidence. Logical ownership does not
require duplicating the existing report generator.

Implementation: `src/ifs_pipeline/delivery.py` and `delivery_core.py`. The next
integration improvement is a formal link from validation handoffs to batches;
the current CLI does not enforce the new handoff schema automatically.

“Use merger for [selected imports/evidence] against [base], producing [delivery].”

For explicitly authorized obsolete baseline names, use the scoped name-only
baseline policy documented in [delivery workflow](../../docs/delivery-workflow.md#explicit-baseline-country-name-modernization).
Preserve its mapping evidence and original base; never replace codes or values.

## Final IFsDataImport contents

Package only successfully applied tables and their matching DataDict rows in each
output import database. Applied-but-unchanged tables still qualify. Omit a file if
none of its tables applied, and omit excluded batches. Preserve the full input in
Consolidation Report/imports for replay and investigation; never trim external
originals or frozen evidence. The native pipeline maintains separate source and
packaged fingerprints, verifies retained contents, and publishes packages with the
database pair. See `import-packages.json` in the run for scope and omission evidence.
Registered archives remain immutable; their generated delivery copies are projections,
not replacement source evidence. This policy applies when a delivery is next merged
or rebuilt, not merely when its workflow instructions are edited.
