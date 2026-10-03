# Workflow 2 — Import validation and repair

## Mission and starting point

Make prepared imports structurally usable and explain whether their content needs
source investigation. Inputs are IFsDataImport files, the intended base case,
available preparation evidence, reviewed metadata/unit profiles and country mapping.
This workflow owns formatting repairs; the merger should receive normalized imports.

Read [format repair](../../docs/format-repair.md),
[import preparation](../../docs/import-preparation.md),
[country concordance](../../docs/country-concordance.md), and the
[shared contract](../shared/HANDOFF.md).

## Procedure

1. Freeze inputs and fingerprint the base/reference. Inspect the table inventory,
   DataDict links, actual stored types and original missing representations. Apply
   the [schema contract](../../docs/schema-contract.md): series keys VARCHAR(255),
   year/endpoints DOUBLE(53), and the selected base's field-specific DataDict schema.
2. Resolve supported repairs using the rules below. Generate a new import version;
   preserve the original file and table/country/year-level before/after evidence.
3. Review every applicable DataDict field for completeness and unnecessary additions.
   Confirm Table/Variable links and IFs configuration as well as source meaning.
4. Verify every retained observation and every declared conversion against the
   frozen input. Separate unchanged numbers, representation conversions and missing
   normalization in the repair counts. Verify metadata against its reviewed profile.
5. Screen source coverage and content using suitable frequency/bounds. Determine
   whether issues are formatting, legitimate source sparsity, or evidence of a bad
   download/extraction/calculation. Recommend a repull only with supporting evidence.
6. Hand off exact corrected/unchanged file selections and fingerprints, coverage,
   per-table outcomes and unresolved issues to merger. Send source-level problems
   back to preprocessing. Do not blend with the base or write delivery outputs.

## Agreed normalization policy

These are the validation stage's agreed responsibilities. Implementation availability
is listed below; a policy statement does not silently enable an untested conversion.

| Problem | Intended action | Evidence or limit |
|---|---|---|
| Unambiguous decimal text, e.g. `"7.0"` | Store numeric `7.0` | Preserve token and parse convention; do not guess decimal/group separators, percentage scaling or locale |
| Stored infinity / declared infinity tokens | Store SQL NULL | Preserve original token/storage type; count affected cells; investigate widespread occurrences for upstream failures |
| Empty/whitespace observation cells | Store SQL NULL | Keep raw representation; zero stays numeric |
| Missing monadic entities | Add canonical NULL rows | Use base roster; report observed coverage separately; no dyadic cross-product padding |
| Misspelled names | Correct from supported code or reviewed mapping | Conflicts, ambiguous identities and historical geography need evidence |
| Extra monadic rows | Exclude surplus unlabeled/non-IFs rows, even populated, when the full canonical roster is already present exactly once | Verify completeness before padding; retain all excluded rows/values in external evidence and report counts; never select between canonical duplicates |
| Missing/stale endpoints | Copy first/last non-null observation values | Chronological year order; never put years in these columns |
| Equivalent unit wording | Retain the baseline's exact unit label | Establish dimension, scale, currency, price basis and denominator equivalence first |
| DataDict schema/storage mismatch | Normalize to the base's field-specific declarations | Preserve explicit blanks; numeric blanks become NULL; retain absent baseline fields without defaults |
| DataDict table-name mismatch | Return to preprocessing with the broken link identified | Similar spelling alone is insufficient evidence |

`billions`, `billions of dollars` and `$Billions` may be equivalent for a confirmed
monetary series. Bare `billions` is not a universal dollar unit. Real differences
in currency, scale or price basis go to preprocessing. Canonicalizing a unit label
does not authorize rescaling values. A new series has no baseline label; use its
reviewed metadata specification.

For numeric parsing, define supported syntax and reject overflow/underflow or
unacceptable precision loss rather than accidentally mapping a finite token to
infinity/zero. Store values using the established IFs numeric representation;
never round to DataDict display precision. Arbitrary text, BLOBs, unknown sentinels
and NaN tokens need an explicit source policy. The infinity rule does not authorize
turning negative values, zeros or numeric sentinels into missingness.

## Implemented behavior and remaining scope

`repair-imports` / `repair-verify` implement roster/identity/endpoint/blank repairs,
series schema enforcement, strict decimal/scientific-text conversion, infinity
normalization, DataDict schema/storage normalization and case/whitespace-only unit
label retention. Frozen evidence records every declared conversion and schema change.
DataDict declarations come from the selected base's DataDict table and apply equally
to standalone and embedded DataDict tables. The repair command writes embedded
DataDict in new import copies; it never edits the base pair.

Read `preprocessing-issues.json` and the report for unresolved identities, units,
links and metadata/schema problems. Return these to the data preprocessing workflow
for evidence-based rework and a new formatted import. Do not infer identities from
row order, guess table links or rescale values in validation. Unresolved extra rows
need rework only when the complete, unique canonical roster cannot be established;
the approved surplus-row rule above handles extras after completeness is proven.
Recognizable identity conflicts and dyadic tables remain outside that exclusion rule.
A return for rework
does not by itself mean a new download is necessary.

The command remains a component of the broader validation workflow. Its `prepared`
status is not proof of complete source-specific metadata or statistical review.
Broader unit synonyms require preprocessing to establish their meaning and produce
the base label; the automatic rule currently handles case/whitespace differences.
[policy.template.json](policy.template.json) remains a review/design template, not
a CLI argument. Do not relax `delivery_core.exact_series` or its unit equality check
to bypass validation.
The legacy `validate` CLI command is a different request-based workflow with old
approval templates; it is not the entry point for this stage.

## Outputs and starting request

For statistical screening, run `quality-evidence` with the selected base/imports and
requested table scope; see [local quality evidence](../../docs/quality-evidence.md).
Use reviewed source-specific settings where available. Read `checks.csv` as well as
findings: insufficient evidence is not a pass. Keep the new package and fingerprints
in the handoff. Humans can review directly; AI interpretation is optional and separate.
This command does not replace repair verification, identity or metadata review.

Deliver corrected imports plus the frozen originals, policy/metadata review, repair
log, numerical/metadata verification, coverage and repull assessment. Table outcomes
are ready, ready with statistical findings, unresolved, or repull required.

“Use validation on [imports] against [base], write new versions and evidence to
[output], and report any source issues. Do not merge.”

### Obsolete names in the baseline

If a canonical import conflicts only with obsolete names in a baseline table,
compare the original country codes against the pinned canonical roster. Report
old name, unchanged code and canonical name. Do not relabel the import with obsolete
names or recommend a raw-data repull solely for this baseline naming issue.
For user-authorized same-code name modernization, the scoped baseline-view option
in [delivery workflow](../../docs/delivery-workflow.md#explicit-baseline-country-name-modernization)
records and verifies the mapping while preserving the original base. Unknown codes,
canonical name/code contradictions and geographic ambiguities still require review.
