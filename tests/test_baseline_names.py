import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ifs_pipeline.demo import create_demo
from ifs_pipeline.delivery import prepare_delivery, consolidate_delivery, compare_delivery
from ifs_pipeline.delivery_core import exact_series, canonical_base_names, fingerprints, baseline_name_view
from ifs_pipeline.storage import readonly, PipelineError

class BaselineNamesTests(unittest.TestCase):
    def test_replay_names_preserves_values_and_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_demo(root/'demo')
            base = root/'demo/baseline'
            table = 'SeriesDemoPopulation'
            with sqlite3.connect(base/'IFsHistSeries.db') as c:
                c.execute('CREATE TABLE SeriesRoster AS SELECT * FROM SeriesDemoPopulation')
                c.execute("UPDATE SeriesDemoPopulation SET Country='Former A' WHERE FIPS_CODE='AAA'")
            c.close()
            before = fingerprints(base)
            out = root/'delivery'
            prepare_delivery(base, out, 'SeriesRoster')
            source = out/'IFsDataImport/IFsDataImport_test.db'
            shutil.copy2(base/'DataDict.db', source)
            with sqlite3.connect(source) as c:
                c.execute('CREATE TABLE SeriesDemoPopulation (Country VARCHAR(255), FIPS_CODE VARCHAR(255), "2024" DOUBLE(53))')
                c.executemany('INSERT INTO SeriesDemoPopulation VALUES (?,?,?)', [('Example A','AAA',115.0),('Example B','BBB',None)])
            c.close()
            first = consolidate_delivery(out)
            self.assertEqual(first['status'], 'completed_with_skips')
            result = consolidate_delivery(out, normalize_base_names=[table])
            self.assertTrue(compare_delivery(out)['verified'])
            result = consolidate_delivery(out)
            self.assertTrue(compare_delivery(out)['verified'])
            with readonly(base/'IFsHistSeries.db') as b, readonly(out/'IFsHistSeries.db') as c:
                old, final = exact_series(b,table), exact_series(c,table)
                for (name,code), cells in old.rows.items():
                    key = ('Example A' if code=='AAA' else name, code)
                    for year,value in cells.items():
                        self.assertEqual(final.rows[key][year], value)
                self.assertEqual(final.rows[('Example A','AAA')]['2024'],115.0)
                run = out/'Consolidation Report/runs'/result['run']
                policy = json.loads((run/'baseline_names.json').read_text())
                policy['tables'][table][0]['before'] = 'Tampered'
                (run/'baseline_names.json').write_text(json.dumps(policy))
                with self.assertRaises(PipelineError):
                    baseline_name_view(old,b,run)
            self.assertEqual(fingerprints(base),before)

    def test_conflicting_canonical_identity_rejected(self):
        from ifs_pipeline.model import Series, KEYS
        data = Series('SeriesX', KEYS['monadic'], ['2020'], {('Example B','AAA'):{'2020':1.0}})
        with self.assertRaises(PipelineError):
            canonical_base_names(data, {'AAA':'Example A','BBB':'Example B'})

