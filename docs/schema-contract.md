# IFs validation schema contract

Series tables use `VARCHAR(255)` for Country/FIPS_CODE or the four actor/partner
identity fields, and `DOUBLE(53)` for four-digit year columns and Earliest/MostRecent.
This agrees with `SQL Scripts/DataTypeConversion.ipynb` and the inspected 8.73 base's
SeriesPopulation table. Unknown columns and custom constraints need an adapter.
Auxiliary tables are preserved. The merger already writes this series schema;
validation now enforces it in prepared imports before merging.

DataDict has a different, field-specific schema. The selected base's DataDict table
is the schema authority, whether the target table lives in DataDict.db or an import.
Validation freezes its PRAGMA table_info as schema-contract.json and the base pair's
fingerprints. It does not assume that every metadata field is VARCHAR(255) or DOUBLE.
For the inspected 8.73 base, the schema has 31 fields:

| Declaration | Fields |
| --- | --- |
| VARCHAR(60) | Variable |
| VARCHAR(255) | Table, Cohort, Definition, Extended Source Defn, Source, Original Source, Notes, UsedInPreprocessorFileName, DisplayNotes |
| VARCHAR(55) | Group |
| VARCHAR(50) | SubGroup, Series, CoVaTrA, CURRENCY, Aggregation, Disaggregation |
| VARCHAR(35) | Units |
| VARCHAR(120) | Years |
| VARCHAR(180) | Last IFs Update |
| VARCHAR(100) | Name in Source, Country Concordance |
| VARCHAR(200) | Code in Source, Formula |
| INTEGER(1) | TreatNullsAs0s, Proprietary, UsedInPreprocessor, UsedInHistAnalog, UsedInFunctions, CompareOtherForecast |
| INTEGER(10) | Decimal Places |

Spacing/case in type declarations does not change equivalence. Column order and the
full DataDict field set follow the base. Unknown source columns are never discarded.
Absent fields retain the exact matching Table/Variable baseline value; if none
exists they remain NULL with external evidence. Explicit source NULLs are retained.
Old source DEFAULT declarations are replaced by the base schema and recorded in
schema evidence. Every output column is inserted explicitly: defaults are never
executed or used to fill NULLs. Indexes, triggers and other custom constraints still
require a dedicated adapter; they are not silently removed.
Blank text metadata remains text; blanks in numeric metadata become NULL, not zero.

SQLite does not enforce VARCHAR lengths or DOUBLE precision from these declarations.
The workflow enforces storage classes and conversion rules explicitly; it does not
truncate long metadata strings, round to Decimal Places, constrain flags to a guessed
meaning or treat INTEGER(1) as a one-digit range.

Supported observation text uses ASCII digits, an optional sign, decimal point and
scientific exponent, with optional outer whitespace. Decimal text must equal
`Decimal(str(float(token)))`; integral decimal values must also be exactly representable
as binary64. This accepts ordinary values such as 7.0 and 0.1 while rejecting excessive
decimal precision, overflow, underflow and integers losing significant digits.
Finite stored integers must be exactly representable as DOUBLE. Existing finite
stored floats on retained rows are preserved. No grouping, locale, percent scaling
or sentinel guessing. The validation workflow's complete-roster exclusion rule may
remove surplus monadic rows, with every original cell preserved in external evidence.

Numeric integer metadata uses exact Decimal parsing and signed 64-bit bounds, with
no fractional truncation. Text metadata accepts strings or string representations of
finite stored numbers. Every conversion is recorded. Schema enforcement establishes
representation, not whether CURRENCY='1' or a flag is semantically appropriate.

Repair operations write only new IFsDataImport copies. The same DataDict contract
can be checked against a standalone database, but this command never rewrites the
base DataDict.db or IFsHistSeries.db. Unresolved meaning goes to preprocessing.
