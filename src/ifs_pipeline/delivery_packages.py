"""Applied-table delivery copies; frozen batch archives remain replay authority."""
import shutil
import sqlite3
from collections import Counter
from .storage import PipelineError, check_idle_database, inside, q, readonly, records, sha256, tables, write_json, read_json


def verify_visible(delivery, state):
    for filename, expected in state.get('import_packages', {}).items():
        path = inside(delivery / 'IFsDataImport', filename)
        check_idle_database(path)
        actual = sha256(path) if path.exists() else None
        if actual != expected:
            raise PipelineError(f'Delivery import package changed: {filename}')


def stage_packages(delivery, state, run):
    entries = []
    folder = run / 'packaged-imports'
    folder.mkdir()
    for batch in state['batches']:
        source = inside(delivery / 'Consolidation Report', batch['archive'])
        if sha256(source) != batch['sha256']:
            raise PipelineError('Archived input changed before packaging')
        selected = set(batch.get('applied_tables', [])) if not batch['excluded'] else set()
        target = inside(delivery / 'IFsDataImport', batch['file'])
        check_idle_database(target)
        before = sha256(target) if target.exists() else None
        digest = None
        removed = []
        if selected:
            packaged = inside(folder, batch['file'])
            shutil.copy2(source, packaged)
            conn = sqlite3.connect(packaged)
            try:
                allowed = selected | {'DataDict'}
                with conn:
                    # Disable executable schema objects before pruning metadata.
                    objects = list(conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('view','trigger')"))
                    for kind, name in objects:
                        conn.execute(f'DROP {kind} {q(name)}')
                    removed = sorted(tables(conn) - allowed - {'sqlite_sequence'})
                    for name in removed:
                        conn.execute(f'DROP TABLE {q(name)}')
                    placeholders = ','.join('?' for _ in selected)
                    conn.execute(f'DELETE FROM DataDict WHERE "Table" IS NULL OR "Table" NOT IN ({placeholders})', sorted(selected))
                if removed or objects or sha256(packaged) != batch['sha256']:
                    conn.execute('VACUUM')
                if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise PipelineError('Packaged import integrity check failed')
            finally:
                conn.close()
            # Verify the retained rows, storage types and schemas against the archive.
            with readonly(source) as original, readonly(packaged) as final:
                if tables(final) - {'sqlite_sequence'} != allowed:
                    raise PipelineError('Packaged import contains unexpected tables')
                for table in allowed:
                    old = records(original, table)
                    if table == 'DataDict':
                        old = [r for r in old if r.get('Table') in selected]
                    signature = lambda rows: Counter(repr(tuple(r.items())) for r in rows)
                    if signature(old) != signature(records(final, table)):
                        raise PipelineError(f'Packaging changed retained contents: {table}')
                    schema = lambda c: c.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('table',table)).fetchone()[0]
                    if schema(original) != schema(final):
                        raise PipelineError(f'Packaging changed retained schema: {table}')
            digest = sha256(packaged)
        entries.append(dict(file=batch['file'], batch=batch['id'], archive=batch['archive'],
                            original_sha256=batch['sha256'], previous_sha256=before, sha256=digest,
                            tables=sorted(selected), removed_tables=removed,
                            skipped_tables=sorted(t for t,o in batch.get('table_outcomes',{}).items() if o['status']=='skipped'),
                            omitted=not selected, excluded=batch['excluded']))
    write_json(run / 'import-packages.json', {'entries': entries})
    state['import_packages'] = {e['file']:e['sha256'] for e in entries}
    report = run / 'report.md'
    if report.exists():
        with report.open('a', encoding='utf-8') as handle:
            handle.write('\n## Delivery import packaging\n\n'
                         'IFsDataImport contains only applied tables and their matching source DataDict rows. '
                         'Applied tables with unchanged values remain included. Files with no applied tables '
                         'and excluded batches are omitted. Full frozen inputs remain in the audit archive '
                         'for replay and skipped-table investigation. See import-packages.json for per-file '
                         'scope and original/packaged fingerprints.\n')


def check_package_publication(delivery, run):
    path = run / 'import-packages.json'
    if not path.exists():
        return []
    entries = read_json(path)['entries']
    for entry in entries:
        if entry['sha256'] is not None:
            source = inside(run / 'packaged-imports', entry['file'])
            if sha256(source) != entry['sha256']:
                raise PipelineError('Packaged import evidence changed')
        target = inside(delivery / 'IFsDataImport', entry['file'])
        check_idle_database(target)
        actual = sha256(target) if target.exists() else None
        if actual not in (entry['previous_sha256'], entry['sha256']):
            raise PipelineError('Delivery import changed during publication')
    return entries


def publish_packages(delivery, run, entries):
    for entry in entries:
        target = inside(delivery / 'IFsDataImport', entry['file'])
        if entry['sha256'] is None:
            if target.exists():
                target.unlink()
        elif not target.exists() or sha256(target) != entry['sha256']:
            temp = target.with_name(target.name + '.package-tmp')
            shutil.copy2(inside(run / 'packaged-imports',entry['file']), temp)
            if sha256(temp) != entry['sha256']:
                raise PipelineError('Import package copy verification failed')
            temp.replace(target)
