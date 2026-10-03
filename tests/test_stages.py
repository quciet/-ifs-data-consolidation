"""Workflow scaffolding must stay separate from processing actual data."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from ifs_pipeline.cli import main
from ifs_pipeline.stages import STAGES, init_workflow, workflow_catalog
from ifs_pipeline.storage import PipelineError, read_json, sha256, write_json


class WorkflowStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(REPO / 'workflows', self.root / 'workflows')

    def test_catalog_preserves_order_and_distinguishes_extensions(self):
        catalog = workflow_catalog(self.root)
        self.assertEqual([s['id'] for s in catalog['stages']], list(STAGES))
        for stage in STAGES:
            entry = workflow_catalog(self.root, stage)
            self.assertTrue(Path(entry['runbook']).is_file())
            self.assertTrue(entry['current_commands'])
        validation = workflow_catalog(self.root, 'validation')
        self.assertIn('src/ifs_pipeline/normalization.py', validation['implementation'])
        self.assertIn('Source-supported unit synonyms beyond case/whitespace', validation['extension_points'])

    def test_workspace_contains_no_data_or_claim_of_completed_checks(self):
        for stage in STAGES:
            output = self.root / 'reviews' / stage
            result = init_workflow(self.root, stage, output)
            handoff = read_json(output / 'handoff.json')
            self.assertEqual(result['status'], 'not_started')
            self.assertFalse(result['data_processed'])
            self.assertEqual(handoff['status'], 'not_started')
            self.assertEqual(handoff['input_artifacts'], [])
            self.assertEqual(handoff['output_artifacts'], [])
            self.assertEqual(handoff['table_outcomes'], [])
            self.assertEqual(handoff['native_results'], [])
            self.assertFalse(list(output.rglob('*.db')))
            self.assertEqual(len((output / 'issues.csv').read_text().splitlines()), 1)
            definition = read_json(output / 'workflow-definition.json')
            for name, digest in definition['snapshots'].items():
                self.assertEqual(sha256(output / name), digest)

    def test_existing_workspace_is_not_overwritten(self):
        output = self.root / 'existing'
        output.mkdir()
        marker = output / 'handoff.json'
        marker.write_text('user content')
        with self.assertRaisesRegex(PipelineError, 'new workspace'):
            init_workflow(self.root, 'validation', output)
        self.assertEqual(marker.read_text(), 'user content')

    def test_rejects_unknown_stage_and_definition_directory(self):
        with self.assertRaisesRegex(PipelineError, 'Unknown workflow'):
            init_workflow(self.root, 'unknown', self.root / 'unused')
        self.assertFalse((self.root / 'unused').exists())
        with self.assertRaisesRegex(PipelineError, 'outside workflow definitions'):
            init_workflow(self.root, 'validation', self.root / 'workflows' / 'new')

    def test_catalog_cannot_reference_paths_outside_workflows(self):
        path = self.root / 'workflows/catalog.json'
        catalog = read_json(path)
        catalog['stages'][0]['runbook'] = '../outside.md'
        write_json(path, catalog)
        with self.assertRaises(PipelineError):
            workflow_catalog(self.root)

    def test_cli_scaffolding_does_not_call_repair_or_merge(self):
        output = self.root / 'stage'
        with patch('ifs_pipeline.cli.repair_imports') as repair, patch('ifs_pipeline.cli.consolidate_delivery') as merge:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(['--root', str(self.root), 'workflow-init', '--stage', 'validation', '--output', str(output)])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(buffer.getvalue())['data_processed'])
            repair.assert_not_called()
            merge.assert_not_called()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(['--root', str(self.root), 'workflows', 'comparison'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())['id'], 'comparison')


if __name__ == '__main__':
    unittest.main()
