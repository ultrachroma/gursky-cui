"""Offline endpoint routing regressions. No live client, credentials or images."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'custom_nodes'))
TARGET = 'https://example-deployment.run.comfy.app'


class DeploymentConnectionTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket', side_effect=AssertionError('Live requests forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def connection(self):
        try:
            from panorama_graph_client import connection
        except ImportError:
            self.fail('Deployment connection defaults have not been implemented.')
        return connection

    def test_target_record_is_blank_without_secrets(self):
        path = ROOT / 'custom_nodes/panorama_graph_client/deployment_target.json'
        self.assertTrue(path.is_file(), 'Non-secret deployment target record missing')
        target = json.loads(path.read_text())
        self.assertEqual(target['deployment_id'], '')
        self.assertEqual(target['base_url'], '')
        self.assertEqual(set(target), {'deployment_id', 'base_url', 'workspace_id', 'build_id', 'release_id'})

    def test_default_endpoint_and_explicit_override_are_resolved_without_credentials(self):
        connection = self.connection()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Set COMFY_BASE_URL'):
                connection.configured_endpoint()
            self.assertNotIn('COMFY_API_KEY', os.environ)
        with patch.dict(os.environ, {'COMFY_BASE_URL': 'https://override.run.comfy.app'}, clear=True):
            self.assertEqual(connection.configured_endpoint(), 'https://override.run.comfy.app')

    def test_sdk_construction_uses_explicit_example_target(self):
        from panorama_graph_client.adapter import SDKTransport
        observed = []

        def client(**kwargs):
            observed.append(os.environ.get('COMFY_BASE_URL'))
            return object()

        environment = {'PANORAMA_GRAPH_ALLOW_REMOTE': 'approved', 'COMFY_API_KEY': 'mock-only', 'COMFY_BASE_URL': TARGET}
        with patch.dict(os.environ, environment, clear=True), patch.dict(sys.modules, {'comfy_sdk': types.SimpleNamespace(Comfy=client)}):
            SDKTransport(TARGET)
            self.assertEqual(observed, [TARGET])

    def test_graph_default_runner_targets_deployment_with_submissions_still_disabled(self):
        from panorama_graph_client import nodes
        observed = []
        artifacts = types.SimpleNamespace(report_text='{}')

        class Runner:
            def __init__(self, root, endpoint, factory):
                observed.append(endpoint)

            def run(self, run_id, sources, allow_new_run):
                observed.append(allow_new_run)
                return artifacts

        with patch.dict(os.environ, {'COMFY_BASE_URL': TARGET}, clear=True), patch.object(nodes, 'CloudRunner', Runner):
            node = nodes.PanoramaCloudRun(materialize=lambda _: ('RGB sentinel', 'MASK sentinel'))
            node.execute('cached-run', False, object(), object())
        self.assertEqual(observed, [TARGET, False])

    def test_app_and_graph_read_the_same_default_target(self):
        spec = importlib.util.spec_from_file_location('panorama_app_connection_test', ROOT / 'app/server.py')
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        with patch.dict(os.environ, {'COMFY_BASE_URL': TARGET}, clear=True):
            self.assertTrue(hasattr(server, 'deployment_endpoint'), 'App has no default endpoint resolver')
            self.assertEqual(server.deployment_endpoint(), TARGET)
            self.assertEqual(self.connection().configured_endpoint(), server.deployment_endpoint())
            self.assertNotIn('COMFY_API_KEY', os.environ)


if __name__ == '__main__':
    unittest.main()
