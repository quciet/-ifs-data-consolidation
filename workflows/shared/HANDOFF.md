# Shared handoff contract — version 1

`handoff.json` is a human-maintained stage record, not a replacement for pipeline
manifests or an executable approval. Empty arrays mean not yet documented, not
zero issues or proof that a check passed. `workflow-init` only scaffolds this record.

## Required context

Record the task's authorized scope, stage ID, source/release, intended base/delivery,
parent handoff and the next stage. Each input/output artifact entry uses `path`,
`sha256`, `role` and optional `batch_id`. Hash actual files; never put placeholders
in a completed handoff. Paths in completed handoffs should be absolute. Retain the
frozen inputs and evidence, not only hashes pointing to replaceable files.

For preprocessing, record the supplied DataDict indicator scope and raw-source
mapping for every requested Table/Variable. Record whether online fetching was
explicitly requested (default: supplied files only), the source-root/output
locations, and any explicit override of the default output-inside-source policy.
Identify base-supported inferences separately from explicit source facts, with
evidence and uncertainty. The base is reference material, not raw gap-fill data.

The authoritative maintained guide is under `D:\IFs Source Library\<Source>`.
Record source-guide maintenance in `source_guide_review`: source ID, maintained
path, `created`/`updated`/`reviewed_no_change`, applicable release/indicator scope,
material changes and open questions. Add actually used and, when different,
updated guide snapshots to `source_guide_snapshots`, each with absolute path,
SHA-256 and role. Retain both when a guide changes during a run; say which changes
were applied to the output. Blank scaffold fields are not proof of review. Wiki
summaries also identify title, URL, known revision/date and consultation basis.

Evidence should identify existing native artifacts (repair manifest, recipe,
DataDict field review, delivery.json, run.json, findings.csv, etc.). Do not copy
process commentary into DataDict. Record a separate coverage summary containing
country rows, countries with data, year span and non-null observations per table.

## Status and table scope

`status` is one of `not_started`, `in_progress`, `completed`,
`completed_with_issues`, `blocked`, or `failed`. These workflow-level labels do
not override native command statuses such as `prepared_with_unresolved` or
`completed_with_skips`; retain those under `native_results`.

For preprocessing/validation, record every Table/Variable outcome in
`table_outcomes`: `ready`, `ready_with_findings`, `unresolved`, or `repull_required`,
with exact source/output artifact references and evidence. A ready outcome needs
the stage's required checks. Statistical flags alone do not make a table unusable.
Unresolved formatting or metadata cannot be described as validated.

For merger, use `applied`/`skipped` outcomes and the registered batch ID. For
comparison, identify the updated-table scope, checks performed and findings;
do not label unrelated base tables as reviewed.

## Issues and routing

`issues.csv` has one issue per row:

`issue_id,stage,owner_stage,source_file,batch_id,table,variable,country_key,year,category,severity,status,evidence_path,message,next_action`

Use stable issue IDs within the task. `country_key` can contain a JSON country/pair
identity; quote CSV values correctly. Evidence contains original tokens/values,
locations and fingerprints. Use severity `info`, `review` or `blocking`; status
`open`, `resolved` or `deferred`. Deferred issues require a reason and remain visible.

| Issue | Owning workflow |
|---|---|
| Incomplete raw release, bad extraction, wrong indicator/scenario, unsupported source transform | preprocessing |
| Numeric representation, missing token, identity formatting, DataDict link or equivalent unit label | validation |
| Competing source precedence, batch registration/replay, inconsistent output pair | merger |
| Misleading diagnostic, unsuitable threshold, incomplete change explanation | comparison |
| Suspicious observations | comparison investigates, then routes with evidence to validation/preprocessing as appropriate |

A `repull_required` recommendation records the affected release/tables, evidence,
recommended source/action, and whether a re-download or rerun of preparation is
needed. It does not itself trigger a download or authorize a different data source.
Only an explicit user instruction to fetch online permits executing that download,
including downloading the same release to investigate an error.

## Handoff acceptance

The receiving workflow reads the producing runbook's completion criteria, checks
artifact/base fingerprints and table scope, and preserves unresolved issues. Reuse
native verification commands where available. `ready` text in a handoff is not a
substitute for source or value verification.

The merger retains native safeguards and can skip a structurally invalid table
while processing valid ones. A partial import may contain unresolved tables only
if that is explicit in the handoff and the delivery records their outcomes. Do not
invent numerical repairs, suppress findings or ask for blanket table approvals.

After a correction, create a new import/version and handoff that references its
parent; never edit old evidence to certify a changed file. Registered replacements
follow delivery-discard and explicit overlap precedence. Comparison routes issues
back to earlier workflows; it never repairs delivery cells directly.
