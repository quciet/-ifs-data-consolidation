# Maintained IFs processing decisions

## 2026-09-17: Learn from user-corrected SDebt metadata

User clarification: the goal is a general improvement to metadata preparation,
not reuse of the three tables' settings elsewhere. Every prepared series requires
a field-by-field applicability/evidence review, followed by separate checks for
missing information and unnecessary additions. Record intentional blanks separately
from unresolved fields outside DataDict. Specific SDebt values and cleared fields
remain scoped to those three tables. See docs/import-preparation.md.

Compared IFsDataImport_SDebt_ddfilled.db with the original staged import: 41
DataDict cell changes, identical schemas, and unchanged series cells (26,847
observations across three 188-row tables). Preserve the corrected IFs settings
in recipes/sdebt.metadata.json and apply them through scripts/prepare_sdebt.py.
This is a source-specific reference, not permission for blanket metadata defaults
or data transformations. See docs/import-preparation.md and local evidence in
reviews/sdebt_metadata_20260917/. The corrected database was read only.

Record accepted source-specific decisions here so they survive separate tasks.
Reference the source documentation, affected recipe and a validating run/fixture.

| Date | Scope | Decision | Evidence / status |
|---|---|---|---|
| 2026-09-08 | Architecture | Local files + standard-library Python pipeline, operated by Codex | Initial user request; no watcher or separate agent app yet |
| 2026-09-08 | General blending | Prefer new non-null values; preserve old values in gaps | Existing DBlending notebook; synthetic integration tests |
| 2026-09-08 | Derived columns | Earliest/MostRecent store values, not years | Existing DBlending notebook |
| 2026-09-08 | WPP | Keep special aggregation/forecast logic out of generic imports | WPP Update notebook; raw-source adapter still requires validation |
| 2026-09-08 | Initial baseline | Configure 8.67 (2026-05-27) as the initial local baseline | Newest folder found under D:\IFsHistSeries; agent default pending any user change |

For a new entry, include: source/release, definition, IFs target table, input/output
units, country exceptions, blending/aggregation rule, reason, evidence, recipe and
test/run reference. Record unresolved questions explicitly rather than treating
an assumption as accepted source policy.

## Datagator integration — 2026-09-08

The user identified quciet/datagator_flask_mongo as the existing country-name mapping tool and explicitly distinguished name mapping from territorial value processing. Reuse its pinned JSON and reviewed mapping exports. Keep numerical aggregation/allocation rules separate, and stop for unresolved geography rather than choosing weights or sums from names. See [country concordance](country-concordance.md) and reference/datagator/provenance.json for implementation and source evidence.

## 2026-09-09: Prepared-import validation before merge

The user endorsed country/structure, units/years/missingness, incoming anomalies,
old-versus-new comparisons, explicit merge decisions and final candidate validation.
Prepared imports now generate source-independent review evidence before backfill.
Blank/whitespace/token normalization is audited; zero remains an observation.
Per-table decisions and accepted finding IDs are bound to the reviewed evidence.
Errors cannot be waived. Statistical screens flag investigation leads and never
reassign countries or repair values. Geographic processing still needs an explicit
indicator/year-specific transformation rule. Existing CSV and Datagator paths are
preserved. No real incoming source has been accepted through this new workflow yet.

## 2026-09-15: Delivery-folder workflow and exact batch provenance

The user authorized implementing prepare, merge, log, compare and batch discard.
Delivery operations now follow the existing release layout under D:\IFsHistSeries.
The default delivery path removes mandatory pre-merge decision templates, retains
structural checks, and reports statistical findings after merging. Every supplied
observation has immutable import provenance; no numerical transformations or
metadata defaults are permitted. Discard rebuilds both files from the base and
remaining accepted batches. Real data will be supplied by the user after synthetic
testing; no actual delivery has been processed during implementation.

## 2026-09-22: Supplied-data preprocessing defaults

The user clarified four defaults after ICTD preparation: supplied data and
documentation are the sole source unless online fetching is explicitly requested;
a DataDict-formatted workbook identifies the IFs indicators to find in raw data;
the supplied base is a read-only reference for best-supported inference about
missing units, definitions and IFs settings; and new preparation outputs belong
inside the supplied source folder unless another location is requested.

Record inference evidence and uncertainty rather than presenting guesses as source
facts. Do not backfill preparation values from the base. Source errors or URLs do
not authorize online investigation. Output snapshots exclude marked generated
packages and are captured before nested output creation to prevent self-copying.
The ICTD and SDebt adapters now default to fresh source-local output folders and
retain an explicit `--output` override. Synthetic tests cover nested copies,
repeat runs, no overwrite, and explicit destinations. No prior real package is
moved, rerun or re-certified by this workflow change.

## 2026-09-22: Reusable local source guides

The user requested a local collection of per-source guides, informed by supplied
wiki material and experience from data updates. `source-guides/` is that collection
within the existing repository. The model reads the relevant guide at intake,
creates it if absent, and updates supported changes during preprocessing. Each
run freezes the version used and records guide maintenance in its handoff.
Release-specific exceptions, evidence-backed inference and unresolved questions
remain distinct. Wiki access requires an explicit instruction; reading a wiki
does not authorize fetching datasets or publishing edits. The initial ICTD guide
summarizes existing preparation evidence without rerunning or changing real data.

## 2026-09-22: External source library location

The user approved `D:\IFs Source Library` with one folder per source. This
supersedes the earlier same-day choice to store maintained guides in the processing
repository. ICTD now lives at `ICTD/SOURCE_GUIDE.md`, with a verified copy of the
supplied PDF in Documentation and links to canonical executable profiles in Recipes.
The repository's source-guides directory is now a pointer index; its old guide and
template paths are compatibility pointers. Workflow instructions direct maintenance
to the external library. Existing data packages and historical evidence were not
changed. The library requires its own backup; no background service was configured.
