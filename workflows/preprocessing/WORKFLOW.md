# Workflow 1 — Data preprocessing

For installed use, obtain the source-library path from `ifs-data paths` or the
user's explicit instructions. Local D: paths in these runbooks describe the
repository owner's setup. Missing path configuration is not permission to create
a replacement library. Source-specific scripts still require the checkout.

## Mission and starting point

Turn original source material into IFsDataImport databases with complete,
applicable DataDict metadata. Inputs are raw CSV, Excel, PDF or other source files;
source release/documentation; intended series and units; and the relevant IFs
base/reference. Actual format support depends on the source adapter.

Use this workflow for extraction, indicator selection, source-specific unit
conversion, geographic processing with explicit evidence, and explicitly requested
raw-source repulls. User-provided files are the sole source of new observations
and source documentation unless the user explicitly authorizes online fetching.
Do not search the web, call APIs, follow source URLs or download comparison files
just because supplied data have errors or incomplete metadata. Local IFs references
and pinned country mappings remain usable. A repull recommendation alone is not
authorization to fetch.
Already formatted imports normally start at validation.

When validation returns `preprocessing-issues.json`, use its source/table/reason
and the frozen originals as the rework queue. Recover country identities, intended
DataDict links and unit meaning from preparation/source evidence, then produce a
new import and return it to validation. A formatting failure does not automatically
require a new download. Preserve task exclusions such as ignoring Working Files.

Read [import preparation](../../docs/import-preparation.md),
[database guide](../../docs/database-guide.md), [recipes](../../docs/recipes.md),
[country concordance](../../docs/country-concordance.md), and the
[shared contract](../shared/HANDOFF.md). Find source-specific guidance through
the [source-guide index](../../source-guides/README.md).

## Intake and source-guide maintenance

Start from four inputs: the raw source folder (also the default parent of the
output folder), its DataDict workbook specifying target indicators, the reference
base folder, and any source-specific formatting instructions or guide. Inspect
what has already been supplied before asking for missing information. A missing
guide does not prevent supported work; create it as the source is understood.

Read `D:\IFs Source Library\README.md`, then the matching
`<Source>\SOURCE_GUIDE.md` and any supplied `SOURCE_GUIDE.md` before
choosing an adapter or mapping indicators. Confirm release/layout applicability;
do not assume old column positions or exceptions still apply. Follow the
[guide-maintenance procedure](../../source-guides/README.md): create a missing
`D:\IFs Source Library\<Source>\SOURCE_GUIDE.md` from the library template,
or update the existing library guide for
new evidence, user corrections and verified methodological changes. Add new
sources and aliases to the library index and the repository pointer index. Keep
unresolved interpretations labeled as such. The library is authoritative; do not
create duplicate maintained guides in the repository. If it is unavailable, report
the missing library rather than silently creating a replacement. Resolve write
permissions at the intended library path through the normal permission mechanism.

Routine evidence-backed guide updates are part of the authorized preprocessing
task. Preserve the distinction between general methodology, release-specific
exceptions, explicit user instructions and inference. Include dated changes and
their evidence; do not imply that the user reviewed every model-derived rule.
Update a recipe/adapter only when supported and test changed behavior with
synthetic fixtures before applying it within the authorized task scope.

Wiki exports or supplied Markdown can be summarized locally. An explicit request
to use/read a wiki page permits reading that documentation only; it does not
permit fetching datasets or publishing wiki changes. A citation alone does not
authorize access. Record page title, known revision/date and the evidence used.

## Procedure

1. Establish the authorized source/release and scope from the user's files. A
   DataDict-formatted Excel workbook normally lists the IFs tables/indicators to
   find in the raw source. Use its old metadata as clues; update outdated details.
   Record each Table/Variable, matched raw sheet/column/code and variant, mapping
   evidence and outcome. Report unmatched indicators rather than silently omitting
   them or adding unrelated ones. Establish historical/forecast scope and base.
   Default output to a fresh versioned subfolder of the supplied source folder.
   Snapshot original inputs before creating output and exclude generated packages
   from source discovery/copying. Freeze the original files.
2. Inspect the format and grain. For spreadsheets identify sheets/header rows; for
   PDFs establish extraction coordinates/page references and visually check the
   extraction using the applicable document tools. Preserve original country labels.
3. Select or build a reproducible source-specific adapter. Use reviewed DataGator
   identity mappings; do not infer aggregation, territorial allocations or scenarios.
4. Record input/stored units, price basis, currency, denominator, missing tokens,
   transformations and exclusions. Use the supplied base read-only to resolve
   missing or unclear meaning. Compare matching metadata, source labels/formats
   and overlapping observations, then make and document the best supported
   inference. For example, a missing Units cell may be resolved by the matching
   base entry plus raw percentage formatting. The base alone does not establish
   the new raw scale; magnitude similarity alone does not justify rescaling.
   Clear current source evidence takes precedence over older base descriptions.
   Record the conclusion, supporting evidence, alternatives and uncertainty in
   the external review. Ask only if materially different interpretations remain
   unsupported; continue independent work. Never fill raw gaps from base values.
5. Review every DataDict field for applicability and evidence. Distinguish source
   facts from IFs configuration; distinguish intentional blanks from unresolved
   fields. Use related accepted tables as evidence, not automatic global defaults.
6. Generate a new IFsDataImport file with DataDict and its series. Keep source
   filenames, code, mappings and preparation lineage outside DataDict fields.
7. Reread the saved database. Check extraction against source, transformation
   reproducibility, observation keys, coverage, metadata and SQLite integrity.
8. Finish the source-guide review. Create/update the maintained page when warranted,
   or record `reviewed_no_change`. Freeze the version actually used in the run's
   evidence; if updated during the run, preserve both versions and identify the
   changes applied. Record guide paths, fingerprints, applicability and open issues
   in the handoff. Do not alter earlier run packages to add new guidance.
9. Send the output and evidence to validation. Route unresolved source issues and
   any repull recommendation explicitly; do not merge here.

## Outputs and completion

Deliver a new prepared import, reproducible adapter/recipe, frozen source evidence,
DataDict field review, coverage summary and handoff. `ready` means the preparation
has been checked, not that validation or consolidation already happened. State
unresolved metadata or extraction uncertainty at Table/Variable level.
Report the guide maintenance outcome and link the maintained source guide. Its
used snapshot normally belongs in `Working Files/source-guides/` within the new
output. The reusable guide stays in `D:\IFs Source Library`; raw data and run audits stay
with the user's output package. A source-guide update alone is not a validation.

By default create `<source folder>/IFsDataImport/`, containing formatted `.db`
files directly alongside `Working Files/` for evidence/code/frozen inputs and
`Preparation Report.md`. Do not nest another `IFsDataImport` folder inside it.
Use `IFsDataImport_02`, `_03`, etc. when needed to preserve existing folders.
Do not place outputs beside the base case merely because its path was provided.
Honor an explicit output/delivery location and requests to ignore Working Files.
Resolve filesystem write permissions for the intended location instead of silently
choosing another folder. Avoid recursive self-copying when output is under source,
and exclude earlier generated packages from discovery. Never overwrite originals
or an existing package. Report the exact output location and inference decisions.

## Existing implementation and improvement points

- Source-guide discovery, interpretation and maintenance are steps performed by
  the model following this runbook. `workflow-init` and standalone preparation
  scripts do not author guide content or establish semantic correctness.
- `inspect` reads CSV/SQLite structure; `concordance` handles source-bound CSV
  country mapping. Its configured baseline must match the intended reference.
- `scripts/prepare_sdebt.py` is a source-specific wide-CSV adapter with reviewed
  metadata. Do not generalize its settings to another source.
- `scripts/prepare_ictd.py` prepares the scoped UNU-WIDER GRD 2025 Excel source
  using `recipes/ictd-2025.metadata.json`, the supplied old DataDict table list
  and an explicit base. The profile is fingerprint-bound and includes reviewed
  country identities, fraction-to-percent conversion and 24 specifically authorized
  Excel-error missing values. It is not a general policy for other GRD downloads.
- `scripts/prepare_climatewatch.py` prepares the supplied January 2026 Climate
  Watch historical-emissions CSV using `recipes/climatewatch-2026.metadata.json`
  and an explicit base and source guide. The 57-table scope is fingerprint-bound.
  Country codes are linked to names in the supplied nested GDP/NDC files before
  pinned DataGator name matching; source MNG must not be joined directly to IFs MNG.
  It preserves numeric values, audits NULL padding/exclusions, snapshots its
  dependencies and guide, and independently checks saved source-keyed cells.
  Review the ClimateWatch library guide's unit-scale inference and AR5-vintage
  caveats before applying the recipe to any other release.
- `src/ifs_pipeline/adapters.py` contains legacy CSV helpers. Legacy ingest/run
  may also merge or generate metadata defaults; do not use them to bypass this
  workflow's metadata review or the separate delivery merger.
- General Excel/PDF adapters and additional source recipes are future stage work.
  Do not describe them as available simply because the workflow accepts those tasks.

## Starting request

“Use preprocessing for the files in [source folder], targeting the indicators in
[DataDict workbook], with [base] as the reference for unclear metadata. Prepare
imports and evidence inside the source folder. Use supplied files only.”

The user may omit the last two sentences: these are defaults. Online fetching and
an alternative output directory require explicit instructions for those actions.
