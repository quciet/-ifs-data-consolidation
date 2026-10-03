# IFs data repository instructions

This workspace is the durable resource repository for IFs data updates. Read README.md
for commands, docs/processing-rules.md for IFs conventions, and docs/recipes.md when
onboarding a source. Use the existing notebooks as methodological references; some
contain hard-coded paths and destructive writes, so do not execute them wholesale.

## Four-workflow architecture

Read workflows/README.md and the relevant stage runbook before stage work:

- workflows/preprocessing/WORKFLOW.md: raw source extraction, source-specific
  transformations, prepared imports and complete applicable DataDict metadata.
- workflows/validation/WORKFLOW.md: import formatting/metadata normalization,
  verification, source screening and evidence-based repull recommendations.
- workflows/merger/WORKFLOW.md: exact-value consolidation of selected imports.
- workflows/comparison/WORKFLOW.md: verification and comparison of updated tables
  and metadata using base, incoming and final evidence; route findings upstream.

Use workflows/shared/HANDOFF.md to record artifacts/fingerprints, table outcomes,
coverage and issues. Existing native pipeline manifests remain authoritative for
their operations; a handoff template is not proof of validation. `workflows` lists
the entry points and `workflow-init` only scaffolds an empty stage workspace.
No background agent framework or separate Codex tasks are created automatically.

An instruction to improve a workflow changes its instructions/code and uses
synthetic tests; it does not authorize applying those changes to prior real inputs.
For actual work, enter at the appropriate stage and continue through the requested
scope. Preserve explicit exclusions, including a request to ignore Working Files.

The approved validation design includes unambiguous numeric-text conversion,
infinity-to-NULL with original-token evidence, and retention of the baseline's
unit wording when semantic equivalence is established. These belong in new imports
before consolidation. The repair command implements strict decimal/infinity and
schema/storage normalization plus case/whitespace unit equivalence; broader meaning
and unresolved links/identities return to preprocessing. Read docs/schema-contract.md
and follow the runbook's capability list. Never relax merge-time exact-value checks. Keep
routine supported repairs autonomous; investigate unresolved meaning and report
partial results without a blanket approval gate.

## Default workflow: versioned delivery folder

Read docs/delivery-workflow.md. User instructions on 2026-09-15 supersede the older
pre-merge approval workflow for delivery consolidation.

- User supplies a base case and delivery folder. Relative names resolve under
  D:\IFsHistSeries. Use delivery-prepare to copy only IFsHistSeries.db/DataDict.db
  and create IFsDataImport and Working Files. Never overwrite an existing database
  pair without resolving its ownership; never modify the base case.
- Working Files holds raw data and preparation code. Consolidation reads only
  formatted IFsDataImport*.db files directly inside the delivery's IFsDataImport.
  A raw-data correction must be reproducibly prepared as a new formatted import.
- Final delivery packaging must also copy the current supplied update's Working
  Files folder (or Working File if so named), including all nested contents unchanged,
  into the final Working Files folder. Preserve it in place when already there.
  Do not substitute the base release's working folder, execute its scripts or import
  its databases. Ignoring Working Files means ignoring it for processing, not omitting
  the copy, unless the user explicitly says so. Preserve existing destination files,
  resolve differing same-path files without silent overwrites, verify copied paths
  and fingerprints, and note the copy or missing source folder in the final report.
- Run delivery-merge to merge, log and compare. No per-table decision templates,
  separate promote command or blanket pre-merge approval is required.
- Produce two change logs: the brief official `Change Log YYYYMMDD.txt` at the
  delivery root counts distinct tables created/updated by final DataDict Source;
  keep the detailed source/batch/table log under Consolidation Report with the
  same filename. Exclude unchanged/skipped tables from release counts; include
  metadata-only updates. Use `delivery-logs` to refresh an existing delivery's
  logs from verified evidence without remerging or changing historical manifests.
  The official source-count table must use native MediaWiki wikitext (`{|`, `!`,
  `|-`, `|`, `|}`), ready to paste into MediaWiki's Edit source mode. Never output
  a Markdown pipe table or enclosing code fence in the release log. Escape source
  labels as literal cell text without changing the underlying metadata.
- Before skipping for routine formatting defects, use docs/format-repair.md and
  repair-imports to stage audited corrections. Repair supported identities, NULL
  roster padding, empty padding rows, endpoints and blank cells without changing
  observations. Run repair-verify before selecting corrected files. Preserve the
  original imports and complete preparation evidence; never overwrite a registered
  batch. Unresolved identity/geography, duplicates, metadata and units still need
  source-specific investigation. This preparation step needs no blanket approval.
- Only structural problems that prevent a meaningful merge skip a table. Record
  those skips, continue valid tables, and report completed_with_skips candidly.
  Statistical anomalies are flagged for review after consolidation.
- Newly overlapping import tables require explicit source precedence. Record the
  order via --order; never guess which competing source should win.
- Read the delivery's Consolidation Report/report.md and detailed CSVs. Report
  observations added/revised, metadata changes, missingness, anomalies and skips,
  with the responsible batch identifiers. A completed run may still have anomalies.
- Use delivery-discard with a registered batch ID/filename and reason when asked
  to remove a batch. Rebuild from the base and remaining accepted imports. Keep
  source files and historical evidence. Do not restore whole tables by ad hoc SQL
  because that may erase valid changes from other batches.
- delivery-compare rechecks output/source/evidence fingerprints and exact origins;
  it does not import new files. Never certify externally modified output as traced.
- Existing ingest/validate/run/promote commands are legacy compatibility tools,
  not the default for a delivery. Their older defaulting/normalization rules must
  not be used to bypass the delivery's exact-value rules.

## Exact-value invariants

These are consolidation invariants. Raw transformations belong to preprocessing;
declared representation/missing-value normalization belongs to validation, with
original evidence and new import versions. Neither happens inside the merger.

- The model must never invent or manually alter numerical observations in an IFs
  database. Every new/updated observation must be copied from a frozen formatted
  import, identified by file fingerprint, table, country/pair and year.
- Retained values come from the base or a preceding accepted import. No arithmetic
  repair, imputation, averaging, unit scaling, numeric sentinel conversion or
  arbitrary SQL value fixes during consolidation. Corrections belong in new imports.
- Zero is a value. SQL NULL, blanks and whitespace are missing with external audit
  evidence. Other incoming values must already be finite stored numbers.
- Earliest/MostRecent only copy first/last existing non-null observation VALUES.
  Numeric DataDict fields receive no invented defaults. Copy source metadata or
  retain baseline fields absent from source; dates/year-span reports stay external.
- Keep source/base files unchanged and preserve unrelated tables/metadata.
  Use the tested pipeline, never hard-coded destructive notebooks.
- Batch evidence, ordered replay, value verification and report files live outside
  the IFs database schema in Consolidation Report. Back them up with the delivery.
- Do not add a cloud service, API key, watcher, publication or agent framework
  unless requested. Do not execute real-data consolidation until actual inputs
  and the intended base/delivery have been provided.

## Development

The core uses Python 3.10+ standard library only. `ifs.ps1` uses the bundled Codex
Python when present, then a system Python. Alternative: `python ifs.py ...`.
Run `python -m unittest discover -s tests -v` with a working Python executable.
Tests use isolated temporary databases. Keep raw data, run artifacts, local config
and large SQLite files out of Git. Existing notebooks and documents remain intact.

## Raw-data preprocessing defaults — 2026-09-22

- Maintain source-specific knowledge in `D:\IFs Source Library\<Source>\SOURCE_GUIDE.md`;
  start with `D:\IFs Source Library\README.md` (repository pointer: `source-guides/README.md`)
  and look up the current source before preprocessing. The library is authoritative;
  do not maintain duplicate repository guides. Read any supplied source
  guide too. Create a guide if absent and update it as the current task establishes
  supported changes. This is part of model-led preprocessing, not a separate
  approval step or automatic behavior of standalone CLI scripts. Keep release
  exceptions, inferred conclusions and unresolved questions distinct; retain
  evidence and dated change notes. Snapshot the guide used with each run. Do not
  turn one-run corrections into universal rules or rewrite historical evidence.
  Wiki summaries require provenance; only explicitly requested page access may
  fetch documentation, and that does not authorize dataset downloads or publishing.
- Treat user-provided files as the sole source of new observations and source
  documentation. Do not browse, call online APIs or download comparison/replacement
  files unless the user explicitly requests online fetching. A URL in a workbook,
  missing metadata, or a source error does not authorize fetching. Existing local
  IFs rules and pinned mappings remain available as reference material.
- A supplied DataDict-formatted Excel file primarily identifies the requested IFs
  tables/indicators. Find their counterparts in the raw files. Older definitions,
  dates and source details are reference clues to update, not immutable metadata.
  Record a mapping or an unresolved outcome for every requested Table/Variable;
  do not silently expand the scope to unrelated raw indicators.
- Use the supplied base case as a read-only reference for inference, including
  missing units, meanings, source-column variants and IFs settings. Make the best
  supported inference from local source labels/formats and matching base metadata
  and values; record evidence and uncertainty. Do not let the old base override
  clear new source evidence, assume a conversion from magnitudes alone, or use
  baseline observations to fill a newly prepared import's gaps.
- Default preparation outputs to `IFsDataImport` inside the user's raw source
  folder, with formatted `.db` files directly alongside `Working Files` and
  `Preparation Report.md`; never nest another IFsDataImport folder. If the folder
  exists, use `IFsDataImport_02`, `_03`, etc. to preserve existing packages. An explicit
  user-selected output location overrides this default. The base path is not an
  output destination. Preserve originals, exclude generated output folders from
  raw-source discovery/copying, and never overwrite an existing package. Filesystem
  write restrictions must be resolved through the normal permission mechanism,
  not by silently relocating the output.

## Database interpretation and metadata review

For raw CSV-to-import preparation and DataDict completion, read
docs/import-preparation.md. Use user-reviewed, source-specific metadata profiles;
verify IFs configuration as well as exact observation copying. Keep preparation
provenance outside DataDict fields. Do not generalize a source's reviewed flags,
aggregation labels or display precision into global defaults.
For each prepared Table/Variable, review every DataDict field for applicability
and evidence. Check both omissions and unnecessary additions before delivery.
Distinguish intentionally blank from unresolved fields in external preparation
evidence; investigate gaps rather than treating an incomplete source note as the
full metadata specification. User corrections to one table do not establish either
mandatory values or mandatory blanks for other tables.

Read docs/database-guide.md before interpreting database contents or onboarding an unfamiliar series. It documents the relationship between the database pair, the distinction between source and stored units, and observed catalog mismatches. Treat its counts as a release-specific snapshot; preserve undocumented auxiliary tables and investigate unresolved semantics rather than guessing.


## Datagator country concordance

Reuse the user's DataGatorLite lookup and reviewed original_name,matched_name exports.
Read docs/country-concordance.md for the source-bound concordance command and evidence.
The pinned lookup and provenance are in reference/datagator/. Do not replace this with
a new name-matching app. Use the original country column and data, not only a replaced
CSV that has lost the original labels. Datagator's prefilled fuzzy suggestions require
actual identity review; do not pass --reviewed just because an export exists.
Copy every generated recipe_fields.json field into the source recipe before intake.
Distinct source labels converging on one target need identity/geography review.
A name mapping does not authorize adding, averaging, splitting or allocating values.
Territorial processing needs a source/indicator/year-specific rule and evidence;
current concordance performs no numeric transformation. No upstream or MongoDB writes
are part of this local workflow.

## Validation semantics

For monadic validation, once every canonical IFs entity (normally 188) is present
exactly once in the original source, exclude extra unlabeled/non-IFs rows even if
they contain observations. Preserve their full raw evidence and report excluded
row and populated-cell counts. Missing-entity padding does not establish this
precondition. Do not select between canonical duplicates, discard recognizable
identity conflicts, or apply the rule to dyadic data. If canonical entities are
missing, unresolved rows return to preprocessing for identity recovery. This is
an authorized validation-stage selection rule; merger exact-value rules are unchanged.

Report country rows separately from countries/years with observations. Preserve
SQL NULL, blank, whitespace and source-token evidence; zero is always a value.
Do not infer withdrawals, interpolation, outlier repairs or territorial arithmetic.
Tune screening thresholds to source meaning. Revalidate when input, master, baseline
or code changes. Inspect postmerge_issues.csv and old/incoming/result coverage;
A completed merge does not by itself settle findings introduced by the merge.

## Delivery import packaging — 2026-09-22

After consolidation, generated IFsDataImport delivery copies contain only that
batch's applied tables and matching DataDict rows; applied-but-unchanged tables
are included. Omit files with no applied tables and excluded batches. Full frozen
inputs remain unchanged in Consolidation Report/imports, with all skipped content
and evidence available for investigation and replay. Keep original and packaged
fingerprints distinct; use native publication and verification rather than ad hoc
file trimming. Do not treat a packaged copy as a new batch or overwrite its frozen
source archive. Workflow-only development uses synthetic tests and does not apply
this packaging retroactively to an existing real delivery without task authorization.

## On-demand janitor — 2026-10-01

The review and approval-based archival role is retired. Use
workflows/janitor/WORKFLOW.md only when the user requests cleanup of a delivery.
The cleanup request authorizes supported deletion; no release review or approval
record is required. `delivery-clean` previews; `--execute` removes only verified
redundant working database copies under Consolidation Report. Preserve native
reports, manifests, frozen inputs (including skipped/excluded content), provenance,
preparation evidence, staged packages required by verification, and unknown files.
Never clean real deliveries merely because the workflow is being developed.
Legacy archive verification/restoration remains available solely for recovery.
