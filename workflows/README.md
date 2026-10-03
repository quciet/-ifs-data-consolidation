# IFs data workflows

Installed users can run `ifs-data workflows` from any directory. Runbooks and
templates ship with the package. See [installation and path defaults](../docs/user-guide/installation.md).
The source library location may be supplied through path configuration; the D: paths
below describe this repository owner's local arrangement, not an installation requirement.

Start here to run or improve one part of an IFs data update. These are four
independently editable workflows. An agent can follow a workflow in the current
task or be assigned that bounded role when delegation is requested. There is no
background agent service, automatic task creation, or scheduled execution.

| Workflow | Starts with | Produces | Next workflow |
|---|---|---|---|
| [1. Data preprocessing](preprocessing/WORKFLOW.md) | Raw source files and source/IFs requirements | Prepared IFsDataImport database, source recipe and DataDict review | Validation |
| [2. Import validation and repair](validation/WORKFLOW.md) | Prepared imports and the intended base case | Validated/corrected imports, repair evidence, unresolved issues or repull recommendation | Merger; preprocessing for source problems |
| [3. Data merger](merger/WORKFLOW.md) | Selected imports, base, delivery and precedence | Updated IFsHistSeries/DataDict pair and batch evidence | Comparison |
| [4. Post-merge comparison](comparison/WORKFLOW.md) | Merged pair, base, incoming imports and run evidence | Updated-table comparison and routed findings | Validation, preprocessing or merger as appropriate |

Normal flow: **preprocessing → validation → merger → comparison**. Already formatted
imports enter at validation. An existing delivery can enter at comparison. A task
to improve a workflow changes its instructions/code and uses synthetic fixtures;
it does not authorize running that workflow on a previous real delivery. An actual
data-processing task follows only the stages authorized by the user. Continue
through all stages when the user requests an end-to-end update.

## Use a workflow

You can ask: "Use the validation workflow on these imports against this base",
or "Improve the comparison workflow; do not process real data."

```powershell
.\ifs.ps1 workflows
.\ifs.ps1 workflows validation
.\ifs.ps1 workflow-init --stage validation --output 'reviews\my-validation-task'
```

`workflows` reads the catalog and identifies the runbook, current commands and
extension points. `workflow-init` creates a new, empty stage workspace with a
runbook snapshot, shared handoff instructions, a handoff template, an issues CSV
and notes. It opens no databases, downloads nothing, and runs no stage commands.
The stage starts as `not_started`; creating a workspace is not validation.

Each stage workspace is a record of the work, not an execution engine. Existing
commands remain the implementations where available. Source-specific adapters and
future validator enhancements belong to their respective stages. The stage
runbooks explicitly separate working code from planned capabilities.

## Shared operating rules

- Use `D:\IFs Source Library` through the [pointer index](../source-guides/README.md) for reusable
  methodology. Model-led preprocessing creates or updates each source's guide as
  evidence accumulates, with release scope and dated changes. Snapshot the version
  used in each run; downstream stages can route new findings for guide updates.
  Scripts and empty workflow scaffolds do not automatically author these guides.
- Supplied files are the sole new-data/documentation source unless the user
  explicitly requests online fetching. Source errors, reference URLs and repull
  recommendations do not authorize browsing, APIs or downloads. Use the supplied
  base read-only for documented inference, never for filling preprocessing gaps.
- A supplied DataDict workbook normally defines the target indicator scope; its
  old metadata may need updating. Default raw-preparation outputs inside the
  source folder, in a new versioned subfolder, unless another path is requested.
- Preserve originals. Each processing stage writes new versions and freezes its
  inputs, recipe/profile, reference roster and relevant code identity.
- Record base, source and output fingerprints. Keep raw tokens, repair logs,
  metadata review and provenance outside IFs database schemas.
- Preprocessing owns source-specific numerical transformations. Validation owns
  declared representation and missing-value normalization. Merger copies frozen
  numerical observations exactly; comparison never edits them.
- Routine repairs supported by evidence proceed without blanket approval. Ask
  only for unresolved meaning, conflicting evidence or a consequential source
  decision that cannot be established from the task and references.
- A table can be structurally usable while it has statistical review findings.
  Process valid tables, retain unresolved structural skips, and report partial
  completion. Do not require per-table approval templates for a normal delivery.
- A source has to be pulled again only when evidence supports that recommendation:
  wrong release, incomplete download, extraction failure or broken preparation.
  Large revisions alone do not establish a need to repull.
- Revalidate after inputs, base, mapping, policy or relevant code changes. Never
  overwrite a registered batch or rewrite historical evidence to hide a correction.
- [Handoffs](shared/HANDOFF.md) name exact artifacts and scope, not just folder
  labels. Issues carry the responsible source/batch and the workflow that owns
  the next action.

## Where to make detailed improvements

Edit the appropriate `WORKFLOW.md` and its stage-specific profile/templates first,
then the implementation listed in [catalog.json](catalog.json). Keep shared
contracts stable or version them when their meaning changes. Test behavior on
synthetic data before applying it to an authorized real task.

The framework uses repair/merge/compare commands. `quality-evidence` and
`delivery-quality` add local statistical packages with separate human/AI review;
see [quality evidence](../docs/quality-evidence.md). Validation implements strict
numeric-text and infinity normalization, schema/storage checks and case/whitespace
unit label retention. Broader CSV/Excel/PDF preparation, semantic unit synonyms,
automatic repull decisions and a dedicated three-way comparison summary remain
extension points; assignment to a stage does not imply full implementation.

## Optional janitor role

The [janitor](janitor/WORKFLOW.md) removes verified redundant work files under
Consolidation Report only when requested. It is not a fifth processing stage and
does not review or approve releases. Use `delivery-clean --delivery DELIVERY` to
preview, or add `--execute` to perform requested cleanup.
