import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from ifs_pipeline.preparation_paths import create_preparation_workspace, MARKER


class PreparationPathsTests(unittest.TestCase):
    def test_nested_output_copies_only_preexisting_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).resolve() / 'raw'; (source / 'nested').mkdir(parents=True)
            (source / 'nested' / 'original.csv').write_bytes(b'Country,Year,Value\nA,2020,0\n')
            output, inputs = create_preparation_workspace(source)
            self.assertEqual(output.parent, source.resolve())
            self.assertEqual(output.name, 'IFsDataImport')
            self.assertEqual(inputs, [source / 'nested' / 'original.csv'])
            for item in inputs:
                dest = output / 'Working Files' / item.relative_to(source)
                dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(item, dest)
            self.assertEqual(len(list(output.rglob('original.csv'))), 1)
            self.assertEqual(inputs[0].read_bytes(), dest.read_bytes())
            second, second_inputs = create_preparation_workspace(source)
            self.assertNotEqual(second, output)
            self.assertEqual(second.name, 'IFsDataImport_02')
            self.assertEqual(second_inputs, inputs)  # no prior outputs re-ingested

    def test_explicit_override_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'raw'; source.mkdir()
            external = Path(tmp) / 'chosen output'
            output, _ = create_preparation_workspace(source, external)
            self.assertEqual(output, external.resolve())
            before = (external / MARKER).read_bytes()
            with self.assertRaises(FileExistsError): create_preparation_workspace(source, external)
            self.assertEqual((external / MARKER).read_bytes(), before)

    def test_existing_unowned_default_folder_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).resolve()
            existing = source / 'IFsDataImport'; existing.mkdir()
            original = existing / 'user-file.txt'; original.write_text('keep')
            output, inputs = create_preparation_workspace(source)
            self.assertEqual(output.name, 'IFsDataImport_02')
            self.assertEqual(original.read_text(), 'keep')
            self.assertIn(original, inputs)  # a name alone is not generated-package evidence

    def test_source_and_ancestor_cannot_be_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'raw'; source.mkdir()
            for output in [source, Path(tmp)]:
                with self.assertRaises(ValueError): create_preparation_workspace(source, output)
            self.assertEqual(list(source.iterdir()), [])

    def test_unmarked_source_folders_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).resolve() / 'raw'; folder = source / 'IFs Preparation old'; folder.mkdir(parents=True)
            (folder / 'original.txt').write_text('original')
            (folder / MARKER).write_text('not valid JSON')
            _, inputs = create_preparation_workspace(source)
            self.assertIn(folder / 'original.txt', inputs)

    def test_missing_source_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'output'
            with self.assertRaises(ValueError): create_preparation_workspace(Path(tmp) / 'missing', output)
            self.assertFalse(output.exists())

    def test_earlier_native_package_is_not_reingested(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).resolve() / 'raw'
            evidence = source / 'previous output' / 'Working Files'; evidence.mkdir(parents=True)
            (evidence / 'preparation_manifest.json').write_text(json.dumps({'stage':'preprocessing','output':{'path':'old.db'}}))
            (source / 'original.csv').write_text('original')
            _, inputs = create_preparation_workspace(source)
            self.assertEqual(inputs, [source / 'original.csv'])
