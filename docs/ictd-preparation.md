# UNU-WIDER ICTD preparation

Read the maintained [ICTD source guide](../source-guides/ictd.md) for indicator
mapping, methodology, scoped exceptions and evidence. This page describes running
the adapter; it does not replace the guide's release applicability checks.

`scripts/prepare_ictd.py` is a standalone preprocessing adapter for the supplied
UNU-WIDER GRD 2025 workbooks. It requires Python with `openpyxl` for reading Excel;
the merger's standard-library-only runtime is unchanged.

```powershell
.\ifs.ps1 workflows preprocessing
# Use the bundled Python executable documented in docs/local-setup.md:
python scripts/prepare_ictd.py --repo 'D:\IFs Data Consolidation' --source 'SOURCE FOLDER' --base 'D:\IFsHistSeries\IFsHistSeries 8.73 20260814' --profile recipes/ictd-2025.metadata.json
```

Without `--output`, the adapter creates a new `IFsDataImport` folder
inside the supplied source folder, choosing a fresh suffix when needed. Only use
`--output 'NEW OUTPUT FOLDER'` when the user requests a different destination.
An explicit output must not exist. Generated packages are marked and excluded
from subsequent raw snapshots; inputs are listed before output creation to avoid
recursive self-copying. The adapter performs no online fetching.

The adapter preserves the original source files and nested input folders
under Working Files, writes IFsDataImport_UNUWIDER_ICTD_2025.db directly beside
Working Files and Preparation Report.md in the output folder,
and produces cell-level transformation/missingness evidence, metadata reviews,
coverage, source annotations, exclusions and a handoff for validation. It does not
merge, alter the base, or update global repository configuration.

The 27-table scope is taken from the supplied old DataDict workbook. IFs settings
come from matching entries in the selected base. Central and General government
are processed separately. Source fractions displayed as percentages are multiplied
by 100 without rounding. The shortened workbook is retained as context; the full
supplied workbook supplies observations.

The table list identifies the indicators to find, not metadata to copy unchanged.
Use the selected base for supported inference where supplied units or meanings
are incomplete. Record inference and uncertainty in the metadata review. The base
is read-only reference material; it supplies no new observations to this import.

The 2026-09-22 profile is bound to exact source fingerprints. It includes the user's
explicit instruction to treat 24 identified Excel #VALUE! cells as missing, with
their original tokens retained. The rule must not extend to other error cells or
other releases. An audited Ethiopia 1980 name correction relies on the source
identifier, ISO and regional context; ordinary alias mappings use pinned DataGator
and source identity evidence. Country/year duplicates fail rather than being selected.

For a changed download, investigate source layout, country coverage, units and each
metadata field again. Prepare a new source-specific profile and output version;
do not merely replace fingerprints to bypass review. `ready_with_findings` is a
preprocessing outcome, not proof of downstream validation or consolidation.
