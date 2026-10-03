"""Discover stage runbooks and scaffold empty workspaces; never process data."""
from pathlib import Path
import re

from .storage import PipelineError, inside, now, read_json, sha256, write_json
from .resources import resource_root

STAGES = ('preprocessing', 'validation', 'merger', 'comparison')


def workflow_catalog(root, stage=None):
    folder = resource_root(root) / 'workflows'
    catalog = read_json(folder / 'catalog.json')
    if catalog.get('schema_version') != 1 or [s['id'] for s in catalog['stages']] != list(STAGES):
        raise PipelineError('Unsupported workflow catalog')
    entries = []
    for item in catalog['stages']:
        runbook = inside(folder, item['runbook'])
        if not runbook.is_file():
            raise PipelineError(f'Missing workflow runbook: {runbook}')
        entries.append({**item, 'runbook': str(runbook)})
    if stage is not None:
        if stage not in STAGES:
            raise PipelineError(f'Unknown workflow: {stage}')
        return next(s for s in entries if s['id'] == stage)
    roles = []
    for role in catalog.get('roles', []):
        runbook = inside(folder, role['runbook'])
        if not runbook.is_file():
            raise PipelineError(f'Missing role runbook: {runbook}')
        roles.append({**role, 'runbook': str(runbook)})
    return {'schema_version': 1, 'guide': str(folder / 'README.md'), 'stages': entries, 'roles': roles}


def _snapshot_markdown(path):
    """Keep local links usable when moving a runbook into a stage workspace."""
    text = path.read_text(encoding='utf-8')
    def link(match):
        target = match[2]
        if '://' in target or target.startswith('#'):
            return match[0]
        resolved = (path.parent / target).resolve().as_posix()
        return f'[{match[1]}](<{resolved}>)'
    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, text)


def init_workflow(root, stage, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    entry = workflow_catalog(root, stage)
    definitions = resource_root(root) / 'workflows'
    if output.exists() or output.is_relative_to(definitions) or definitions.is_relative_to(output):
        raise PipelineError('Use a new workspace outside workflow definitions')
    handoff = read_json(definitions / 'shared/handoff.template.json')
    sources = {
        'WORKFLOW.md': Path(entry['runbook']),
        'HANDOFF.md': definitions / 'shared/HANDOFF.md',
        'issues.csv': definitions / 'shared/issues.template.csv',
    }
    snapshots = {name: _snapshot_markdown(path) if name.endswith('.md') else path.read_text(encoding='utf-8')
                 for name, path in sources.items()}
    handoff.update(stage=stage, status='not_started', created_at=now(), next_stage=entry['next_stage'])
    # Read all templates before creating the destination. Existing paths are never
    # overwritten, even if they contain a previously interrupted workspace.
    output.mkdir(parents=True, exist_ok=False)
    for name, content in snapshots.items():
        (output / name).write_text(content, encoding='utf-8')
    write_json(output / 'handoff.json', handoff)
    write_json(output / 'workflow-definition.json', {
        'schema_version': 1, 'stage': entry, 'workspace_kind': 'scaffold_only',
        'source_definitions': {str(path): sha256(path) for path in sources.values()},
        'snapshots': {name: sha256(output / name) for name in snapshots},
    })
    (output / 'notes.md').write_text(
        f'# {entry["title"]} workspace\n\nStatus: not started. No data commands have run.\n\n'
        'Record the authorized task, source paths, base, delivery and exclusions in handoff.json. '
        'Read WORKFLOW.md and HANDOFF.md; use issues.csv for findings. '
        'Do not mark empty fields as verified or ready.\n\n'
        f'Maintained instructions: [{entry["title"]}](<{Path(entry["runbook"]).as_posix()}>).\n',
        encoding='utf-8')
    return {'status': 'not_started', 'stage': stage, 'workspace': str(output),
            'handoff': str(output / 'handoff.json'), 'data_processed': False}
