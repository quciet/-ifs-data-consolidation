"""Local preparation output defaults and non-recursive input snapshots."""
import json
import os
from pathlib import Path

MARKER = '.ifs-preparation.json'


def is_generated_package(folder):
    marker = Path(folder) / MARKER
    if marker.is_file():
        try:
            if json.loads(marker.read_text(encoding='utf-8')).get('kind') == 'ifs_preparation_output':
                return True
        except (ValueError, OSError, AttributeError):
            pass
    # Recognize the native evidence of packages created before marker support.
    native = Path(folder) / 'Working Files' / 'preparation_manifest.json'
    if native.is_file():
        try:
            data = json.loads(native.read_text(encoding='utf-8'))
            return data.get('stage') == 'preprocessing' and isinstance(data.get('output'), dict)
        except (ValueError, OSError, AttributeError):
            pass
    return False


def create_preparation_workspace(source, output=None):
    """Reserve a NEW output folder and return the pre-creation source file list.

    Default is inside source; explicit output retains normal caller-relative path
    semantics. Earlier marked outputs are not raw inputs. No source files are
    changed. Callers copy this frozen list, never recurse over source afterward.
    """
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError(f'Source folder does not exist: {source}')
    if output is None:
        stem = 'IFsDataImport'
        output = source / stem
        suffix = 2
        while output.exists():
            output = source / f'{stem}_{suffix:02d}'
            suffix += 1
    output = Path(output).resolve()
    if output == source or output in source.parents:
        raise ValueError('Preparation output must not be the source folder or its ancestor')
    if output.exists():
        raise FileExistsError(f'Preparation output already exists: {output}')
    inputs = []
    for current, dirs, files in os.walk(source, followlinks=False):
        folder = Path(current)
        dirs[:] = sorted(d for d in dirs if not is_generated_package(folder / d))
        inputs.extend(folder / name for name in sorted(files))
    output.mkdir(parents=True, exist_ok=False)
    (output / MARKER).write_text(json.dumps({'kind': 'ifs_preparation_output',
                                           'source': str(source)}, indent=2) + '\n', encoding='utf-8')
    return output, inputs
