# Installation and direct folder paths

Python 3.10 or newer is required. No AI account, API key, managed workspace or
runtime third-party Python dependency is required by the processing engine.
Building a distribution uses setuptools and wheel; these are build tools.

From a complete source checkout, install with:

```text
python -m pip install .
ifs-data --help
ifs-data paths
ifs-data workflows
```

Alternatively install a supplied wheel with `python -m pip install PATH_TO_WHEEL`.
`python -m ifs_pipeline` exposes the same commands when the console executable is
not on PATH. The installed package includes workflow runbooks/templates, reference
Markdown, recipe templates, and the pinned DataGator lookup and provenance.
It contains no user databases or local configuration. Build snapshots come from
the maintained repository files; do not edit installed snapshots as source.

## Choose paths without creating a workspace

Provide absolute paths to existing source/base folders and new output folders.
Relative delivery and base names resolve under the configured delivery root;
without one, they resolve under the current directory. Other input/output paths
remain relative to the current directory. `--root` still selects the location
used by legacy request commands; it is not a required workspace for deliveries.

```text
ifs-data --delivery-root /data/releases delivery-prepare --base baseline --delivery update
ifs-data --delivery-root /data/releases delivery-merge --delivery update
ifs-data --delivery-root /data/releases delivery-compare --delivery update
```

Global options go before the command. An optional JSON file can remember paths:

```json
{
  "delivery_root": "./releases",
  "source_library": "./source-guides"
}
```

Use `ifs-data --paths-config /path/to/paths.json paths` to inspect the result.
Paths in that file resolve relative to the file. Settings do not create folders.
Precedence: explicit CLI options, `IFS_DELIVERY_ROOT` / `IFS_SOURCE_LIBRARY`
environment variables, configuration, then current directory for delivery names.
An absolute base/delivery path always stands on its own.

Source library configuration exposes the authoritative guide location for the
assistant/operator; it does not copy, author, or update source guides automatically.
Source-specific preparation scripts remain checkout tools in this milestone.
No general preprocessing runner or model API adapter has been added yet.

For development checkouts only, an ignored `paths.local.json` at the repository
root is loaded when no explicit config is supplied. This installation preserves
the existing D:/IFsHistSeries and D:/IFs Source Library defaults that way. Those
machine settings are excluded from distributions; installed users have no D: default.

## Synthetic check and development tests

```text
ifs-data demo --directory /path/to/new-synthetic-example
python -m unittest discover -s tests -v
```

The demo exercises the legacy CSV path with invented example countries only.
The installation test additionally builds and installs a wheel, excludes checkout
imports, and exercises native delivery prepare/merge/compare/cleanup on synthetic
data. Packaging tests require pip, setuptools and wheel but perform no downloads.
This is not a claim of tested Linux/macOS operation; current execution testing is
on Windows. Historical notebooks and real deliveries are never run by these tests.
