"""Cleanup tests use disposable synthetic deliveries only."""
from pathlib import Path
from unittest.mock import patch
import unittest
import test_delivery as fixtures
from ifs_pipeline.delivery import consolidate_delivery, compare_delivery, refresh_delivery_logs
from ifs_pipeline.delivery_core import REPORT, DATABASES, fingerprints
from ifs_pipeline.storage import PipelineError, sha256
from ifs_pipeline.janitor import clean_delivery


class JanitorTests(unittest.TestCase):
    setUp = fixtures.DeliveryTests.setUp
    source = fixtures.DeliveryTests.source
    run_folder = fixtures.DeliveryTests.run_folder

    def tearDown(self):
        self.assertEqual(self.before, fingerprints(self.base))

    def merged(self):
        self.source()
        return self.run_folder(consolidate_delivery(self.delivery))

    def test_preview_cleanup_preservation_and_replay(self):
        run = self.merged()
        unknown = run / 'work' / 'unknown.txt'
        unknown.write_text('keep')
        before = {p: sha256(p) for p in self.delivery.rglob('*') if p.is_file()}
        plan = clean_delivery(self.delivery)
        self.assertEqual(len(plan['entries']), 2)
        self.assertEqual(before, {p: sha256(p) for p in before})
        result = clean_delivery(self.delivery, execute=True)
        removed = {self.delivery / REPORT / e['path'] for e in plan['entries']}
        self.assertEqual(result['removed_bytes'], plan['candidate_bytes'])
        for p, digest in before.items():
            if p in removed:
                self.assertFalse(p.exists())
            else:
                self.assertEqual(sha256(p), digest)
        self.assertTrue(compare_delivery(self.delivery)['verified'])
        refresh_delivery_logs(self.delivery)
        self.assertEqual(clean_delivery(self.delivery, execute=True)['status'], 'nothing_to_clean')
        self.assertEqual(consolidate_delivery(self.delivery)['status'], 'completed')

    def test_changed_work_blocks_all_deletion(self):
        run = self.merged()
        (run / 'work' / DATABASES[1]).write_bytes(b'changed')
        with self.assertRaises(PipelineError):
            clean_delivery(self.delivery, execute=True)
        self.assertTrue((run / 'work' / DATABASES[0]).exists())

    def test_pending_publication_is_not_recovered_by_cleanup(self):
        run = self.merged()
        pending = self.delivery / REPORT / 'pending.json'
        pending.write_text('{}')
        with self.assertRaisesRegex(PipelineError, 'Pending publication'):
            clean_delivery(self.delivery, execute=True)
        self.assertEqual(pending.read_text(), '{}')
        self.assertTrue((run / 'work' / DATABASES[0]).exists())

    def test_changed_output_or_evidence_blocks_cleanup(self):
        run = self.merged()
        (run / 'changes.csv').write_text('changed')
        with self.assertRaises(PipelineError):
            clean_delivery(self.delivery, execute=True)
        self.assertTrue((run / 'work' / DATABASES[0]).exists())

    def test_multiple_runs_and_skipped_inputs_are_preserved(self):
        self.source()
        self.source('IFsDataImport_Broken.db', table='SeriesBroken', units='')
        first = self.run_folder(consolidate_delivery(self.delivery))
        second = self.run_folder(consolidate_delivery(self.delivery))
        report = self.delivery / REPORT
        frozen = {p: sha256(p) for p in (report / 'imports').rglob('*.db')}
        result = clean_delivery(self.delivery, execute=True)
        self.assertEqual(len(result['removed']), 4)
        for p, digest in frozen.items():
            self.assertEqual(sha256(p), digest)
        self.assertEqual(compare_delivery(self.delivery)['status'], 'completed_with_skips')

    def test_changed_final_pair_blocks_cleanup(self):
        run = self.merged()
        (self.delivery / DATABASES[0]).write_bytes(b'changed')
        with self.assertRaises(PipelineError):
            clean_delivery(self.delivery, execute=True)
        self.assertTrue((run / 'work' / DATABASES[0]).exists())

    def test_interrupted_cleanup_can_resume(self):
        self.merged()
        unlink = Path.unlink
        count = []
        def interrupted(path, *args, **kwargs):
            if path.parent.name == 'work':
                if count:
                    raise OSError('interrupted')
                count.append(path)
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                clean_delivery(self.delivery, execute=True)
        self.assertTrue(compare_delivery(self.delivery)['verified'])
        self.assertEqual(len(clean_delivery(self.delivery, execute=True)['removed']), 1)

    def test_linked_work_is_rejected(self):
        run = self.merged()
        path = run / 'work' / DATABASES[0]
        outside = self.root / 'outside.db'
        path.replace(outside)
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('Host cannot create symlinks')
        with self.assertRaises(PipelineError):
            clean_delivery(self.delivery, execute=True)
        self.assertTrue(outside.exists())
