# Delivery-folder workflow

Installed use supports explicit folder paths and configurable relative-name defaults;
see [installation and paths](user-guide/installation.md). The D:/IFsHistSeries default
described below is retained by this checkout's local configuration, not hard-coded
into the installed processing engine.

These commands implement [workflow 3, merger](../workflows/merger/WORKFLOW.md) and
provide evidence for [workflow 4, comparison](../workflows/comparison/WORKFLOW.md).
The [four-workflow guide](../workflows/README.md) separates raw preparation and
import validation from consolidation. Reports may be generated during a merge;
comparison owns their interpretation and issue follow-up.

Use this workflow for prepared IFs database deliveries. It implements:
copy the base pair, collect imports, merge values, write the log, compare, and
optionally discard an import batch. No pre-merge decision templates are required.

## Prepare

~~~powershell
.\ifs.ps1 delivery-prepare --base 'IFsHistSeries 8.67 20260527' --delivery 'IFsHistSeries 8.68 20260915'
~~~

Folder names resolve under D:\IFsHistSeries. Absolute paths are also accepted.
The command copies IFsHistSeries.db and DataDict.db, creates IFsDataImport and
Working Files, and records the base identity. Existing working/import folders and
other files are preserved. Existing destination database files are never silently
overwritten. Previous-release imports, raw files and logs are not copied from the
base. The base folder remains unchanged.

The final delivery must also include the **current update's Working Files folder**,
copied with all files and nested folders unchanged. A supplied folder named Working
File serves the same role; place its contents under the final Working Files folder.
This copy is a separate packaging step: `delivery-prepare` creates the destination
folder but does not automatically discover or copy an external working folder.
Use the supplied update's folder, not the previous-release base's raw files. If
consolidation happens in the supplied delivery, keep its existing folder in place.
Reuse identical destination files; resolve differing same-path files without silent
overwrites. Verify relative paths and file fingerprints and record the copy's source,
destination, file count and verification in Consolidation Report. Note an absent
source working folder in the summary rather than implying an empty folder was copied.

Ignoring Working Files during validation/consolidation means not processing its
contents; it does not omit the folder from the final package unless explicitly
requested. Do not execute scripts or import databases from this archival copy.

The country reference comes from the base's SeriesPopulation table, normally 188
countries. A different reference can be named with --country-table.

Put raw data and conversion work in Working Files. Put completed IFsDataImport*.db
files directly inside IFsDataImport. Each import must contain DataDict and its Series
tables. Only that folder's matching database files are discovered; Working Files,
other file types and nested folders are not imported.

## Consolidate and report

Before first registration, run [format repair](format-repair.md) for supplied imports
with routine structural/formatting problems. It stages corrected versions and
evidence without changing original files or observations. Select corrected versions
instead of their originals, retain preparation evidence, and then merge. Previously
registered imports require new correction filenames and the existing discard/order
workflow. `delivery-merge` itself remains strict; it does not silently repair sources.

~~~powershell
.\ifs.ps1 delivery-merge --delivery 'IFsHistSeries 8.68 20260915'
~~~

All accepted files are merged cumulatively. The resulting database pair is saved
at the delivery's top level. The command automatically creates:

- Change Log YYYYMMDD.txt at the delivery root: brief official release summary,
  with counts of distinct tables created or updated by data source.
- Consolidation Report/Change Log YYYYMMDD.txt: detailed source/batch/table log,
  retaining observation and metadata counts, skipped reasons and exclusions.
- Consolidation Report/report.md, the current comparison summary.
- Consolidation Report/runs/RUN_ID/, the detailed evidence for this execution.
- Consolidation Report/delivery.json, the recorded base, batches, order and status.
- Consolidation Report/imports/, immutable snapshots of the supplied imports.

The user does not need to manage internal run directories. They contain work copies
and provenance used for verification and recovery. The delivery database pair is
updated only after the build passes exact-source and SQLite-integrity checks.
A small recovery record protects against interruption between saving the two files.
No release/promote command is required.

### Two change-log versions

The official log groups by the final DataDict `Source` text, not import filenames.
Tables absent from the original base are **created**. Existing tables with final
observation or metadata differences are **updated**, including metadata-only changes.
Unchanged tables, reverted intermediate changes and skipped entries do not count.
Each changed table counts once; multiple Source labels on one table are shown as a
combined label. The brief log notes partial application without listing failure details.
The detailed log retains all applied/skipped outcomes and responsible batch IDs.

The official log is plain UTF-8 text containing a **MediaWiki wikitext table**,
not a Markdown table. Copy the table from `{| class="wikitable"` through `|}`
(or the entire log) into the wiki's **Edit source** mode, then use Show preview.
Keep the original line breaks; do not add Markdown code fences or paste it as
literal text into VisualEditor. Headers use `!` / `!!`, rows use `|-`, and cells
use `|` / `||`. Source labels are escaped as literal text, preserving their displayed
characters without allowing embedded table, template, link or HTML markup.
Reference: [MediaWiki Help:Tables](https://www.mediawiki.org/wiki/Help:Tables).
The detailed processing log and analytical Markdown reports retain their formats.

New runs freeze `change_log.txt` (detailed), `release_log.txt` (brief), and
`release_tables.csv` (one counted table, classification, source and contributing
batches per row). Both user-facing copies are published with the database pair.

To apply the current log format to an already consolidated delivery:

~~~powershell
.\ifs.ps1 delivery-logs --delivery 'IFsHistSeries 8.74 20260918'
~~~

This command verifies the recorded output, base, archived sources and run evidence,
then writes both logs without importing files or rebuilding the database pair.
It preserves previous user-facing logs and new log artifacts/fingerprints under
`Consolidation Report/logs/ID/`. Existing `delivery.json`, native run files and their
fingerprints remain unchanged. `delivery-compare` continues to verify that original
run; it does not revert refreshed logs. The log date follows the release filename,
not the date of a formatting refresh. A delivery with skips retains that status.

Each `delivery-merge` invocation rebuilds from the base and recorded accepted imports. This makes
reruns predictable and prevents double application. The current version favors
reproducibility over speed and disk usage: each run retains its full work pair
until requested janitor cleanup removes verified redundancy.
Do not run it while the baseline, imports or delivery databases are being edited.

## Merge rules

For each table, country (or country pair) and year:

- A non-null incoming observation supplies the new value.
- A missing incoming observation preserves the previous value.
- Baseline-only countries and years remain.
- Zero is an observation.
- Earliest and MostRecent copy the first and last existing non-null observations.
- Metadata comes from the import, retaining baseline fields absent from that import.
  Explicit incoming metadata NULLs remain NULL. No numeric defaults, generated
  Last IFs Update dates, or rewritten Years strings are inserted into DataDict.

No interpolation, averaging, arithmetic correction, unit scaling, country reassignment,
numeric sentinel conversion or manual SQL number edits belong in this workflow.
Corrections must arrive in a revised formatted import under a new filename.

SQL NULL, empty string and whitespace are treated as missing and recorded separately.
Other values must already be stored as finite SQLite numbers. Numeric text that
SQLite still stores as text, unknown tokens, NaN/infinity and BLOB observations are
rejected. The pipeline does not guess whether -999 means missing: a stored -999 is
an observation. Resolve source-specific sentinels in the formatted import.

If the required IFs DOUBLE representation would round a large integer, the table is
skipped instead of changing the number. Normal numeric storage changes such as
integer 5 becoming real 5.0 preserve the exact numerical value.

Basic structural failures skip the affected table and are recorded: invalid identities,
duplicate keys, incompatible units, missing metadata and unsupported schema features.
Other valid tables continue. Country coverage gaps and statistical anomalies are
reported without a pre-merge approval step. There is no automatic outlier correction
or reversion.

A skipped table and its metadata remain at their previously accepted values. Skipped
table outcomes are retained on later rebuilds; discarding another batch cannot
silently activate a previously rejected table. Submit corrected tables in a new
formatted import. A wholly invalid import is recorded as a skipped batch.

## Multiple files updating the same table

The first run stops if newly discovered files overlap on a table without recorded
precedence. Supply all active filenames in the intended first-to-last order:

~~~powershell
.\ifs.ps1 delivery-merge --delivery 'IFsHistSeries 8.68 20260915' --order 'IFsDataImport_A.db' 'IFsDataImport_B.db'
~~~

Later non-null values win; later NULLs retain earlier values. The order is remembered.
Unrelated files can be discovered in filename order because they do not compete
for the same tables. Identical files with different names are rejected as duplicates.

Registered imports are immutable. A changed registered file blocks merging; restore
it and supply a correction under a new filename. Removing a file from IFsDataImport
does not discard it: replay uses its frozen snapshot. Use delivery-discard explicitly.

## Trace each change

One formatted import file is one batch. Its batch ID, filename and SHA-256 fingerprint
are stored in delivery.json and each run.json. The base is identified in the same way.
Fingerprints detect accidental changes; they are not signatures or access controls.

| Evidence | Purpose |
|---|---|
| report.md | Overall result, batches, finding counts and first issues. |
| processed_tables.csv | Applied/skipped tables, reasons, added/revised observations and metadata-change counts. |
| processing.jsonl | Events written as each table/import finishes, useful after a failure. |
| changes.csv | Final observation differences from the original base, with winning batch IDs. |
| coverage.csv | Base versus delivery non-null coverage by table/year. |
| incoming_missing.csv | Original SQL NULL/blank/whitespace cells before backfill. |
| findings.csv | Incoming and final anomaly flags, country coverage issues and skipped-table reasons. |
| metadata_changes.csv | Final DataDict differences from the original base, with batch IDs. |
| table_summary.csv | Distinct processed tables and coverage/change counts. |
| provenance.db | Every supplied non-null cell, prior value, winning batch and metadata change events. |
| run.json | Base, ordered batches, fingerprints, exclusions, outcomes and screening settings. |

The provenance database has events, origins, metadata_events and metadata_result
tables. Country identity in the key field is a JSON array of Country/FIPS_CODE, or
Actor/Actor_FIPS/Partner/Partner_FIPS. Even an incoming value identical to the current
value is recorded. A retained cell without an import origin comes from the base.

Verification independently rereads the base and archived imports, reconstructs
precedence, checks every affected output cell and metadata row, and checks SQLite
integrity. A number that cannot be explained by those sources prevents publication.

## Comparison and screening

The report is generated after every merge or discard. Incoming missingness and
anomalies are also retained because backfill can otherwise conceal source problems.

To reverify the delivery and its evidence and restore the current report copy:

~~~powershell
.\ifs.ps1 delivery-compare --delivery 'IFsHistSeries 8.68 20260915'
~~~

This checks the unchanged database pair and archived sources against the last run;
it does not import newly added files. Externally modified delivery databases are
not overwritten or certified. The report compares against the original base,
not only the preceding import.

Default screens cover 50% jumps/revisions, annual gaps, six-observation flat runs,
missing countries, possible scale changes, country-trajectory misplacement, and
year shifts. These identify investigation leads, not proven errors. Bounds and
component-sum relationships require explicit source-appropriate settings.

An optional JSON file passed as --validation contains the validation object described
in validation-workflow.md (defaults, tables and sum_rules), without an outer
"validation" wrapper. For example:

~~~json
{
  "defaults": {"frequency_years": 1, "jump_fraction": 0.5},
  "tables": {
    "SeriesExamplePercent": {"min_value": 0, "max_value": 100},
    "SeriesExampleFiveYear": {"frequency_years": 5}
  }
}
~~~

These settings affect reporting only. They cannot authorize value transformations,
unit changes or wholesale table replacement. The configuration is recorded with
the delivery and can be changed on a later merge. No sum relationship is inferred.

Metadata is faithfully copied even if its Years string differs from final merged
coverage; use the coverage evidence to interpret it. Unrelated baseline tables are
preserved, and are not comprehensively screened for statistical/semantic issues.

## Discard a batch

~~~powershell
.\ifs.ps1 delivery-discard --delivery 'IFsHistSeries 8.68 20260915' --batch 'IFsDataImport_A.db' --reason 'Incorrect source release'
~~~

A registered batch ID can replace the filename. The operation marks the batch
excluded and rebuilds from the unchanged base plus the remaining accepted imports
in their recorded order. It updates both databases, the log and the report.
Source files and prior run evidence remain.

If a later batch still supplies an observation, that value survives. Otherwise the
value returns to an earlier accepted import or the base. A new table supplied only
by the discarded batch disappears, along with its corresponding new metadata.
Discarding the last active batch restores the original database pair.

## Status and recovery

- prepared: copies exist, no consolidation performed.
- completed: all selected tables processed; statistical findings may still need review.
- completed_with_skips: a delivery was produced, but some tables/imports were rejected.
  The CLI exits with code 1 so skipped work is visible.
- error: the command failed. Existing delivery output remains unchanged unless
  publication was interrupted; pending.json records how to finish that save.

After an ordinary exception the lock is released. A forcibly terminated process may
leave Consolidation Report/.lock. Verify that no process is working on this delivery
before removing that lock. Then delivery-compare or delivery-merge will recover a
pending publication automatically from verified work copies. Do not manually repair
one member of a database pair. Back up the delivery folder and its base together.

## Testing boundary

Tests use isolated synthetic databases, including exact-value provenance, metadata,
overlap ordering, retained history, batch discard, skipped tables and interrupted
publication. Actual release testing remains a separate step using the user's supplied
base and import files. No real source has been consolidated by this implementation.

## Explicit baseline country-name modernization

When the user confirms that specific base tables use obsolete country labels,
`delivery-merge --normalize-base-names TABLE [TABLE ...]` enables a name-only
baseline view for those tables using the pinned canonical country roster. The
original base is unchanged. Codes and all numerical cells are preserved exactly;
unknown codes, canonical names attached to a different code, and duplicates fail.
This is not fuzzy identity recovery or a geographic transformation.

Each run records old name, code and canonical name in `baseline_names.json`.
Independent origin verification and comparison reconstruct the same view from the
original base and validate that evidence. The policy persists on replay. Only
explicitly selected prior skips with the exact country-identity-conflict reason
are retried; other skips remain unchanged. Applied tables receive canonical names
in the final delivery, with baseline-only values still traced to the original base.

## Applied-table import packaging

After a successful merge, the delivery's IFsDataImport folder is a release package:
- Each registered .db contains only tables with an `applied` outcome for that batch,
  together with their matching DataDict rows. Applied but numerically unchanged
  tables are included; skipped series and unmatched metadata rows are excluded.
- Files with zero applied tables, and excluded batches, are omitted from that folder.
- Complete input files remain unchanged in Consolidation Report/imports, including
  skipped tables and their metadata. The external supplied originals remain intact.
  Investigation and replay use these frozen archives, never a merely trimmed copy.
- `runs/<run>/import-packages.json` records source and packaged fingerprints,
  included/skipped table scope, removed auxiliary tables and omissions. Table schemas,
  retained observations and retained metadata are verified against the archive.
  Packaging does not replace source metadata with final DataDict metadata, or pull
  retained base observations into the import copies.
- Publication is recoverable with the database pair. Replay recognizes registered
  delivery copies by their separate package fingerprints and does not register them
  as new inputs. Comparison checks both archives and published packages. Modified
  packages are rejected; use a new filename for a corrected import.

This applies to subsequent merges/rebuilds. Updating workflow code alone does not
retroactively trim an existing delivery. Historical run evidence remains unchanged.
Unregistered files are not deleted by packaging; keep non-import material in Working
Files and supply only the intended formatted imports for a release.

## On-demand janitor

Use the [janitor role](../workflows/janitor/WORKFLOW.md) only when cleanup is requested.
`delivery-clean --delivery 'DELIVERY'` previews candidates; add `--execute` to clean.
No release review or approval record is required. Only verified redundant working
pairs under Consolidation Report/runs/*/work are removed. Reports, provenance,
frozen imports, preparation evidence, required staged packages and unknown files
stay intact. A small cleanup log records paths, fingerprints and bytes removed.
Comparison and replay continue working without a restoration step.

Review/approval/archive creation commands are retired. `delivery-archive-verify`
and `delivery-restore` remain solely to recover evidence archived by the old system.
Historical records remain intact. The janitor does not create new archives.
