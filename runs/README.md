# Processing runs

Each execution gets a new folder containing a manifest, report, frozen request
context, change CSVs when processing succeeds, and candidate databases.
A failed folder can contain incomplete candidate files; the manifest status is
authoritative for whether promotion is allowed. Keep run evidence with its raw
input and referenced baseline. These artifacts are excluded from Git.
