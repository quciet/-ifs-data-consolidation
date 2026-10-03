"""Optional path defaults; no managed user workspace is required."""
import os
from pathlib import Path
from .storage import read_json, PipelineError


def path_settings(config=None):
    if config is not None:
        config = Path(config).resolve()
    else:
        local = Path(__file__).resolve().parents[2] / 'paths.local.json'
        config = local if (local.parent / 'pyproject.toml').is_file() and local.is_file() else None
    data = read_json(config) if config else {}
    if not isinstance(data, dict) or set(data) - {'delivery_root', 'source_library'}:
        raise PipelineError('Path configuration supports only delivery_root and source_library')
    result = {}
    for key in ('delivery_root', 'source_library'):
        value = os.environ.get('IFS_' + key.upper())
        anchor = Path.cwd()
        if not value:
            value = data.get(key)
            anchor = config.parent if config else anchor
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise PipelineError('Path setting must be a nonempty string: ' + key)
            path = Path(value).expanduser()
            result[key] = str((path if path.is_absolute() else anchor / path).resolve())
    return result
