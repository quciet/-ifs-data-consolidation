"""Recovery compatibility for deliveries archived by the retired review role.

Native delivery manifests are never rewritten by retention. Compressed evidence
is temporarily restored for the existing exact-value verifier.
"""
from contextlib import contextmanager
from pathlib import Path
import gzip
import hashlib

from .delivery_core import REPORT, PARENT, DATABASES, resolve_folder, locked
from .storage import PipelineError, read_json, write_json, sha256, new_id, now, check_idle_database


def safe(root, relative):
    root = Path(root).absolute()
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise PipelineError('Unsafe retention path: ' + str(relative))
    path = root / rel
    for part in (root, *reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024:
                raise PipelineError('Retention refuses symbolic links or junctions: ' + str(part))
    if not path.resolve().is_relative_to(root.resolve()):
        raise PipelineError('Retention path escapes report folder')
    return path


def home(delivery):
    return safe(delivery, REPORT + '/retention')


def active(delivery):
    path = home(delivery) / 'state.json'
    return read_json(path) if path.exists() else {}


def assert_mutable(delivery):
    if active(delivery).get('archive'):
        raise PipelineError('Delivery evidence is archived; run delivery-restore before merge/discard.')


def check(path, digest):
    if not path.is_file() or sha256(path) != digest:
        raise PipelineError('Retention fingerprint mismatch: ' + str(path))


def identity(delivery):
    from .delivery import load_state, assert_output
    from .delivery_packages import verify_visible
    state = load_state(delivery)
    assert_output(delivery, state)
    verify_visible(delivery, state)
    return sha256(delivery / REPORT / 'delivery.json')


def archive_manifest(delivery):
    aid = active(delivery).get('archive')
    if not aid:
        return None, None
    path = safe(home(delivery), 'archives/' + aid + '/manifest.json')
    manifest = read_json(path)
    decision = read_json(safe(home(delivery), 'approvals/' + manifest['approval'] + '.json'))
    folder, review = checked_review(delivery, decision['review'])
    check(folder / 'manifest.json', decision['review_sha256'])
    if decision['decision'] != 'approve-and-archive' or manifest['entries'] != read_json(folder / 'plan.json')['entries'] or manifest['identity'] != review['identity']:
        raise PipelineError('Archive journal does not match approved plan')
    for name, info in manifest.get('objects', {}).items():
        check(safe(home(delivery), 'objects/' + name), info['sha256'])
    return path, manifest


def replacement(delivery, entry, target=None):
    """Verify replacement bytes, optionally materializing them at target."""
    if entry['strategy'] == 'drop_work':
        return
    temp = target.with_name(target.name + '.' + new_id() + '.restore-tmp') if target else None
    output = temp.open('xb') if temp else None
    digest = hashlib.sha256()
    count = 0
    try:
        if entry['strategy'] == 'alias':
            stream = safe(delivery, entry['alias']).open('rb')
        else:
            stream = gzip.open(safe(home(delivery), 'objects/' + entry['sha256'] + '.gz'), 'rb')
        with stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                count += len(block)
                if output:
                    output.write(block)
        if digest.hexdigest() != entry['sha256'] or count != entry['bytes']:
            raise PipelineError('Archived evidence fingerprint mismatch: ' + entry['path'])
    finally:
        if output:
            output.close()
            if digest.hexdigest() == entry['sha256'] and count == entry['bytes']:
                if target.exists():
                    temp.unlink()
                    raise PipelineError('Restore target appeared during materialization: ' + str(target))
                temp.replace(target)
            else:
                temp.unlink()


@contextmanager
def retained_access(delivery):
    _, manifest = archive_manifest(delivery)
    created = []
    try:
        if manifest:
            if identity(delivery) != manifest['identity']:
                raise PipelineError('Archived release has changed')
            state = read_json(delivery / REPORT / 'delivery.json')
            needed = {Path(b['archive']).as_posix() for b in state['batches']}
            prefix = 'runs/' + state['last_run'] + '/'
            needed.update(prefix + n for n in state['report_sha256'])
            for entry in manifest['entries']:
                rel = entry['path'].replace('\\', '/')
                if rel not in needed and not rel.startswith(prefix + 'packaged-imports/'):
                    continue
                path = safe(delivery / REPORT, rel)
                if path.exists():
                    check(path, entry['sha256'])
                elif entry['strategy'] != 'drop_work':
                    path.parent.mkdir(parents=True, exist_ok=True)
                    created.append((path, entry))
                    replacement(delivery, entry, path)
        yield
    finally:
        for path, entry in reversed(created):
            if path.exists():
                check(path, entry['sha256'])
                path.unlink()






def checked_review(delivery, rid):
    folder = safe(home(delivery), 'reviews/' + rid)
    manifest = read_json(folder / 'manifest.json')
    if identity(delivery) != manifest['identity']:
        raise PipelineError('Review is stale: delivery has changed')
    for name, digest in manifest['files'].items():
        check(safe(folder, name), digest)
    for name, digest in manifest.get('evidence', {}).items():
        check(safe(delivery / REPORT, name), digest)
    return folder, manifest








def verify_archive(delivery, parent=PARENT):
    from .delivery import verify_recorded_delivery
    delivery = resolve_folder(delivery, parent)
    with locked(delivery):
        _, manifest = archive_manifest(delivery)
        if not manifest:
            raise PipelineError('No active archive')
        for e in manifest['entries']:
            replacement(delivery, e)
        with retained_access(delivery):
            verify_recorded_delivery(delivery)
        return {'status': 'verified', 'verified': True, 'archive': manifest['archive']}


def restore_delivery(delivery, parent=PARENT, archive=None):
    from .delivery import verify_recorded_delivery
    delivery = resolve_folder(delivery, parent)
    with locked(delivery):
        journal, manifest = archive_manifest(delivery)
        if not manifest or (archive and archive != manifest['archive']):
            raise PipelineError('No matching active archive')
        if identity(delivery) != manifest['identity']:
            raise PipelineError('Archived release has changed')
        for e in manifest['entries']:
            replacement(delivery, e)
        for e in manifest['entries']:
            if e['strategy'] == 'drop_work':
                continue
            path = safe(delivery / REPORT, e['path'])
            if path.exists():
                check(path, e['sha256'])
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                replacement(delivery, e, path)
        verify_recorded_delivery(delivery)
        manifest['status'] = 'restored'
        write_json(journal, manifest)
        write_json(home(delivery) / 'state.json', {})
        return {'status': 'restored', 'archive': manifest['archive']}
