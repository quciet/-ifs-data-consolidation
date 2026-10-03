"""Read maintained checkout resources or their installed package snapshots."""
from pathlib import Path
from .storage import PipelineError


def resource_root(root=None):
    if root is not None and (Path(root) / 'workflows/catalog.json').is_file():
        return Path(root).resolve()
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / 'pyproject.toml').is_file() and (checkout / 'workflows/catalog.json').is_file():
        return checkout
    bundled = Path(__file__).resolve().parent / 'resources'
    if not (bundled / 'workflows/catalog.json').is_file():
        raise PipelineError('Package resources are missing; reinstall from a complete distribution')
    return bundled


def datagator_lookup(root):
    local = Path(root) / 'reference/datagator'
    # An incomplete local override must fail, not silently mix reference versions.
    folder = local if local.exists() else resource_root() / 'reference/datagator'
    return folder / 'country_data.json'
