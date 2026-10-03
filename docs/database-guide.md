# IFs database guide

This guide explains the roles of the databases and how to interpret their contents.
Read it before choosing an import recipe or interpreting an unfamiliar series.
For transformation behavior, see [processing rules](processing-rules.md); for
source onboarding, see [recipes](recipes.md).

The concrete examples below were checked read-only against the configured release
**IFsHistSeries 8.67 20260527** on 2026-09-08. Counts and coverage are release-specific.
Field interpretations describe observed usage and this repository's conventions;
undocumented IFs flags are not treated as fully understood executable rules.

## 1. Database roles and relationships

| Database or artifact | Role | How to use it |
|---|---|---|
| `IFsHistSeries.db` | Consolidated numeric time series, mainly in `Series...` tables | Read country/year observations; use the matching DataDict to interpret them |
| `DataDict.db` | Metadata database; its `DataDict` table describes variables, physical table names, units, sources and IFs usage | Resolve a table's definition and processing context before updating its values |
| `IFsDataImport_*.db` | Prepared source update package, usually containing its own `DataDict` and selected `Series...` tables | Import the selected values and their corresponding metadata together |
| `runs/RUN_ID/candidate/` | A proposed consolidated pair created from a baseline | Review its manifest and differences before treating it as an accepted release |
| `releases/LABEL/` | An accepted local pair, with its run evidence | Can be explicitly configured as the baseline for the next update |
| `raw/` | Preserved downloaded inputs | These are source evidence, not necessarily IFs-formatted data |

A release directory identifies the pair of consolidated databases. Treat them as
one versioned unit; do not combine a historical database from one release with an
unrelated metadata database simply because their filenames match.

The relationship is a name-based application convention:

```text
DataDict.db -> DataDict row -> [Table] = "SeriesPopulation"
                                        |
                                        v
IFsHistSeries.db -> physical table "SeriesPopulation"
                   -> one country row, many year-value columns
```

`DataDict.[Table]` holds an exact physical table name, including the `Series`
prefix and any punctuation. `Variable` identifies the corresponding IFs variable;
it is not a substitute for the physical table name. The inspected DataDict schema
has no declared primary key. Do not assume SQL constraints enforce these links.

For a concrete prepared-package example, the inspected
`IFsDataImport/IFsDataImport_Essex.db` contains DataDict with three rows and the
three tables `SeriesTheta1`, `SeriesTheta2` and `SeriesTheta3`. This illustrates the
package structure; other imports can differ and must be inspected.

## 2. Understanding a series table

Most processing in this repository uses monadic data: one country per row.
The logical observation is **table + country identity + year**. Storage is wide:
a separate SQL column for each year, rather than a single Year/Value pair.

| Field | Interpretation |
|---|---|
| `Country` | Canonical IFs country label |
| `FIPS_CODE` | Canonical code as stored in that baseline; preserve its actual value as text |
| `1950`, `1951`, ... | Numeric observations in the units specified by DataDict |
| `Earliest` | First non-null observation VALUE in chronological order, not its year |
| `MostRecent` | Last non-null observation VALUE in chronological order, not its year |

Do not infer an external country-code standard from the name `FIPS_CODE`. For
example, the inspected population table uses `AFG` for Afghanistan. Source aliases
must be mapped to the exported IFs country roster rather than guessed from names.
Country names and codes together must be consistent. In the inspected population
table, these columns have no declared uniqueness or NOT NULL constraints; the
pipeline validates the identities itself.

Keys are written as `VARCHAR(255)` and numeric fields as `DOUBLE(53)` by the current
pipeline. SQLite type declarations alone are not proof that each stored value is
valid; processing also checks the values. Null means missing, while zero is a
numeric observation. A source's sentinel values require a documented recipe.

Dyadic data represents directed actor/partner pairs. The existing conversion
notebook specifies `Actor`, `Actor_FIPS`, `Partner`, `Partner_FIPS` as the keys.
Actor A / Partner B is distinct from Actor B / Partner A. A 188-country row-count
rule cannot be applied to pair tables. This guide did not profile a separate real
dyadic release; inspect its schema and configure a matching baseline before use.

Despite its name, IFsHistSeries.db also contains forecast-named tables: the inspected
release has 1,409 tables whose names start with `SeriesForecast`. Do not assume that
every table is historical observed data. Determine estimates/projections, scenario
and revision from the source and metadata for the particular series.

## 3. DataDict fields

The inspected DataDict has 31 columns. Use these groups to decide which information
to retrieve; do not load every series merely to answer a metadata question.

| Fields | Interpretation and use |
|---|---|
| `Variable`, `Table` | Variable identifier and exact physical table link |
| `Group`, `SubGroup` | Subject classification for discovery |
| `Definition` | Description of the measure; may preserve wording from the original source |
| `Extended Source Defn` | Additional source context; observed content can be a source filename, not just prose |
| `Units`, `CURRENCY` | Stored-value unit and currency context; confirm price basis and scale where relevant |
| `Years` | Described coverage span; inspect actual year columns and non-null observations for precise coverage |
| `Source`, `Original Source` | Source attribution and upstream reference; Original Source can contain a URL |
| `Name in Source`, `Code in Source` | Original indicator label/code for mapping a new download |
| `Formula` | Recorded transformation context; do not automatically execute it or apply it again to already-converted data |
| `Last IFs Update` | IFs metadata update stamp; distinct from source release date and latest observation year |
| `Country Concordance` | Named country mapping/concordance context |
| `Aggregation`, `Disaggregation` | IFs rule labels; preserve them and establish their full semantics before implementing a rule |
| `TreatNullsAs0s` | IFs null-treatment flag; not permission for the generic intake pipeline to replace missing values with zero |
| `UsedInPreprocessor`, `UsedInPreprocessorFileName` | Preprocessor usage flag and module/file references; a series can list multiple modules |
| `UsedInHistAnalog`, `UsedInFunctions`, `CompareOtherForecast` | IFs usage/comparison flags; preserve source/baseline metadata rather than inventing behavior |
| `Series`, `CoVaTrA`, `Cohort` | Classification flags; the exact meaning of CoVaTrA and downstream flag semantics require IFs-specific documentation |
| `Proprietary` | Metadata restriction flag; source terms still need to be understood for sharing |
| `Decimal Places` | Recorded display precision; not an instruction to round stored values during consolidation |
| `Notes`, `DisplayNotes` | Editorial/source context and display annotations |

These fields contain text, integers and nulls in practice. In particular, flags do
not all share one representation: the population row has `Series = Yes`, while
`UsedInPreprocessor = 1`. Preserve meanings instead of normalizing all flags by guess.
A blank Formula, code or flag is not proof that no transformation or rule exists.

## 4. Worked example: SeriesPopulation

The inspected table has 188 country rows and 74 year columns, 1950 through 2023.
Its DataDict row records:

| Metadata field | Actual value |
|---|---|
| Variable | Population |
| Table | SeriesPopulation |
| Definition | Total Population, as of 1 July (thousands) - Estimates |
| Units | Millions |
| Name in Source | Total Population, as of 1 July (thousands) |
| Formula | /1000 |
| Source | UNPD World Population Prospects |
| Extended Source Defn | WPP2024_GEN_F01_DEMOGRAPHIC_INDICATORS_FULL.xlsx |
| Last IFs Update | 2024/08/16 |
| Aggregation / Disaggregation | SUM / POP |
| UsedInPreprocessorFileName | ECONOMY, HEALTH, INFRASTRUCTURE, POPULATION |

The Afghanistan row contains 7.7762 in 1950 and 41.4548 in 2023. Its Earliest and
MostRecent values are 7.7762 and 41.4548 respectively. Interpret the stored values
in **millions**, following Units. The source wording says **thousands**, and the
recorded `/1000` is consistent with converting those source units to millions.
This is an interpretation of the inspected metadata, not an independent rerun of
the original WPP transformation. Do not divide these existing database values by
1,000 again. Verify the units of a new raw download before choosing a conversion.

This example also distinguishes three dates: the IFs release folder is dated
2026-05-27, the series update stamp is 2024-08-16, and its latest year column is
2023. None of those dates can substitute for the other two.

## 5. Snapshot of the inspected release

| Observed measure | Count |
|---|---:|
| Tables in IFsHistSeries.db | 7,133 |
| Tables starting with Series | 7,126 |
| Rows in DataDict.db's DataDict table | 6,967 |
| Distinct nonempty DataDict Table names | 6,967 |
| DataDict Table names absent from this IFsHistSeries.db | 163 |
| Series tables with no matching DataDict Table name | 322 |

The name comparison is exact and limited to these two files. These differences
are **observations to investigate, not established database defects**. Explanations
could require other IFs databases or release-specific conventions; none were
established by this inspection. Do not delete unmatched tables, invent metadata,
or classify a whole release as invalid on this comparison alone. A targeted import
must still have a valid link for every table it updates.

All DataDict Table names in this inspected release are unique. The pipeline permits
multiple metadata rows per table with distinct Variable values for prepared imports;
that support is not evidence that such rows exist in this particular release.

IFsHistSeries.db also contains `DataEdit`, `Paste Errors`, `SFAllBlanks`,
`SFAllBlanks19602010`, `SFAllZeros`, `Table1`, and `Table2`. DataDict.db also contains
`AggRulesOld`, `DumpedData`, and `Paste Errors`. Their purposes have not been
established here. Preserve these tables rather than inferring disposability from
names such as Table1 or Paste Errors.

## 6. Useful read-only queries

Run the metadata queries against DataDict.db:

```sql
SELECT "Variable", "Table", "Definition", "Units", "Formula",
       "Source", "Last IFs Update", "Years"
FROM "DataDict"
WHERE "Table" = 'SeriesPopulation';

SELECT "Variable", "Table", "Units", "Source"
FROM "DataDict"
WHERE "UsedInPreprocessor" = 1;
```

Run these against IFsHistSeries.db:

```sql
PRAGMA table_info("SeriesPopulation");

SELECT "Country", "FIPS_CODE", "1950", "2023", "Earliest", "MostRecent"
FROM "SeriesPopulation"
WHERE "FIPS_CODE" = 'AFG';

SELECT COUNT(*) AS rows_with_2023_data
FROM "SeriesPopulation"
WHERE "2023" IS NOT NULL;
```

Inspect column existence before querying a year in another table. Quote identifiers:
years are numeric-looking column names, and actual IFs table names can contain `%`
and other punctuation. Use parameters for values and the repository's identifier
quoting helper for dynamically selected table names.

## 7. What to document for each new source

A source recipe and accompanying note should state the source/release, supported
input layout, observation grain, country universe, historical versus forecast scope,
IFs target tables, input/output units, conversions, missingness, blending policy and
known exceptions. Include one worked observation or a known accepted-result fixture.
The schema tells the agent how to read data; this source context tells it whether
the proposed update preserves the meaning of the series.

Refresh this guide's snapshot when adopting a different release. The current
machine-readable catalog and country map are exported under `reference/local/`;
config.local.json identifies the active baseline and country reference. Document
unresolved field semantics in [decisions.md](decisions.md) as evidence is obtained.
