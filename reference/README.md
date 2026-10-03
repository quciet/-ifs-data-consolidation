# IFs reference resources

`local/` holds versioned exports from a configured baseline:

- `countries.csv`: canonical Country/FIPS_CODE pairs and initial source-code aliases.
- `indicator_catalog.csv`: the baseline DataDict, including units and source mappings.
- `datadict_schema.json`: actual SQLite column declarations.
- `provenance.json`: baseline path, country table, count and export timestamp.

The current country reference path is in config.local.json. Country and indicator
references should be refreshed by configuring a new accepted baseline. Exported
catalogs are reference material, not an automatic mapping from arbitrary source codes.

Maintain reviewed source-specific aliases here in a named CSV if useful. Set that
path in the source recipe. See ../docs/recipes.md and ../docs/processing-rules.md.

See [the database guide](../docs/database-guide.md) for database roles, field meanings, verified examples, and release-specific catalog limitations.

The user's Datagator lookup and upstream provenance are preserved in [datagator/](datagator/). See [country concordance](../docs/country-concordance.md) for source-bound name mapping and reviewed exports.
