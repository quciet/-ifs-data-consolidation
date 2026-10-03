"""On-demand removal of verified redundant report working databases."""
from .delivery_core import REPORT, DATABASES, PARENT, resolve_folder, locked
from .storage import PipelineError, read_json, write_json, sha256, new_id, now, check_idle_database
from .retention import safe, check, assert_mutable


def clean_delivery(delivery, execute=False, parent=PARENT):
    from .delivery import verify_recorded_delivery
    delivery = resolve_folder(delivery, parent)
    report = safe(delivery, REPORT)
    with locked(delivery):
        if safe(report, 'pending.json').exists():
            raise PipelineError('Pending publication must finish before cleanup')
        assert_mutable(delivery)
        verify_recorded_delivery(delivery)
        entries = []
        for run in sorted(safe(report, 'runs').iterdir()):
            run = safe(report, run.relative_to(report))
            manifest = safe(report, (run / 'run.json').relative_to(report))
            if not run.is_dir() or not manifest.is_file():
                continue
            saved = read_json(manifest)
            if saved.get('status') not in ('completed', 'completed_with_skips'):
                continue
            for name in DATABASES:
                path = safe(report, (run / 'work' / name).relative_to(report))
                if not path.exists():
                    continue
                digest = saved.get('output_sha256', {}).get(name)
                check_idle_database(path)
                check(path, digest)
                entries.append(dict(path=path.relative_to(report).as_posix(),
                                    sha256=digest, bytes=path.stat().st_size))
        result = dict(status='planned', entries=entries,
                      candidate_bytes=sum(e['bytes'] for e in entries), removed_bytes=0)
        if not execute or not entries:
            if execute:
                result['status'] = 'nothing_to_clean'
            return result
        journal = safe(report, 'cleanup/' + new_id() + '.json')
        result.update(status='cleaning', created_at=now(), removed=[])
        write_json(journal, result)
        for entry in entries:
            path = safe(report, entry['path'])
            check_idle_database(path)
            check(path, entry['sha256'])
            path.unlink()
            result['removed'].append(entry['path'])
            result['removed_bytes'] += entry['bytes']
            write_json(journal, result)
        verify_recorded_delivery(delivery)
        result['status'] = 'cleaned'
        write_json(journal, result)
        return {**result, 'log': str(journal)}
