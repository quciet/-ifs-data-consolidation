# IFs local data repository

For installation outside this checkout and configurable folder paths, see
[installation and paths](docs/user-guide/installation.md). No managed user workspace
is required. The [architecture plan](docs/development/architecture-plan.md) records
the incremental development scope.

Use the [four-workflow guide](workflows/README.md) to start or improve an IFs update.
Each workflow has its own instructions and can be used independently:

| Workflow | Responsibility |
|---|---|
| [Data preprocessing](workflows/preprocessing/WORKFLOW.md) | Raw sources to prepared imports and reviewed DataDict metadata |
| [Import validation and repair](workflows/validation/WORKFLOW.md) | Formatting/metadata normalization, verification and repull assessment |
| [Data merger](workflows/merger/WORKFLOW.md) | Exact-value consolidation of selected imports |
| [Post-merge comparison](workflows/comparison/WORKFLOW.md) | Updated-table and metadata comparison, anomaly investigation and issue routing |

```powershell
.\ifs.ps1 workflows
.\ifs.ps1 workflows validation
# Optional: create empty records for a stage; this does not process any data.
.\ifs.ps1 workflow-init --stage validation --output 'reviews\my-validation-task'
```

Raw data starts at preprocessing; formatted imports start at validation; an existing
delivery can start at comparison. Improving a workflow does not authorize running
it on previously supplied real data. The guide identifies existing commands and
future implementation work. Validation includes schema enforcement, numeric-text/
infinity normalization and case/whitespace unit label retention; semantic unit
questions return to preprocessing.

For raw preprocessing, supplied files are the sole source unless online fetching
is explicitly requested. A supplied DataDict Excel file identifies the indicators
to find; update its outdated metadata using the raw files and read-only inference
from the specified base. Record inferred units/meaning and uncertainty. Default
output to `IFsDataImport` inside the supplied source folder, with formatted `.db`
files directly alongside `Working Files` and `Preparation Report.md`. Use
`IFsDataImport_02`, `_03`, etc. if needed to preserve existing folders, unless the
user requests another location. The base folder is a reference,
not the default preparation destination.

Reusable source methodology lives in `D:\IFs Source Library`, with one folder
per source and a `SOURCE_GUIDE.md` inside. [source-guides](source-guides/README.md)
is the repository index pointing to that authoritative library.
The model reads the matching guide during preprocessing, creates it if absent,
and updates it when the work establishes changes. The first guide covers
[ICTD / UNU-WIDER GRD](source-guides/ictd.md). Each run keeps a snapshot of the
version used; source files and detailed audits stay with the output package.

Use a versioned delivery folder with `Working Files/` for preparation and
`IFsDataImport/` for selected formatted imports.
The repository stores the IFs rules, country mappings, recipes, reusable code, and
run evidence needed to repeat an update. Delivery processing produces an updated pair of
`IFsHistSeries.db` and `DataDict.db`, plus a validation and change report.

The original notebooks, handoff documents, schedule workbook, and coverage exports
are preserved. This first version adds a working processing pipeline around them.

Read the [database guide](docs/database-guide.md) for database roles, table relationships, field meanings, and a real population-series example.

## Delivery commands (merger and comparison)

Before registering new imports, use the [format-repair subworkflow](docs/format-repair.md)
to resolve routine roster, identity, endpoint and blank-cell defects as audited new
copies. Read its report and select the corrected versions for consolidation.
Originals and preparation evidence remain preserved; unresolved problems are reported.

Provide the base-case folder and the new delivery folder. Use these commands in
PowerShell from this repository; folder names resolve under D:\IFsHistSeries.

```powershell
.\ifs.ps1 delivery-prepare --base 'IFsHistSeries 8.67 20260527' --delivery 'IFsHistSeries 8.68 20260915'
# Put formatted IFsDataImport*.db files in the delivery's IFsDataImport folder.
# First inspect/repair new imports as described in docs/format-repair.md.
.\ifs.ps1 delivery-merge --delivery 'IFsHistSeries 8.68 20260915'
```

The delivery contains the copied and updated IFsHistSeries.db/DataDict.db pair,
IFsDataImport, Working Files, a brief dated release log and Consolidation Report.
Final packaging copies the supplied update's complete Working Files folder,
including nested contents, or preserves it in place when already in the delivery.
Its contents remain excluded from import processing. `delivery-prepare` creates
the folder; the merger workflow handles copying and verifying external contents.
The detailed change log lives inside Consolidation Report; the root log summarizes
distinct tables created or updated by data source. To refresh both logs on an
existing delivery without remerging, use `delivery-logs --delivery 'DELIVERY'`.
The official source-count table uses MediaWiki wikitext; paste it into the wiki's
Edit source mode with its line breaks intact. It is not a Markdown pipe table.
The original base and imports remain unchanged. Existing destination database
files are not silently overwritten by preparation.

Merging copies non-null observations from imports and retains old values where
incoming data is missing. No numerical corrections or transformations are performed.
Every supplied observation has batch provenance, independently verified against
the frozen source files. Metadata is copied without generated numeric defaults.

Review the automatically generated Consolidation Report/report.md and its detailed
evidence. Statistical flags do not require a pre-merge approval exercise. Structural
errors skip affected tables and are reported; other valid tables continue.
Overlapping imports need a recorded first-to-last order using --order.

```powershell
.\ifs.ps1 delivery-compare --delivery 'IFsHistSeries 8.68 20260915'
.\ifs.ps1 delivery-discard --delivery 'IFsHistSeries 8.68 20260915' --batch 'IFsDataImport_Source.db' --reason 'Incorrect source release'
```

Discarding a batch rebuilds from the base and remaining accepted imports, preserving
their order. No source files are deleted. Read the
[delivery workflow guide](docs/delivery-workflow.md) for all commands, reports and recovery.

No API key or third-party Python packages are required. ifs.ps1 uses the bundled
Python when available, otherwise a system Python. No folder watcher is installed.

Local statistical evidence is available through `quality-evidence` (explicit
base/import specification) and `delivery-quality` (verified recorded delivery scope).
Both write new read-only analysis packages with CSV/JSON evidence and an offline HTML
viewer. `quality-verify` checks the package; `quality-review` appends independent human
or AI review records. See [quality evidence](docs/quality-evidence.md). These commands
do not alter observations or historical consolidation manifests.

For on-demand cleanup, use the [janitor role](workflows/janitor/WORKFLOW.md).
It removes verified redundant work database copies under Consolidation Report.
It does not review or approve releases, compress evidence, or run automatically.
Use `delivery-clean --delivery 'DELIVERY'` to preview, or add `--execute` to clean.

## Legacy request workflow

The earlier ingest/validate/run/promote commands remain available for compatibility
and existing fixtures. They use separate repository run folders and, for prepared
SQLite imports, pre-merge decision templates. They are not required by the delivery
workflow. See [the legacy request instructions](docs/legacy-request-workflow.md).

## Country concordance with your Datagator tool

The repository now reuses DataGatorLite's 188-country lookup and accepts its reviewed
mapping CSV exports. Start with the original source CSV:

```powershell
.\ifs.ps1 concordance --input 'inbox\source.csv' --country-column 'Country' --source 'Source name'
```

Known names/codes/aliases are matched; unknown names and converging source entities
are reported for review. The original data values are unchanged. Ready output includes
`recipe_fields.json` to connect the mapping and its evidence to a source recipe.
See [the concordance guide](docs/country-concordance.md) for Datagator exports,
explicit exclusions and the separate rules required for territorial combinations/splits.

## Legacy raw CSV preparation

Copy `recipes/csv-long.template.json` to a source-specific name. Define columns,
source indicator codes, IFs table names, input and output units, conversion factors,
missing tokens, and metadata. Set `status` to `active` after reviewing those choices.
See [recipe guidance](docs/recipes.md). Run the same `ingest` and `run` commands with
that recipe. Unmapped rows and duplicate observations fail instead of being guessed away.

## Legacy candidate review and promotion

Each `runs/RUN_ID/` contains:

| Artifact | Purpose |
|---|---|
| `report.md` | Result, table counts, warnings and errors |
| `changes.csv` | Added, revised and removed country/table/year values |
| `coverage.csv` | Non-null observations by table and year: before, incoming and after |
| `metadata_changes.csv` | Changed DataDict fields |
| `manifest.json` | Status, hashes, baseline, source and code version evidence |
| `request.json`, `recipe.json`, `countries.csv`, `config.json` | Frozen run context |
| `validation/`, `decisions.json`, `postmerge_issues.csv` | Prepared-import review, recorded choices and candidate anomaly findings |
| `candidate/` | Candidate databases; only valid when the manifest says validated |

```powershell
.\ifs.ps1 promote RUN_ID --label 'update-2026-09'
```

If a validated run has warnings, review them and use `--accept-warnings` when that
decision is made. Failed or modified candidates cannot be promoted. Releases are
copied into `releases/LABEL/`; existing labels cannot be overwritten. Promotion is
local and does not change the baseline. To use that release for the next update:

```powershell
.\ifs.ps1 configure --baseline 'releases\update-2026-09'
```

For several sources in one consolidated release, run them sequentially against
the accepted result of the preceding source. Separate runs against the original
baseline each contain only their own update.

## Demonstration and verification

A completed synthetic example is available locally in `examples/demo-workspace/`.
Its expected result is documented in [examples/README.md](examples/README.md).
Create another isolated demo with:

```powershell
.\ifs.ps1 demo --directory 'examples\my-demo'
python -m unittest discover -s tests -v
```

Use a working Python executable for tests; the exact bundled executable on this
machine is shown in [local setup notes](docs/local-setup.md).

## Repository layout

| Location | Contents |
|---|---|
| `AGENTS.md` | Instructions for future Codex tasks |
| `workflows/` | Four stage runbooks, catalog and shared handoff/issue templates |
| `docs/` | Handoff documents, processing rules and decision history |
| `reference/` | IFs schema notes and locally exported reference data |
| `recipes/` | Explicit rules for supported source layouts |
| `inbox/` | New downloads and archived request records |
| `raw/` | Original inputs stored by SHA-256 hash |
| `src/ifs_pipeline/` | Processing, validation and CLI code |
| `tests/` | Isolated end-to-end and failure-path tests |
| `reviews/` | Prepared-import validation evidence and decision templates |
| `runs/` | Candidate outputs and run evidence |
| `releases/` | Accepted local database pairs |
| `SQL Scripts/`, `output/` | Existing notebooks and analysis outputs |

Git tracks code, instructions and recipes. Local data, baseline paths and generated
databases are ignored. Back up `raw/`, `inbox/`, `reference/local/`, `reviews/`, `runs/`,
`releases/`, `config.local.json` and external baseline folders separately: Git is
not a data backup. Hashes detect accidental changes; they are not access controls.

## Current boundaries

- Working adapters: prepared IFs SQLite imports (monadic/dyadic) and explicitly mapped
  long-format monadic CSV. Dyadic processing needs a matching configured baseline.
- Native Excel, ZIP, wide-format WDI downloads, WPP cohort/scenario transformations,
  API downloads, automatic source discovery and folder monitoring are future adapters.
- The current CSV adapter supports unit scaling and value bounds, not arbitrary
  formulas, aggregation, interpolation or five-year conversions.
- Checks cover updated tables, metadata consistency and SQLite integrity. They do
  not certify every untouched baseline series or statistical outlier.
- Closed, stable source/baseline databases are required. Custom table constraints,
  indexes or triggers need a dedicated adapter instead of automatic rebuilding.
- Large imports currently load selected source tables into memory. Process sources
  separately and use recipe table selection for unusually large imports.
- The pipeline runs locally. The AI's hosting and data handling depend on the model
  and service you use to operate it; a local folder does not imply offline inference.

Next useful extension: take one real raw source download and a previously accepted
IFs result, add its recipe/adapter, and verify the complete transformation against
that result before enabling routine unattended updates.
