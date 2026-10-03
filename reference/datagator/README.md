# Datagator country lookup

Reused from [Yutang Xiong's DataGatorLite](https://github.com/quciet/datagator_flask_mongo),
commit `561e371805d032236825f03dea06621ab33ab132`.

- [Original lookup](https://github.com/quciet/datagator_flask_mongo/blob/561e371805d032236825f03dea06621ab33ab132/data/country_data.json)
- [Original name matching and CSV export](https://github.com/quciet/datagator_flask_mongo/blob/561e371805d032236825f03dea06621ab33ab132/app.py)
- [Original loader](https://github.com/quciet/datagator_flask_mongo/blob/561e371805d032236825f03dea06621ab33ab132/country_mapping.py)

`country_data.json` retains the original country records and aliases, with JSON
whitespace minified locally. `provenance.json` records the upstream commit/blob
and local SHA-256. The upstream README states MIT licensing; there was no separate
LICENSE file in the inspected commit. The contributor's names and data provenance
are retained here.

The local bridge reuses this data and the upstream `original_name,matched_name`
export contract; it does not vendor or start the Flask/MongoDB application.
See [the concordance guide](../../docs/country-concordance.md) for usage and the
boundary between country identities and geographic arithmetic.
