# Legacy request workflow

For current delivery work use [delivery workflow](delivery-workflow.md).
The commands below preserve the earlier request-based interface.

## Start here

In a PowerShell terminal opened in this folder:

```powershell
.\ifs.ps1 status
```

No API key or third-party Python packages are required for the pipeline. The launcher
uses Codex's bundled Python if available; otherwise install Python 3.10+ and use
`python ifs.py` or `py -3 ifs.py` in place of `.\ifs.ps1`.

The initial configuration on this machine points to
`D:\IFsHistSeries\IFsHistSeries 8.67 20260527`. Check `status` for the current setting.
Change it and export that release's references with:

```powershell
.\ifs.ps1 configure --baseline 'D:\IFsHistSeries\IFsHistSeries 8.67 20260527'
```

Configuration reads the two databases and exports a versioned country list,
indicator catalog, and DataDict schema into `reference/local/`. It does not copy or
modify the baseline. The country roster defaults to `SeriesPopulation`; use
`--country-table NAME` to select a different authoritative monadic reference table.

## Your normal workflow

1. Put a download and its documentation in `inbox/`. Include the source URL and
   release date/version when available.
2. Ask: **“Process the new files in inbox using the appropriate IFs recipe. Produce
   candidate databases and a change report; flag any mappings or definitions that
   need my input.”**
3. Codex inspects the download, reuses or creates a source recipe, runs the pipeline,
   and summarizes the results. Confirmed source decisions are saved in the repo.
4. Use an accepted candidate as a named local release when ready.

Files do not start processing merely by arriving in a folder. This version is
triggered by a Codex request or a command. A watcher/UI can call the same pipeline later.

## Prepared IFs import database

For an existing `IFsDataImport_*.db` containing DataDict and IFs Series tables:

```powershell
.\ifs.ps1 inspect 'inbox\IFsDataImport_Source.db'
.\ifs.ps1 ingest --input 'inbox\IFsDataImport_Source.db' --recipe 'recipes\ifs-import-monadic.json' --source-url 'https://source.example/dataset' --source-release '2026-09' --notes 'Update the supplied source series'
.\ifs.ps1 validate REQUEST_ID
# Review the report and complete a COPY of decisions.template.json.
.\ifs.ps1 run REQUEST_ID --decisions 'inbox\decisions-source.json'
```

Use the request ID returned by `ingest`. Replace the example source URL with the
actual provenance. The default recipe processes all tables named in the import's
DataDict and checks the full country master before blending, with 188 countries
expected by the default monadic recipe. Copy and customize
the recipe for a selected table list or a different documented country universe.

Read the [validation workflow](validation-workflow.md) for missing values,
anomaly checks, incoming-versus-old comparisons and per-table merge decisions.
Running a prepared request without decisions generates a review and stops before
creating a candidate. Raw CSV retains its existing ingest/run path.

