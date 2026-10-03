# Original input archive

`ingest` copies each original file under its SHA-256 hash and retains its filename.
Do not edit archived files. A changed download should be ingested as a new request.
The pipeline verifies the archived hash before processing. Data is excluded from Git.
