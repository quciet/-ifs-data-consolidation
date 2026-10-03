# Local setup

Use `.\ifs.ps1` in this project. On this machine the script finds the bundled
runtime at:

```text
C:\Users\yutang.xiong\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

The installed `py` launcher reported no installed Python during initial setup;
the wrapper therefore prefers the bundled runtime. Tests can be run with:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
```

This path is a machine convenience, not a package dependency. A regular Python
3.10+ installation can run `python ifs.py ...` without installation. Optionally,
`python -m pip install -e .` installs the `ifs-data` console command; no editable
installation is needed for the checked-out launcher.

`config.local.json` and `reference/local/` are ignored by Git because they contain
machine-specific paths and exported baseline metadata. Run `configure` after
moving/cloning the repository. Source files may remain outside the repo until
intake archives them. Keep source and baseline databases closed during processing.

## Delivery commands

delivery-prepare/merge/compare/discard use the base and delivery folders recorded
with the delivery itself; they do not depend on config.local.json or require the
legacy configure/ingest flow. Relative release names resolve under D:\IFsHistSeries.
Use absolute paths for another location. See delivery-workflow.md.
