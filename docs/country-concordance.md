# Country concordance with DataGatorLite

This repository reuses your [DataGatorLite](https://github.com/quciet/datagator_flask_mongo)
country lookup and mapping-export format. Datagator remains the existing name-review
tool. Its fuzzy matching interface can suggest and edit matches; the local pipeline
uses exact known identities or an explicitly reviewed export.

Country-name concordance identifies entities. Combining or splitting observations
is a separate data-processing operation. This bridge performs no arithmetic.

## What is reused

The pinned local lookup at `reference/datagator/country_data.json` contains 188 IFs
countries and 567 alternative-name entries. Country names, codes and alternatives
produce 754 distinct normalized lookup keys. At integration, all 188 name/code pairs
matched the configured IFs 8.67 master, with no conflicting normalized aliases.

The lookup comes from Datagator commit
`561e371805d032236825f03dea06621ab33ab132`. The source URL, original Git blob
identifier and local file hash are in `reference/datagator/provenance.json`.
No MongoDB connection, hosted service, Flask app or fuzzy-matching dependency is
required to run the bridge. The upstream application has not been modified.

The baseline country master defines the output universe. Datagator supplies aliases.
A source covering three countries does not redefine IFs as a three-country system.

## Normal workflow

Use the original UTF-8, comma-delimited CSV with a single country-identity column.
Excel or a different source layout still needs a reproducible conversion first;
retain the original download as evidence.

First try the existing Datagator lookup:

```powershell
.\ifs.ps1 concordance --input 'inbox\source.csv' --country-column 'Country' --source 'Source name'
```

The command checks names, codes and known aliases using Unicode normalization,
case-insensitive comparison and whitespace normalization. It preserves punctuation
and does not accept approximate matches automatically.

If names remain unresolved, use Datagator's review interface and download its
**mapping CSV**, which has this structure:

```csv
original_name,matched_name
Source spelling,Canonical IFs country name
```

Then run:

```powershell
.\ifs.ps1 concordance --input 'inbox\source.csv' --country-column 'Country' --source 'Source name' --mapping 'inbox\source_mapping.csv' --reviewed
```

`--reviewed` records that the exported mappings were checked as references to the
same geographic entities. Datagator can prefill suggestions above a similarity
threshold, so merely downloading the CSV is not proof that those choices were
reviewed. Blank matched names remain unresolved; they are not exclusions. The
mapping export must refer to the selected source country column, not unrelated
strings from a whole-sheet scan.

Use original data plus the mapping export, rather than only Datagator's
`*_replaced.csv`, so the original identities remain available for collision checks.
A name lookup cannot prove unchanged geographic coverage, even if a string matches
exactly; a historical boundary change still requires source-specific investigation.

Known aggregates can be explicitly excluded, for example:

```powershell
.\ifs.ps1 concordance --input 'inbox\source.csv' --country-column 'Country' --source 'Source name' --exclude 'World'
```

Repeat `--exclude` for each exact source label. Record the reason in the source
recipe or decision log. The report retains every exclusion and affected row count.

## Outputs and processing gate

Every preparation creates `reference/local/concordance/ID/` containing:

| Artifact | Purpose |
|---|---|
| `report.md` | Status, mapped/unresolved/excluded counts and collisions |
| `matches.csv` | Original labels, row counts, destination identities and match method |
| `review_in_datagator.csv` | Current original-to-target choices for inspection; unresolved targets are blank |
| `master.csv`, `datagator.json` | Frozen master and alias lookup |
| `reviewed_mapping.csv` | Original mapping export, when supplied |
| `datagator_provenance.json` | Pinned upstream lookup evidence, when available |
| `concordance.json` | Source hash, country column, provenance, exclusions and absent IFs countries |
| `countries.csv`, `recipe_fields.json` | Executable mapping and recipe fields, produced only when ready |

Statuses:

- `ready`: mappings resolve without convergence of distinct source labels.
- `blocked`: unknown/ambiguous names, blank reviewed targets, or no mapped entities.
- `needs_processing_review`: distinct source labels converge on one IFs entity.

A non-ready status returns a nonzero exit code and does not produce an executable
country reference. Converging labels might be harmless spelling variants or
different territories; the tool does not decide which. Review source identity and
observation keys before adding a tested normalization or territorial-processing
recipe. Repeated rows mapping to the same country/indicator/year also fail during
data loading. No automatic deduplication, summation or averaging occurs.

For a ready bundle, copy all fields from `recipe_fields.json` into the source's
CSV recipe. They set `country_reference`, `concordance_manifest`, and
`ignored_country_codes`. Keep the matching `columns.country` declaration.

Run the usual `ingest` and `run` commands. Intake verifies the mapping is bound to
that exact input file, country column and exclusions. It snapshots the bundle with
the request. Runs and accepted releases retain that evidence, and changed archived
mapping files fail verification. Regenerate the concordance for a changed download;
reuse a reviewed mapping export if it still applies to that source's labels.

The original simple country-reference CSV interface remains supported for existing
recipes. The new source-binding checks apply when `concordance_manifest` is present.
Prepared IFs SQLite imports are already expected to use canonical keys; this bridge
currently targets the raw CSV stage.

## Separate territorial data processing

A mapping decision is insufficient to authorize these operations:

| Situation | Additional rule needed |
|---|---|
| Two non-overlapping territories into one target, additive counts/totals | Explicit members, compatible units and periods, sum rule, and missing-component policy |
| Combining a rate or percentage | Its numerator/denominator or justified weights; often recompute from summed components |
| Splitting a total across several targets | Explicit allocation weights with source, applicable years, completeness and sum-to-one checks |
| Historical geographic change | Definition of the target boundaries and the years for which the correspondence applies |
| Index, median, category or non-additive measure | Indicator-specific method; do not assume sum or average is meaningful |

The future processing stage should operate on original entity IDs before collapsing
them to final IFs keys. Its rule should identify source/release, indicator, year
range, inputs, outputs, operation, units, weights, missingness policy and evidence.
It should record contributing rows and values, then validate coverage and appropriate
conservation checks. A source-level name alias should never activate such arithmetic.

No generic geographic-processing engine has been enabled in this integration.
Actual source examples and agreed methods are needed to implement those rules.
For a partially observed sum, decide explicitly whether a missing member invalidates
the total; do not turn missing into zero implicitly. Allocation weights must not be
inferred merely from the country names or assumed to be population weights.

## Refreshing Datagator

Keep the upstream snapshot pinned. To adopt a new version, retrieve its country
JSON, compare it to the configured master, review added/removed/ambiguous aliases,
update provenance, and run the tests. Do not silently replace the global snapshot
during processing. Source-specific reviewed mappings are preferable to globally
changing aliases for a single unusual source.

See [processing rules](processing-rules.md) for existing consolidation behavior
and [decisions](decisions.md) for accepted source policies.
