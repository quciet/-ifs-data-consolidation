# Prepared IFs import validation and consolidation

For current delivery consolidation, follow [delivery workflow](delivery-workflow.md).
The delivery commands supersede legacy pre-merge approvals and numerical defaults;
this document retains reference details for the earlier request pipeline.

Prepared import databases now pass through a review before a candidate is created.
The review examines the incoming data independently of the old database. Historical
backfill therefore cannot make an incomplete import appear complete.

## Six stages

| Stage | Checks and evidence |
|---|---|
| 1. Country and structure | Exact names and codes against the configured IFs master; duplicate or inconsistent keys; expected columns; absent country rows. The default monadic recipe expects the 188-country master. |
| 2. Meaning, units and missingness | Required DataDict fields, unit compatibility, changed definitions/source/formula fields, observed years versus metadata, SQL NULL versus blank/whitespace/tokens, numeric storage and finite values. |
| 3. Incoming data quality | Countries and years with no usable values, internal gaps, sudden jumps, long constant runs, declared value bounds and explicitly declared component sums. |
| 4. Incoming versus old | All changed cells, lost observations, old-only years, coverage changes, large revisions, possible scaling errors, possible country misplacement and year shifts. |
| 5. Merge decisions | Select and explain an action for every table; resolve blocking errors and record accepted review findings. |
| 6. Candidate verification | Verify saved values and metadata against computed results, types, derived fields, SQLite integrity, old/incoming/result coverage, and repeat anomaly and declared-sum checks on the candidate. |

Country presence and observation coverage are different measures. A table can have
all 188 correctly identified countries while some rows contain no data. Both are
reported. Expected country coverage is taken from the configured master, with the
recipe row count as an additional check. Dyadic tables validate actor/partner
identities and unique pairs; no complete pair universe is inferred.

## Commands

After inspecting and ingesting a prepared database:

~~~powershell
.\ifs.ps1 validate REQUEST_ID
~~~

For a batch:

~~~powershell
.\ifs.ps1 validate REQUEST_ID_A REQUEST_ID_B
~~~

The command returns a review ID, status and report path. Open
reviews/REVIEW_ID/report.md and its CSV evidence. A blocked review exits with code 1.
A ready_for_decision review still requires table decisions, even if no issues were
found.

Copy reviews/REVIEW_ID/decisions.template.json to a separate file, such as
inbox/decisions-source.json. Fill it only after reviewing the evidence or applying
an already established source policy. Keep the original review files unchanged.

~~~json
{
  "schema_version": 1,
  "review_id": "COPY_FROM_TEMPLATE",
  "review_sha256": "COPY_FROM_TEMPLATE",
  "accepted_issue_ids": ["COPY_REVIEWED_ISSUE_IDS"],
  "requests": {
    "REQUEST_ID": {
      "SeriesExample": {
        "action": "prefer_new_non_null",
        "reason": "This release updates recent observations; older history is retained under the documented source policy."
      }
    }
  }
}
~~~

Keep the real request/table names from the generated template. If there are no
review findings, accepted_issue_ids is an empty list. Use the IDs in issues.csv,
not the example placeholders. Acceptance should record an actual decision;
generating a template is not acceptance.

~~~powershell
.\ifs.ps1 run REQUEST_ID --decisions 'inbox\decisions-source.json'
~~~

Running a prepared request without --decisions generates a review and stops before
creating candidate databases. Its run status is needs_review, or failed if the
review has blocking errors; both exit with code 1. CSV imports retain their existing
ingest/run workflow and do not yet receive this full prepared-import review.

## Missing values

| Representation | Handling |
|---|---|
| SQL NULL | Missing; counted separately. |
| Empty string or whitespace | Proposed conversion to SQL NULL, with the original representation recorded for review. |
| Numeric zero, including text "0" | A real observation; never a missing marker. |
| Source tokens such as "..", "NA" or literal "NULL" | Missing only when explicitly declared in missing_values. Matching is case-sensitive after trimming the cell. |
| Numeric sentinels such as -999 | Missing only when explicitly declared in missing_numeric_values; applies to numeric and numeric-text cells. |
| Other numeric text | Parsed to a finite number and recorded in normalization.csv. |
| Unknown text, non-finite values or unsupported storage such as BLOB | Blocking error. |

Both missing-value settings apply to prepared SQLite and CSV adapters. For example:

~~~json
{
  "missing_values": ["", ".."],
  "missing_numeric_values": [-999]
}
~~~

Zero cannot be declared as a missing token or sentinel. Use numeric sentinels only
when the source documents their meaning: -999 may be a valid observation in another
indicator. Baseline values are interpreted separately; source token rules do not
reclassify historical numbers.

Normalization happens in memory during validation; original files remain unchanged.
The audit records what was observed, not an inferred missingness reason. If a token
means suppressed, not applicable or withdrawn, preserve that meaning in source
documentation and the decision reason. A SQL NULL cannot by itself distinguish those
cases. prefer_new_non_null would restore an old observation at a missing incoming
cell, so it is inappropriate where missingness means deliberate withdrawal.

## Rules and thresholds

Add a validation object to a maintained source recipe before intake. These settings
are screening rules; choose thresholds that make sense for the series.

~~~json
{
  "validation": {
    "allow_partial_countries": false,
    "defaults": {
      "frequency_years": 1,
      "jump_fraction": 0.5,
      "revision_fraction": 0.5,
      "absolute_change_floor": 0.0,
      "near_zero_floor": 1e-9,
      "flat_run_length": 6,
      "min_value": null,
      "max_value": null
    },
    "tables": {
      "SeriesExamplePercent": {
        "min_value": 0,
        "max_value": 100,
        "absolute_change_floor": 2
      },
      "SeriesExampleFiveYear": {
        "frequency_years": 5
      }
    },
    "sum_rules": [
      {
        "total": "SeriesExampleTotal",
        "parts": ["SeriesExamplePartA", "SeriesExamplePartB"],
        "absolute_tolerance": 0.000001,
        "relative_tolerance": 0.000001
      }
    ]
  }
}
~~~

The defaults above are the actual defaults. Table overrides inherit them. Resolved
rules for each successfully reviewed table are saved in review.json.

An absolute change must exceed absolute_change_floor before the relative jump or
revision screen applies. When the old magnitude is below near_zero_floor, a change
above the absolute floor is flagged without unstable percentage division.
Bounds are optional and generate review findings; negative values are not globally
invalid. Gaps are assessed between usable observations using frequency_years.
A regular five-year series should declare a five-year interval. No interpolation or
annualization is performed. Candidate tables can include intervening NULL year columns.

Missing incoming country rows block by default. Setting allow_partial_countries
to true makes an explicitly partial import reviewable; it does not invent
observations. The final expected_country_count check remains active.

Sum rules run only for relationships you declare, with compatible units,
definitions and geography established beforehand. Missing components are skipped
and counted, never treated as zero. The pre-merge check uses selected incoming
tables; the post-merge check uses candidate tables, including retained historical
components. A name concordance does not establish an additive relationship.

## Findings and actions

- error: invalid structure, countries, values or incompatible metadata. Resolve the
  underlying input/recipe and ingest again. Errors cannot be accepted away.
- review: a plausible issue that requires a recorded interpretation. List accepted
  issue IDs and explain the table decision.
- info: descriptive coverage, storage or normalization information.

| Table action | Effect |
|---|---|
| prefer_new_non_null | New observations win; baseline fills incoming gaps and preserves baseline-only history. |
| replace | Incoming table replaces the old table; historical values are not backfilled. |
| keep_old | Keep the baseline table and its metadata. A source-only table is not added. |
| hold | Stop candidate creation for the request until the table decision is resolved. |

A replacement that removes populated cells also needs allow_coverage_loss: true
in the archived recipe. Unit changes need allow_unit_change: true and a replacement
recipe/policy; the pipeline never blends incompatible units. Prefer a documented
conversion into baseline units before intake. Table decisions do not execute
conversions, territorial combinations, splits or other arithmetic.

Multiple requests for the same table generate an overlap finding. Record intended
source precedence before merging. A batch review does not combine requests or pick
a winner: process sources cumulatively against each accepted result. After switching
the baseline with configure, validate the next request again. A blocked batch can be
split into separate reviews while its defective requests are repaired.

Reviews are tied by SHA-256 to archived requests, inputs, recipes, the country master,
baseline databases and processing code. Changed evidence requires a fresh review.
These hashes detect accidental changes; they are not cryptographic signatures or
access controls. Keep baseline/source SQLite databases closed and stable.

## Evidence and candidate review

Each reviews/REVIEW_ID/ contains:

| File | Purpose |
|---|---|
| report.md | Finding counts, first actionable findings and next steps. |
| issues.csv | Complete findings with IDs, severity, request, table, country and year. |
| missing_values.csv | Counts of each representation, including actual zero values and SQL NULL. |
| normalization.csv | Original raw representation, location and proposed conversion for non-native or invalid cells. |
| country_coverage.csv | Populated-year count and observed span for each country in old/incoming data. |
| coverage.csv | Old versus incoming non-null counts for every table/year. |
| incoming_vs_old.csv | Cell differences before backfill, including lost observations. |
| review.json | Frozen context, resolved thresholds and file hashes. |
| decisions.template.json | Unfilled per-table choices; copy before editing. |

A reviewed run also contains frozen validation/ evidence, decisions.json and
postmerge_issues.csv. Its coverage.csv reports before, incoming and after counts.
A successful write is followed by saved-data checks and fresh candidate screening;
for example, a retained historical value and a new observation may create a jump
that was absent from the incoming table alone.

Review the candidate report and post-merge findings before promotion:

~~~powershell
.\ifs.ps1 promote RUN_ID --label 'source-update'
~~~

Use --accept-warnings only when the post-merge warnings have been reviewed. Candidate
and frozen evidence hashes are checked again during promotion. The named release
retains those artifacts; promotion does not modify the baseline or select it for the
next request.

## What the screens cannot establish

Country misplacement detection looks for an incoming trajectory that exactly matches
a unique different baseline country, with at least five usable observations and three
distinct values. It cannot detect every swap, approximate displacement, or an error
already present in both releases. No country is automatically reassigned.

Scale screening needs at least ten comparable cells; it flags common powers-of-ten
factors when at least 80% are within 2% of a candidate factor. Year-shift screening
looks one configured interval forward/backward and requires enough changing values.
These are investigation leads, not proof of a unit or alignment error.

The workflow cannot establish semantic correctness from numeric patterns alone.
Check source definitions, geographical coverage, denominators, estimates versus
observations, methodological breaks and revision notes. No generic sum/split rule,
imputation or outlier correction is inferred by the model. Untouched baseline tables
are preserved and are not comprehensively revalidated. Large selected imports are
currently loaded into memory; use source-specific table selection when needed.
