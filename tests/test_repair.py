"""Formatting preparation must preserve observations and expose uncertainty."""
import io
import json
from contextlib import redirect_stdout, contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ifs_pipeline.cli import main
from ifs_pipeline.repair import repair_imports, verify_preparation
from ifs_pipeline.storage import PipelineError, readonly, records, sha256, write_json
from ifs_pipeline.delivery import prepare_delivery, consolidate_delivery, compare_delivery
from ifs_pipeline import repair
from ifs_pipeline.normalization import integer_value, numeric_value


@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'base'
        self.base.mkdir()
        with database(self.base / 'IFsHistSeries.db') as c:
            c.execute('CREATE TABLE SeriesPopulation (Country TEXT, FIPS_CODE TEXT, "2020" REAL)')
            c.executemany('INSERT INTO SeriesPopulation VALUES (?,?,?)', [('Example A', 'AAA', 5), ('Example B', 'BBB', 6)])
        with database(self.base / 'DataDict.db') as c:
            c.execute('CREATE TABLE DataDict (Variable TEXT, "Table" TEXT, Definition TEXT, Units TEXT, Source TEXT, Notes TEXT)')
        (self.root / 'inputs').mkdir()
        self.source = self.root / 'inputs/IFsDataImport_A.db'
        with database(self.source) as c:
            c.execute('CREATE TABLE DataDict (Variable TEXT, "Table" TEXT, Definition TEXT, Units TEXT, Source TEXT, Notes TEXT)')
            c.execute("INSERT INTO DataDict VALUES ('Test','SeriesTest','Test definition','persons','Example','')")
            c.execute('CREATE TABLE Auxiliary (value TEXT)')
            c.execute("INSERT INTO Auxiliary VALUES ('keep')")
            c.execute('CREATE TABLE SeriesTest (Country TEXT, FIPS_CODE TEXT, "2022" DOUBLE, "2020" DOUBLE)')
            c.execute("INSERT INTO SeriesTest VALUES ('Exampel A',' AAA ',0,2.5)")
        self.output = self.root / 'prepared'
        lookup = self.root / 'reference/datagator/country_data.json'
        write_json(lookup, [dict(ifs_name='Example A', ifs_fipscode='AAA', alternative_names=['Old combined territory']),
                            dict(ifs_name='Example B', ifs_fipscode='BBB', alternative_names=['Alias B'])])
        write_json(lookup.with_name('provenance.json'), dict(local_sha256=sha256(lookup)))

    def edit(self, query, args=()):
        with database(self.source) as c:
            c.execute(query, args)

    def run_repair(self, **kwargs):
        before = sha256(self.source)
        result = repair_imports(self.root, self.base, [self.source], self.output, **kwargs)
        self.assertEqual(sha256(self.source), before)
        self.manifest = json.loads((self.output / 'manifest.json').read_text())
        return result

    def corrected(self):
        return self.output / 'corrected' / self.manifest['imports'][0]['corrected']

    def test_repair_identity_padding_endpoints_and_exact_zero(self):
        result = self.run_repair()
        self.assertEqual(result['repaired_tables'], 1)
        with readonly(self.corrected()) as c:
            data = records(c, 'SeriesTest')
            self.assertEqual(data[0], dict(Country='Example A', FIPS_CODE='AAA', **{'2020': 2.5, '2022': 0.0, 'Earliest': 2.5, 'MostRecent': 0.0}))
            self.assertEqual(data[1]['Country'], 'Example B')
            self.assertIsNone(data[1]['2020'])
            self.assertEqual(records(c, 'DataDict')[0]['Notes'], '')
            self.assertEqual(records(c, 'Auxiliary')[0]['value'], 'keep')
        self.assertTrue(verify_preparation(self.output)['verified'])
        entry = self.manifest['imports'][0]['tables'][0]
        self.assertEqual(entry['observations_preserved'], 2)
        self.assertEqual(entry['source_rows'], 1)
        self.assertEqual(entry['output_rows'], 2)

    def test_blank_evidence_and_empty_padding(self):
        self.edit('UPDATE SeriesTest SET "2020"=?', (' \t ',))
        self.edit('INSERT INTO SeriesTest VALUES (?,?,?,?)', ('', None, None, ''))
        self.run_repair()
        evidence = [json.loads(x) for x in (self.output / 'evidence/IFsDataImport_A.jsonl').read_text().splitlines()]
        blank = next(e for e in evidence if e.get('rule') == 'blank_to_null')
        self.assertEqual(blank['before'], ' \t ')
        self.assertIn('remove_empty_padding', [e.get('rule') for e in evidence])
        with readonly(self.corrected()) as c:
            row = records(c, 'SeriesTest')[0]
            self.assertEqual(row['Earliest'], 0)
            self.assertIsNone(row['2020'])

    def test_observed_unidentified_extra_is_unresolved_not_deleted(self):
        self.edit('INSERT INTO SeriesTest VALUES (NULL,NULL,0,NULL)')
        result = self.run_repair()
        self.assertEqual(result['unresolved_tables'], 1)
        self.assertEqual(result['corrected_imports'], 0)
        self.assertIn('Unresolved identity', self.manifest['imports'][0]['tables'][0]['reason'])

    def test_nonempty_endpoint_prevents_padding_deletion(self):
        self.edit('ALTER TABLE SeriesTest ADD COLUMN Earliest DOUBLE')
        self.edit('INSERT INTO SeriesTest VALUES (NULL,NULL,NULL,NULL,0)')
        self.assertEqual(self.run_repair()['unresolved_tables'], 1)

    def test_conflicting_identity_not_fixed_using_code(self):
        self.edit("UPDATE SeriesTest SET Country='Example B'")
        self.assertEqual(self.run_repair()['unresolved_tables'], 1)

    def test_duplicate_after_normalization_not_deduplicated(self):
        self.edit("INSERT INTO SeriesTest VALUES ('Example A','AAA',0,2.5)")
        self.assertEqual(self.run_repair()['unresolved_tables'], 1)
        self.assertIn('Duplicate/converging', self.manifest['imports'][0]['tables'][0]['reason'])

    def test_canonical_name_recovers_missing_code(self):
        self.edit("UPDATE SeriesTest SET Country='Example A',FIPS_CODE=NULL")
        self.assertEqual(self.run_repair()['repaired_tables'], 1)

    def test_unknown_nonempty_code_not_discarded(self):
        self.edit("UPDATE SeriesTest SET Country='Example A',FIPS_CODE='ZZZ'")
        self.assertEqual(self.run_repair()['unresolved_tables'], 1)

    def test_historical_alias_requires_actual_identity_review(self):
        self.edit("UPDATE SeriesTest SET Country='Old combined territory',FIPS_CODE='AAA'")
        self.assertEqual(self.run_repair()['unresolved_tables'], 1)
        self.assertIn('source-specific identity review', self.manifest['imports'][0]['tables'][0]['reason'])

    def test_reviewed_source_mapping_is_frozen(self):
        self.edit("UPDATE SeriesTest SET Country='Alias B',FIPS_CODE=NULL")
        mapping = self.root / 'mapping.csv'
        mapping.write_text('original_name,matched_name\nAlias B,Example B\n')
        self.assertEqual(self.run_repair(mapping=mapping, reviewed=True)['repaired_tables'], 1)
        self.assertEqual((self.output / 'reviewed_mapping.csv').read_text(), mapping.read_text())

    def test_alias_lookup_mismatch_blocks_instead_of_falling_back(self):
        lookup = self.root / 'reference/datagator/country_data.json'
        lookup.write_text('[]')
        with self.assertRaisesRegex(PipelineError, 'fingerprint'):
            self.run_repair()
        self.assertFalse(self.output.exists())

    def test_unsafe_values_are_not_repaired(self):
        for value in ('ambiguous', '1,234', '1e999', '1e-999', '9007199254740993',
                      '0.123456789012345678901', 'NaN', 9007199254740993, b'x'):
            with self.subTest(value=value):
                self.output = self.root / ('p' + str(len(list(self.root.iterdir()))))
                self.edit('DROP TABLE SeriesTest')
                self.edit('CREATE TABLE SeriesTest (Country TEXT,FIPS_CODE TEXT,"2020")')
                self.edit('INSERT INTO SeriesTest VALUES (?,?,?)', ('Example A','AAA',value))
                self.assertEqual(self.run_repair()['unresolved_tables'], 1)

    def test_decimal_text_infinities_and_zero_are_audited_and_stable(self):
        self.edit('DROP TABLE SeriesTest')
        self.edit('CREATE TABLE SeriesTest (Country TEXT,FIPS_CODE TEXT,"2020","2021","2022","2023","2024","2025")')
        self.edit('INSERT INTO SeriesTest VALUES (?,?,?,?,?,?,?,?)',
                  ('Example A','AAA','7.0','-3.9e1',float('inf'),'-Infinity','0','0.1'))
        result = self.run_repair()
        self.assertEqual(result['unresolved_issues'], 0)
        self.assertEqual(result['repair_counts']['numeric_text_to_double'], 4)
        self.assertEqual(result['repair_counts']['infinity_to_null'], 2)
        with readonly(self.corrected()) as c:
            row = records(c, 'SeriesTest')[0]
            self.assertEqual([row[str(y)] for y in range(2020,2026)], [7.0,-39.0,None,None,0.0,0.1])
            self.assertEqual([row['Earliest'],row['MostRecent']], [7.0,0.1])
        evidence = [json.loads(line) for line in (self.output / 'evidence/IFsDataImport_A.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('before') == {'raw_repr': 'inf', 'storage_type': 'float'} for e in evidence))
        self.assertTrue(verify_preparation(self.output)['verified'])
        again = repair_imports(self.root,self.base,[self.corrected()],self.root/'again')
        self.assertEqual(again['corrected_imports'], 0)

    def test_declared_schema_alone_is_repaired(self):
        self.edit('DROP TABLE SeriesTest')
        self.edit('CREATE TABLE SeriesTest (Country TEXT,FIPS_CODE TEXT,"2020" REAL,Earliest REAL,MostRecent REAL)')
        self.edit("INSERT INTO SeriesTest VALUES ('Example A','AAA',1,1,1)")
        self.edit("INSERT INTO SeriesTest VALUES ('Example B','BBB',2,2,2)")
        result = self.run_repair()
        self.assertEqual(result['repair_counts'], {'normalize_series_schema': 1})
        with readonly(self.corrected()) as c:
            info = c.execute('PRAGMA table_info(SeriesTest)').fetchall()
            self.assertEqual([r['type'] for r in info], ['VARCHAR(255)','VARCHAR(255)','DOUBLE(53)','DOUBLE(53)','DOUBLE(53)'])

    def test_datadict_uses_base_types_retains_absent_fields_and_unit_label(self):
        with database(self.base / 'DataDict.db') as c:
            c.execute('ALTER TABLE DataDict ADD COLUMN CURRENCY "VARCHAR (50)"')
            c.execute('ALTER TABLE DataDict ADD COLUMN UsedInFunctions "INTEGER (1)"')
            c.execute("INSERT INTO DataDict VALUES ('Test','SeriesTest','base definition','Millions','base source','retain me','USD',1)")
        self.edit('DROP TABLE DataDict')
        self.edit('CREATE TABLE DataDict (Variable, "Table", Definition, Units, Source, CURRENCY, UsedInFunctions)')
        self.edit("INSERT INTO DataDict VALUES ('Test','SeriesTest','Test definition',' millions ','Example',1,'0')")
        result = self.run_repair()
        self.assertEqual(result['unresolved_issues'], 0)
        with readonly(self.corrected()) as c:
            row = records(c,'DataDict')[0]
            self.assertEqual(row['Notes'], 'retain me')
            self.assertEqual(row['CURRENCY'], '1')
            self.assertEqual(row['UsedInFunctions'], 0)
            self.assertEqual(row['Units'], 'Millions')
        self.assertEqual(result['repair_counts']['retain_base_unit_label'], 1)
        again = repair_imports(self.root,self.base,[self.corrected()],self.root/'again')
        self.assertEqual(again['corrected_imports'], 0)

    def test_metadata_missing_numeric_remains_null_and_invalid_integer_is_atomic(self):
        with database(self.base / 'DataDict.db') as c:
            c.execute('ALTER TABLE DataDict ADD COLUMN Flag "INTEGER (1)"')
        self.edit('ALTER TABLE DataDict ADD COLUMN Flag TEXT')
        self.edit("UPDATE DataDict SET Flag=' '")
        self.run_repair()
        with readonly(self.corrected()) as c:
            self.assertIsNone(records(c,'DataDict')[0]['Flag'])
        self.output = self.root/'badmetadata'
        self.edit("UPDATE DataDict SET Flag='1.5'")
        result = self.run_repair()
        self.assertGreater(result['unresolved_issues'], 0)
        self.assertEqual(self.manifest['imports'][0]['metadata']['status'], 'unresolved')
        with readonly(self.corrected()) as c:
            self.assertEqual(records(c,'DataDict')[0]['Flag'], '1.5')

    def test_unit_scale_and_metadata_links_are_sent_to_preprocessing(self):
        with database(self.base / 'DataDict.db') as c:
            c.execute("INSERT INTO DataDict VALUES ('Test','SeriesTest','base','millions','base','')")
        self.edit("UPDATE DataDict SET Units='billions'")
        self.edit("INSERT INTO DataDict VALUES ('Other','SeriesTypo','definition','persons','source','')")
        result = self.run_repair()
        self.assertEqual(result['status'], 'prepared_with_unresolved')
        issues = self.manifest['imports'][0]['issues']
        self.assertTrue(all(i['owner_stage']=='preprocessing' for i in issues))
        self.assertTrue(any('Unit meaning' in i['reason'] for i in issues))
        self.assertTrue(any('missing physical' in i['reason'] for i in issues))
        with readonly(self.corrected()) as c:
            self.assertEqual(records(c,'DataDict')[0]['Units'], 'billions')

    def test_integer_metadata_retains_large_exact_values_and_rejects_overflow(self):
        self.assertEqual(integer_value('9007199254740993'), 9007199254740993)
        self.assertEqual(integer_value('9223372036854775807'), 9223372036854775807)
        for value in ('9223372036854775808', '-9223372036854775809', '1.2', 'NaN', '1e999999999999999999999'):
            with self.subTest(value=value), self.assertRaises(PipelineError):
                integer_value(value)
        for value in ('-inf', '+Infinity', float('-inf')):
            self.assertIsNone(numeric_value(value)[0])
        self.assertEqual(numeric_value('-1.25e-2')[0], -0.0125)

    def test_unknown_metadata_fields_are_preserved_and_routed(self):
        self.edit('ALTER TABLE DataDict ADD COLUMN Mystery TEXT')
        self.edit("UPDATE DataDict SET Mystery='keep this evidence'")
        result = self.run_repair()
        self.assertEqual(self.manifest['imports'][0]['metadata']['status'], 'unresolved')
        self.assertGreater(result['unresolved_issues'], 0)
        with readonly(self.corrected()) as c:
            self.assertEqual(records(c, 'DataDict')[0]['Mystery'], 'keep this evidence')

    def test_source_metadata_defaults_removed_without_filling_nulls(self):
        with database(self.base / 'DataDict.db') as c:
            c.execute('ALTER TABLE DataDict ADD COLUMN Flag INTEGER')
        self.edit('ALTER TABLE DataDict ADD COLUMN Flag INTEGER DEFAULT (0)')
        self.edit('UPDATE DataDict SET Flag=NULL')
        self.run_repair()
        with readonly(self.corrected()) as c:
            self.assertIsNone(records(c,'DataDict')[0]['Flag'])
            self.assertTrue(all(r['dflt_value'] is None for r in c.execute('PRAGMA table_info(DataDict)')))
        again = repair_imports(self.root,self.base,[self.corrected()],self.root/'again')
        self.assertEqual(again['corrected_imports'], 0)

    def test_blob_metadata_identity_routes_without_aborting_other_repairs(self):
        self.edit('UPDATE DataDict SET "Table"=?', (b'bad-link',))
        result = self.run_repair()
        self.assertEqual(result['status'], 'prepared_with_unresolved')
        self.assertEqual(result['repaired_tables'], 1)
        self.assertTrue((self.output/'preprocessing-issues.json').is_file())

    def test_double_metadata_schema_has_real_storage_after_conversion(self):
        with database(self.base / 'DataDict.db') as c:
            c.execute('ALTER TABLE DataDict ADD COLUMN Scale "DOUBLE(53)"')
        self.edit('ALTER TABLE DataDict ADD COLUMN Scale')
        self.edit('UPDATE DataDict SET Scale=1')
        result = self.run_repair()
        self.assertEqual(result['unresolved_issues'], 0)
        with readonly(self.corrected()) as c:
            self.assertIs(type(records(c,'DataDict')[0]['Scale']), float)

    def test_table_repairs_are_atomic_and_other_tables_continue(self):
        self.edit('CREATE TABLE SeriesBad (Country TEXT,FIPS_CODE TEXT,"2020")')
        self.edit("INSERT INTO SeriesBad VALUES ('Example A','AAA','bad')")
        result = self.run_repair()
        self.assertEqual(result['repaired_tables'], 1)
        self.assertEqual(result['unresolved_tables'], 1)
        with readonly(self.corrected()) as c:
            self.assertEqual(records(c, 'SeriesBad')[0]['2020'], 'bad')

    def test_dyadic_not_padded_to_country_count(self):
        self.edit('DROP TABLE SeriesTest')
        self.edit('CREATE TABLE SeriesTest (Actor TEXT,Actor_FIPS TEXT,Partner TEXT,Partner_FIPS TEXT,"2020" DOUBLE)')
        self.edit("INSERT INTO SeriesTest VALUES ('Example A','AAA','Exampel B','BBB',8)")
        self.run_repair()
        with readonly(self.corrected()) as c:
            data = records(c, 'SeriesTest')
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['Partner'], 'Example B')
            self.assertEqual(data[0]['MostRecent'], 8)

    def test_custom_constraints_not_removed(self):
        self.edit('CREATE INDEX important ON SeriesTest(Country)')
        self.assertEqual(self.run_repair()['unresolved_tables'], 1)

    def test_repair_is_idempotent(self):
        self.run_repair()
        again = repair_imports(self.root, self.base, [self.corrected()], self.root / 'again')
        self.assertEqual(again['corrected_imports'], 0)
        self.assertEqual(again['unresolved_tables'], 0)

    def test_tampering_original_corrected_or_audit_is_detected(self):
        self.run_repair()
        for path in [self.corrected(), self.output / 'originals' / self.source.name, self.output / 'evidence/IFsDataImport_A.jsonl']:
            with self.subTest(path=path):
                before = path.read_bytes()
                path.write_bytes(before + b'changed')
                with self.assertRaisesRegex(PipelineError, 'evidence changed'):
                    verify_preparation(self.output)
                path.write_bytes(before)

    def test_cli_directory_scan_does_not_read_working_files(self):
        nested = self.source.parent / 'Working Files'
        nested.mkdir()
        (nested / 'IFsDataImport_B.db').write_text('invalid')
        with redirect_stdout(io.StringIO()):
            status = main(['--root', str(self.root), 'repair-imports', '--base', str(self.base), '--input', str(self.source.parent), '--output', str(self.output)])
        self.assertEqual(status, 0)
        self.assertEqual(len(json.loads((self.output / 'manifest.json').read_text())['imports']), 1)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaisesRegex(PipelineError, 'new output folder'):
            self.run_repair()

    def test_named_extra_entity_with_no_values_is_not_padding(self):
        self.edit("INSERT INTO SeriesTest VALUES ('World',NULL,NULL,NULL)")
        self.assertEqual(self.run_repair()['unresolved_tables'], 1)

    def test_complete_roster_allows_audited_populated_and_empty_extras(self):
        self.edit("INSERT INTO SeriesTest VALUES ('Example B','BBB',4,3)")
        self.edit('INSERT INTO SeriesTest VALUES (?,?,?,?)', (None,None,9,0))
        self.edit("INSERT INTO SeriesTest VALUES ('Province','ZZZ',8,7)")
        self.edit("INSERT INTO SeriesTest VALUES ('Empty province','ZZ2',NULL,NULL)")
        result = self.run_repair()
        self.assertEqual(result['unresolved_tables'], 0)
        self.assertEqual(result['excluded_rows'], 3)
        self.assertEqual(result['excluded_year_cells'], 4)
        with readonly(self.corrected()) as c:
            rows = records(c,'SeriesTest')
            self.assertEqual({r['FIPS_CODE'] for r in rows}, {'AAA','BBB'})
            self.assertEqual({r['FIPS_CODE']: r['2020'] for r in rows}, {'AAA':2.5,'BBB':3.0})
        audit = [json.loads(line) for line in (self.output/'evidence/IFsDataImport_A.jsonl').read_text().splitlines()]
        excluded = [e for e in audit if e.get('rule') == 'exclude_extra_after_complete_roster']
        self.assertEqual(len(excluded),3)
        self.assertEqual(excluded[0]['before']['2020']['raw_repr'], '0.0')
        self.assertEqual(self.manifest['exclusions'][0]['populated_rows'], 2)
        self.assertIn('Excluded surplus rows', (self.output/'report.md').read_text())
        self.assertTrue(verify_preparation(self.output)['verified'])
        again = repair_imports(self.root,self.base,[self.corrected()],self.root/'again')
        self.assertEqual(again['corrected_imports'],0)
        self.assertEqual(again['excluded_rows'],0)

    def test_full_roster_does_not_authorize_duplicate_selection(self):
        self.edit("INSERT INTO SeriesTest VALUES ('Example B','BBB',4,3)")
        self.edit("INSERT INTO SeriesTest VALUES ('Example A','AAA',99,98)")
        self.edit('INSERT INTO SeriesTest VALUES (NULL,NULL,9,8)')
        self.assertEqual(self.run_repair()['unresolved_tables'],1)
        self.assertEqual(self.manifest['exclusions'],[])

    def test_full_roster_does_not_hide_recognizable_conflicting_identity(self):
        self.edit("INSERT INTO SeriesTest VALUES ('Example B','BBB',4,3)")
        self.edit("INSERT INTO SeriesTest VALUES ('Example A','ZZZ',99,98)")
        self.assertEqual(self.run_repair()['unresolved_tables'],1)

    def test_excluded_unsupported_values_are_preserved_as_raw_evidence(self):
        self.edit("INSERT INTO SeriesTest VALUES ('Example B','BBB',4,3)")
        self.edit('INSERT INTO SeriesTest VALUES (?,?,?,?)', (None,None,float('inf'),b'raw'))
        result = self.run_repair()
        self.assertEqual(result['excluded_rows'],1)
        evidence = (self.output/'evidence/IFsDataImport_A.jsonl').read_text()
        self.assertIn('bytes',evidence)
        self.assertIn('inf',evidence)

    def test_monadic_exclusion_rule_never_applies_to_dyadic_rows(self):
        self.edit('DROP TABLE SeriesTest')
        self.edit('CREATE TABLE SeriesTest (Actor TEXT,Actor_FIPS TEXT,Partner TEXT,Partner_FIPS TEXT,"2020" DOUBLE)')
        self.edit("INSERT INTO SeriesTest VALUES ('Example A','AAA','Example B','BBB',8)")
        self.edit('INSERT INTO SeriesTest VALUES (NULL,NULL,NULL,NULL,9)')
        self.assertEqual(self.run_repair()['unresolved_tables'],1)

    def test_normalized_roster_collisions_are_rejected(self):
        with database(self.base / 'IFsHistSeries.db') as c:
            c.execute("UPDATE SeriesPopulation SET FIPS_CODE='aaa' WHERE FIPS_CODE='BBB'")
        with self.assertRaisesRegex(PipelineError, 'normalized'):
            self.run_repair(use_datagator=False)

    def test_mapping_change_during_freezing_is_rejected(self):
        self.edit("UPDATE SeriesTest SET Country='Alias B',FIPS_CODE=NULL")
        mapping = self.root / 'mapping.csv'
        mapping.write_text('original_name,matched_name\nAlias B,Example B\n')
        original = repair.reviewed_mappings
        def changed(*args):
            result = original(*args)
            mapping.write_text('original_name,matched_name\nAlias B,Example A\n')
            return result
        with patch.object(repair, 'reviewed_mappings', side_effect=changed):
            with self.assertRaisesRegex(PipelineError, 'changed while preparing'):
                self.run_repair(mapping=mapping, reviewed=True)
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_failed_write_verification_rolls_back_the_whole_table(self):
        with patch.object(repair, 'verify_table', side_effect=PipelineError('verification failed')):
            result = self.run_repair()
        self.assertEqual(result['corrected_imports'], 0)
        self.assertEqual(result['unresolved_tables'], 1)

    def test_corrected_import_merges_without_losing_base_only_observations(self):
        for path in (self.base / 'DataDict.db', self.source):
            with database(path) as c:
                c.execute('DROP TABLE DataDict')
                c.execute('CREATE TABLE DataDict (Variable TEXT,"Table" TEXT,Definition TEXT,Units TEXT,Source TEXT)')
                c.execute("INSERT INTO DataDict VALUES ('Test','SeriesTest','Test definition','persons','Example')")
        with database(self.base / 'IFsHistSeries.db') as c:
            c.execute('CREATE TABLE SeriesTest (Country TEXT,FIPS_CODE TEXT,"2020" DOUBLE)')
            c.execute("INSERT INTO SeriesTest VALUES ('Example B','BBB',19)")
        self.run_repair()
        delivery = self.root / 'delivery'
        prepare_delivery(self.base, delivery)
        shutil.copy2(self.corrected(), delivery / 'IFsDataImport' / self.corrected().name)
        result = consolidate_delivery(delivery)
        self.assertEqual(result['status'], 'completed')
        with readonly(delivery / 'IFsHistSeries.db') as c:
            data = {r['FIPS_CODE']: r for r in records(c, 'SeriesTest')}
            self.assertEqual(data['AAA']['2022'], 0)
            self.assertEqual(data['BBB']['2020'], 19)
        self.assertTrue(compare_delivery(delivery)['verified'])


if __name__ == '__main__':
    unittest.main()
