"""Synthetic packaging, replay, tamper and interrupted-publication checks."""
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
import test_delivery as fixture
from ifs_pipeline.delivery import consolidate_delivery, compare_delivery
from ifs_pipeline.delivery_core import fingerprints
from ifs_pipeline.storage import readonly, records, q, sha256, read_json, PipelineError

class ImportPackagesTests(unittest.TestCase):
    setUp = fixture.DeliveryTests.setUp
    source = fixture.DeliveryTests.source
    run_folder = fixture.DeliveryTests.run_folder

    def tearDown(self):
        self.assertEqual(fingerprints(self.base), self.before)

    def partial(self):
        source = self.source(a=110,b=220)
        conn = sqlite3.connect(source)
        conn.row_factory = sqlite3.Row
        try:
            row = records(conn,'DataDict')[0]
            with conn:
                conn.execute('CREATE TABLE SeriesBad AS SELECT * FROM SeriesDemoPopulation')
                conn.execute("UPDATE SeriesBad SET [2023]='not a number'")
                for table in ['SeriesBad','SeriesMissing']:
                    extra = dict(row, Table=table, Variable=table.removeprefix('Series'))
                    conn.execute('INSERT INTO DataDict ('+','.join(map(q,extra))+') VALUES ('+','.join('?' for _ in extra)+')',list(extra.values()))
        finally:
            conn.close()
        return source

    def test_partial_keeps_applied_unchanged_table_and_replays_archive(self):
        source = self.partial()
        digest = sha256(source)
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result['status'],'completed_with_skips')
        with readonly(source) as conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}, {'DataDict',fixture.TABLE})
            self.assertEqual({r['Table'] for r in records(conn,'DataDict')},{fixture.TABLE})
            self.assertEqual(conn.execute('SELECT [2023] FROM SeriesDemoPopulation WHERE FIPS_CODE=?',('AAA',)).fetchone()[0],110)
        state = read_json(self.delivery/'Consolidation Report/delivery.json')
        archive = self.delivery/'Consolidation Report'/state['batches'][0]['archive']
        self.assertEqual(sha256(archive),digest)
        with readonly(archive) as conn:
            self.assertEqual(len(records(conn,'DataDict')),3)
        self.assertTrue(compare_delivery(self.delivery)['verified'])
        again = consolidate_delivery(self.delivery)
        self.assertEqual(len(again['batches']),1)
        self.assertTrue(compare_delivery(self.delivery)['verified'])
        consolidate_delivery(self.delivery,discard=state['batches'][0]['id'],reason='Synthetic removal')
        self.assertFalse(source.exists())
        self.assertEqual(sha256(archive),digest)
        self.assertTrue(compare_delivery(self.delivery)['verified'])

    def test_zero_applied_omits_file_but_keeps_archive(self):
        source = self.source(units='unsupported units')
        digest = sha256(source)
        consolidate_delivery(self.delivery)
        self.assertFalse(source.exists())
        state = read_json(self.delivery/'Consolidation Report/delivery.json')
        self.assertEqual(sha256(self.delivery/'Consolidation Report'/state['batches'][0]['archive']),digest)
        consolidate_delivery(self.delivery)
        self.assertFalse(source.exists())
        self.assertTrue(compare_delivery(self.delivery)['verified'])

    def test_interrupted_package_publication_recovers(self):
        source = self.partial()
        replace = Path.replace
        def interrupt(path,target):
            if path.name.endswith('.package-tmp'):
                raise OSError('Synthetic package publication interruption')
            return replace(path,target)
        with patch.object(Path,'replace',interrupt):
            with self.assertRaises(OSError):
                consolidate_delivery(self.delivery)
        self.assertTrue(compare_delivery(self.delivery)['verified'])
        with readonly(source) as conn:
            self.assertEqual(len(records(conn,'DataDict')),1)

    def test_tampered_delivery_package_is_not_reingested(self):
        source = self.partial()
        consolidate_delivery(self.delivery)
        conn=sqlite3.connect(source)
        try:
            with conn: conn.execute('UPDATE SeriesDemoPopulation SET [2023]=999')
        finally: conn.close()
        for operation in [compare_delivery,consolidate_delivery]:
            with self.assertRaisesRegex(PipelineError,'import package changed'):
                operation(self.delivery)

