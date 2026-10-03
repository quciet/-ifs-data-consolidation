"""Installed wheel works without repository resources or a managed workspace."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from ifs_pipeline.paths import path_settings
from ifs_pipeline.delivery_core import resolve_folder


class PathTests(unittest.TestCase):
    def test_config_relative_paths_environment_and_explicit_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            config = folder / 'paths.json'
            config.write_text(json.dumps({'delivery_root': 'releases', 'source_library': 'guides'}))
            with patch.dict(os.environ, {}, clear=True):
                settings = path_settings(config)
                self.assertEqual(settings['delivery_root'], str(folder / 'releases'))
                self.assertEqual(settings['source_library'], str(folder / 'guides'))
            with patch.dict(os.environ, {'IFS_DELIVERY_ROOT': str(folder / 'override')}):
                self.assertEqual(path_settings(config)['delivery_root'], str(folder / 'override'))
                self.assertEqual(resolve_folder('release', folder), folder / 'release')
                self.assertEqual(resolve_folder(folder / 'absolute'), folder / 'absolute')


class InstalledPackageTests(unittest.TestCase):
    def test_wheel_outside_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            def command(args, cwd=folder):
                done = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                return done.stdout
            command(['setup.py', 'build', '--build-base', str(folder / 'build'),
                     'bdist_wheel', '--dist-dir', str(folder / 'dist'),
                     '--bdist-dir', str(folder / 'bdist')], REPO)
            wheel = next((folder / 'dist').glob('*.whl'))
            with zipfile.ZipFile(wheel) as archive:
                names = archive.namelist()
                self.assertTrue(any(n.endswith('workflows/catalog.json') for n in names))
                self.assertTrue(any(n.endswith('datagator/provenance.json') for n in names))
                self.assertFalse(any(n.endswith(('.db', 'paths.local.json', 'config.local.json')) for n in names))
            target = folder / 'installed'
            command(['-m', 'pip', 'install', '--no-index', '--no-deps', '--target', str(target), str(wheel)])
            def installed(code, *args):
                # -I excludes the checkout, PYTHONPATH and user site packages.
                return command(['-I', '-c', 'import sys; sys.path.insert(0, ' + repr(str(target)) + '); ' + code, *args])
            def cli(*args):
                return json.loads(installed('from ifs_pipeline.cli import main; raise SystemExit(main())', *map(str, args)))
            with patch.dict(os.environ, {'IFS_DELIVERY_ROOT': '', 'IFS_SOURCE_LIBRARY': ''}):
                paths = cli('paths')
                self.assertEqual(paths['delivery_root'], str(folder))
                self.assertIsNone(paths['source_library'])
                catalog = cli('workflows')
                self.assertEqual(len(catalog['stages']), 4)
                for stage in catalog['stages']:
                    self.assertTrue(Path(stage['runbook']).is_relative_to(target))
                cli('workflow-init', '--stage', 'validation', '--output', folder / 'stage-record')
                cli('demo', '--directory', folder / 'synthetic')
                base = folder / 'synthetic/baseline'
                delivery = folder / 'delivery'
                cli('delivery-prepare', '--base', base, '--delivery', delivery,
                    '--country-table', 'SeriesDemoPopulation')
                installed('''from pathlib import Path
import sqlite3, shutil
from ifs_pipeline.resources import datagator_lookup
from ifs_pipeline.storage import read_json, sha256
lookup = datagator_lookup(Path.cwd())
assert sha256(lookup) == read_json(lookup.with_name('provenance.json'))['local_sha256']
base, delivery = Path(sys.argv[1]), Path(sys.argv[2])
target = delivery / 'IFsDataImport/IFsDataImport_Demo.db'
shutil.copy2(base / 'IFsHistSeries.db', target)
with sqlite3.connect(target) as conn:
    conn.execute('DROP TABLE Unrelated')
    conn.execute('ATTACH DATABASE ? AS metadata', (str(base / 'DataDict.db'),))
    conn.execute('CREATE TABLE DataDict AS SELECT * FROM metadata.DataDict')
''', str(base), str(delivery))
                self.assertEqual(cli('delivery-merge', '--delivery', delivery)['status'], 'completed')
                self.assertTrue(cli('delivery-compare', '--delivery', delivery)['verified'])
                self.assertEqual(cli('delivery-clean', '--delivery', delivery, '--execute')['status'], 'cleaned')
                self.assertTrue(cli('delivery-compare', '--delivery', delivery)['verified'])

