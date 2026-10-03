# On-demand Consolidation Report janitor

One responsibility: clean unnecessary files under a specified delivery's
Consolidation Report when the user asks. No release review, approval records,
interpretation, compression, automatic scheduling, remerge or repackaging.

1. Resolve the requested delivery. A request to develop this role does not
   authorize cleaning existing deliveries.
2. Run `delivery-clean --delivery 'DELIVERY'` to inspect candidates. Supported
   candidates are only the two working database files in completed runs' `work`
   folders, matching their recorded output SHA-256 fingerprints. Changed evidence,
   pending publication, active locks and linked paths block cleanup.
3. When the user requested cleanup, run the same command with `--execute`.
   No separate approval or release acceptance is necessary. A preview-only request
   authorizes only a preview. Never extend scope by guessing from file age or size.
4. Report removed file count, bytes and cleanup log, or why cleanup was blocked.

Preserve all reports, CSVs, provenance, manifests, code snapshots, preparation
evidence, frozen inputs including skipped/excluded data, and unknown files.
Staged import packages are still required by native verification and stay intact.
Never delete whole run folders. Final databases, IFsDataImport, Working Files and
the external base are outside the deletion scope. Native manifests stay unchanged;
the janitor records a small cleanup journal and verifies the delivery afterward.
Comparison, log refresh and future merge/discard remain usable without restoration.

The retired archive system's verification/restoration code remains recovery-only
for previously archived evidence. The janitor will not clean a sealed old archive.

Example request: "Run the janitor to clean unnecessary Consolidation Report files
in [delivery]."
