"""Offline contract tests. Fixtures are opaque bytes, never decoded images."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'custom_nodes'))


class GraphClientTests(unittest.TestCase):
    def setUp(self):
        # A regression must never turn this approved mock suite into network work.
        self.network = patch('socket.socket', side_effect=AssertionError('Network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'input'
        self.input.mkdir()
        try:
            self.package = importlib.import_module('panorama_graph_client')
        except ModuleNotFoundError as exc:
            self.fail(f'Graph-client implementation is missing: {exc}')
        self.sources = importlib.import_module('panorama_graph_client.sources')
        self.adapter = importlib.import_module('panorama_graph_client.adapter')

    def photo(self, name='a.jpg', content=b'opaque original A'):
        (self.input / name).write_bytes(content)
        return self.sources.select_source(self.input, name)

    def setup_run(self):
        sources = [self.photo(), self.photo('b.png', b'opaque original B')]
        transport = FakeTransport(sources)
        runner = self.adapter.CloudRunner(self.root / 'runs', 'https://demo.run.comfy.app',
                                          lambda: transport)
        return sources, transport, runner

    def test_package_registers_nodes_without_importing_processing_dependencies(self):
        self.assertIn('PanoramaOriginalPhoto', self.package.NODE_CLASS_MAPPINGS)
        self.assertIn('PanoramaCloudRun', self.package.NODE_CLASS_MAPPINGS)
        self.assertNotIn('cv2', sys.modules)
        self.assertNotIn('PIL.Image', sys.modules)
        self.assertNotIn('torch', sys.modules)
        self.assertNotIn('comfy_sdk', sys.modules)

    def test_selection_preserves_original_bytes_and_safe_relative_identity(self):
        source = self.photo()
        self.assertEqual(source.filename, 'a.jpg')
        self.assertEqual(source.sha256, hashlib.sha256(b'opaque original A').hexdigest())
        self.assertEqual((self.input / 'a.jpg').read_bytes(), b'opaque original A')

    def test_loader_rejects_escape_output_annotations_empty_and_oversize(self):
        self.photo()
        for name in ('../outside.jpg', 'a.jpg [output]', '', 'a.jpg [temp]'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.sources.select_source(self.input, name)
        with patch.object(self.sources, 'MAX_FILE_BYTES', 3), self.assertRaises(ValueError):
            self.sources.select_source(self.input, 'a.jpg')

    def test_no_submission_without_explicit_run_permission(self):
        sources, transport, runner = self.setup_run()
        with self.assertRaisesRegex(ValueError, 'new run'):
            runner.run('run1', sources)
        self.assertEqual(transport.submissions, [])
        self.assertFalse((self.root / 'runs' / 'run1').exists())

    def test_changed_or_duplicate_sources_fail_before_any_remote_call(self):
        sources, transport, runner = self.setup_run()
        (self.input / 'a.jpg').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            runner.run('changed', sources, allow_new_run=True)
        duplicate = self.photo('copy.png')
        with self.assertRaises(ValueError):
            runner.run('duplicates', [self.photo(), duplicate], allow_new_run=True)
        self.assertEqual(transport.submissions, [])

    def test_two_to_six_count_enforced_before_submission(self):
        sources, transport, runner = self.setup_run()
        for count in (0, 1, 7):
            with self.subTest(count=count), self.assertRaises(ValueError):
                runner.run('count' + str(count), (sources * 4)[:count], allow_new_run=True)
        self.assertEqual(transport.submissions, [])

    def test_success_binds_original_snapshots_and_reuses_receipt_after_restart(self):
        sources, transport, runner = self.setup_run()
        result = runner.run('run1', sources, allow_new_run=True)
        self.assertEqual(result.panorama.read_bytes(), b'opaque panorama')
        self.assertEqual(result.coverage.read_bytes(), b'opaque coverage')
        self.assertEqual(json.loads(result.report_text)['status'], 'completed')
        submitted = transport.submissions[0]
        self.assertEqual(submitted['key'], 'run1')
        self.assertFalse(submitted['graph']['1']['inputs']['crop'])
        self.assertEqual(submitted['files'][0].read_bytes(), b'opaque original A')
        self.assertEqual(submitted['graph']['1']['inputs']['photo_3'], '')
        receipt = json.loads((self.root / 'runs' / 'run1' / 'receipt.json').read_text())
        self.assertEqual(receipt['remote_job_id'], 'remote-job-1')
        self.assertEqual(receipt['expected_release_id'], self.adapter.RELEASE_ID)
        restarted = self.adapter.CloudRunner(self.root / 'runs', 'https://demo.run.comfy.app',
                                            lambda: self.fail('Completed run must stay offline'))
        again = restarted.run('run1', sources)
        self.assertEqual(again.report_text, result.report_text)
        self.assertEqual(len(transport.submissions), 1)

    def test_receipt_written_before_submit_and_uncertainty_never_resubmits(self):
        sources, transport, runner = self.setup_run()
        transport.error = TimeoutError('secret-provider-message')
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            runner.run('ambiguous', sources, allow_new_run=True)
        receipt_path = self.root / 'runs' / 'ambiguous' / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt['state'], 'uncertain')
        self.assertNotIn('secret-provider-message', receipt_path.read_text())
        transport.error = None
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            runner.run('ambiguous', sources, allow_new_run=True)
        self.assertEqual(len(transport.submissions), 1)

    def test_reused_run_id_cannot_silently_change_inputs_or_endpoint(self):
        sources, transport, runner = self.setup_run()
        runner.run('run1', sources, allow_new_run=True)
        different = [sources[0], self.photo('c.jpg', b'third source')]
        with self.assertRaises(ValueError):
            runner.run('run1', different, allow_new_run=True)
        other = self.adapter.CloudRunner(self.root / 'runs', 'https://other.run.comfy.app',
                                        lambda: transport)
        with self.assertRaises(ValueError):
            other.run('run1', sources)
        self.assertEqual(len(transport.submissions), 1)

    def test_missing_report_or_wrong_source_provenance_never_completes(self):
        for failure in ('missing', 'wrong_hash'):
            with self.subTest(failure=failure):
                sources, transport, runner = self.setup_run()
                transport.failure = failure
                with self.assertRaises(ValueError):
                    runner.run(failure, sources, allow_new_run=True)
                receipt = json.loads((self.root / 'runs' / failure / 'receipt.json').read_text())
                self.assertEqual(receipt['state'], 'submitted')

    def test_submitted_job_recovers_by_id_without_second_submission(self):
        sources, transport, runner = self.setup_run()
        transport.failure = 'missing'
        with self.assertRaises(ValueError):
            runner.run('recover', sources, allow_new_run=True)
        transport.failure = None
        recovered = runner.run('recover', sources)
        self.assertTrue(recovered.panorama.is_file())
        self.assertEqual(len(transport.submissions), 1)
        self.assertEqual(transport.recovered_ids, ['remote-job-1'])

    def test_lookup_failure_is_sanitized_and_retains_submitted_receipt(self):
        sources, transport, runner = self.setup_run()
        transport.failure = 'missing'
        with self.assertRaises(ValueError):
            runner.run('lookup', sources, allow_new_run=True)
        transport.recovery_error = TimeoutError('private-provider-url')
        with self.assertRaisesRegex(ValueError, 'lookup') as failure:
            runner.run('lookup', sources)
        self.assertNotIn('private-provider-url', str(failure.exception))
        self.assertEqual(len(transport.submissions), 1)
        receipt = json.loads((self.root / 'runs' / 'lookup' / 'receipt.json').read_text())
        self.assertEqual(receipt['state'], 'submitted')

    def test_close_failure_does_not_leak_or_override_completed_receipt(self):
        sources, transport, runner = self.setup_run()
        transport.close_error = RuntimeError('private-transport-detail')
        result = runner.run('close', sources, allow_new_run=True)
        self.assertTrue(result.report.is_file())
        again = runner.run('close', sources)
        self.assertEqual(result.report_text, again.report_text)
        self.assertEqual(len(transport.submissions), 1)

    def test_corrupt_cached_output_and_concurrent_run_fail_closed(self):
        sources, transport, runner = self.setup_run()
        result = runner.run('cache', sources, allow_new_run=True)
        result.coverage.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            runner.run('cache', sources)
        lock = self.root / 'runs' / 'locked'
        lock.mkdir()
        (lock / 'active.lock').write_text('busy')
        with self.assertRaisesRegex(ValueError, 'active|lock'):
            runner.run('locked', sources, allow_new_run=True)
        self.assertEqual(len(transport.submissions), 1)

    def test_unsafe_endpoint_and_run_id_rejected(self):
        for endpoint in ('https://cloud.comfy.org', 'https://key@demo.run.comfy.app',
                         'http://demo.run.comfy.app', 'https://demo.run.comfy.app/path'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.adapter.CloudRunner(self.root / 'runs', endpoint, lambda: None)
        sources, transport, runner = self.setup_run()
        with self.assertRaises(ValueError):
            runner.run('../escape', sources, allow_new_run=True)

    def test_node_optional_slots_and_typed_outputs_use_injected_materializer(self):
        nodes = importlib.import_module('panorama_graph_client.nodes')
        schema = nodes.PanoramaCloudRun.INPUT_TYPES()
        self.assertIn('photo_2', schema['required'])
        self.assertIn('photo_6', schema['optional'])
        sources, transport, runner = self.setup_run()
        runner.run('typed', sources, allow_new_run=True)
        node = nodes.PanoramaCloudRun(runner=runner,
                                     materialize=lambda result: ('RGB sentinel', 'MASK sentinel'))
        result = node.execute('typed', False, sources[0], sources[1])
        self.assertEqual(result['result'][:2], ('RGB sentinel', 'MASK sentinel'))
        self.assertEqual(len(transport.submissions), 1)  # preview evaluation must reuse this run

    def test_six_sources_bind_in_order_and_selected_invalid_optional_fails(self):
        sources = [self.photo(f'{i}.jpg', f'original-{i}'.encode()) for i in range(6)]
        transport = FakeTransport(sources)
        runner = self.adapter.CloudRunner(self.root / 'runs', 'https://demo.run.comfy.app', lambda: transport)
        nodes = importlib.import_module('panorama_graph_client.nodes')
        node = nodes.PanoramaCloudRun(runner=runner, materialize=lambda _: ('RGB', 'MASK'))
        result = node.execute('six', True, sources[0], sources[1],
                              **{f'photo_{i+1}': sources[i] for i in range(2, 6)})
        self.assertEqual(len(json.loads(result['result'][2])['selected']), 6)
        self.assertEqual([p.read_bytes() for p in transport.submissions[0]['files']],
                         [f'original-{i}'.encode() for i in range(6)])
        with self.assertRaises(ValueError):
            node.execute('invalid', True, sources[0], sources[1], photo_3=None)
        self.assertEqual(len(transport.submissions), 1)

    def test_loader_widgets_have_upload_and_source_hash_invalidation(self):
        nodes = importlib.import_module('panorama_graph_client.nodes')
        self.photo()
        folder_paths = types.SimpleNamespace(get_input_directory=lambda: str(self.input))
        with patch.dict(sys.modules, {'folder_paths': folder_paths}):
            schema = nodes.PanoramaOriginalPhoto.INPUT_TYPES()
            self.assertIn('a.jpg', schema['required']['image'][0])
            self.assertTrue(schema['required']['image'][1]['image_upload'])
            original = nodes.PanoramaOriginalPhoto.IS_CHANGED('a.jpg')
            record, = nodes.PanoramaOriginalPhoto().load('a.jpg')
            self.assertEqual(record.sha256, original)
            (self.input / 'a.jpg').write_bytes(b'new opaque original')
            self.assertNotEqual(nodes.PanoramaOriginalPhoto.IS_CHANGED('a.jpg'), original)

    def test_live_transport_disabled_before_any_sdk_construction(self):
        sdk = types.SimpleNamespace(Comfy=lambda **_: self.fail('Must not construct SDK'))
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {'comfy_sdk': sdk}):
            with self.assertRaisesRegex(ValueError, 'disabled'):
                self.adapter.SDKTransport('https://demo.run.comfy.app')

    def test_sdk_boundary_binds_assets_and_uses_the_receipted_key(self):
        calls = []

        class Workflow:
            def set_input(self, node, field, value):
                calls.append((node, field, value))

        class Client:
            def __init__(self, **kwargs):
                self.workflows = types.SimpleNamespace(from_json=lambda _: Workflow())
                self.assets = types.SimpleNamespace(from_file=lambda path: ('original-file', path))
                self.jobs = types.SimpleNamespace(get=lambda job_id: ('existing', job_id))

            def submit(self, workflow, **kwargs):
                calls.append(kwargs)
                return 'submitted'

        environment = {'PANORAMA_GRAPH_ALLOW_REMOTE': 'approved',
                       'COMFY_BASE_URL': 'https://demo.run.comfy.app', 'COMFY_API_KEY': 'mock-only'}
        with patch.dict(os.environ, environment, clear=True), patch.dict(sys.modules, {'comfy_sdk': types.SimpleNamespace(Comfy=Client)}):
            transport = self.adapter.SDKTransport('https://demo.run.comfy.app')
            self.assertEqual(transport.submit({}, [Path('snapshot.jpg')], 'receipt-key'), 'submitted')
            self.assertEqual(calls, [('1', 'photo_1', ('original-file', Path('snapshot.jpg'))),
                                     {'idempotency_key': 'receipt-key'}])
            self.assertEqual(transport.get_job('job-1'), ('existing', 'job-1'))

    def test_download_size_lie_is_bounded_while_streaming(self):
        sources, transport, runner = self.setup_run()
        transport.failure = 'oversize'
        with patch.dict(self.adapter.ARTIFACT_LIMITS, {'panorama.png': 4}):
            with self.assertRaises(ValueError):
                runner.run('oversize', sources, allow_new_run=True)
        partial = self.root / 'runs' / 'oversize' / 'panorama.part'
        self.assertLessEqual(partial.stat().st_size, 4)

    def test_run_directory_symlink_escape_rejected(self):
        sources, transport, runner = self.setup_run()
        (self.root / 'runs').mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'runs' / 'escape'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks require Windows privileges')
        with self.assertRaises(ValueError):
            runner.run('escape', sources, allow_new_run=True)
        self.assertEqual(list(outside.iterdir()), [])


class FakeOutput:
    def __init__(self, name, content):
        self.name, self.content = name, content
        self.size_bytes = len(content)

    def to_file(self, path):
        Path(path).write_bytes(self.content)

    def to_stream(self, stream):
        return stream.write(self.content)


class FakeJob:
    id = 'remote-job-1'

    def __init__(self, transport):
        self.transport = transport

    def result(self):
        return self

    def get_outputs(self, node_id):
        if node_id != '1':
            raise AssertionError('Wrong worker output node')
        records = [{'sha256': s.sha256, 'status': 'used'} for s in self.transport.sources]
        if self.transport.failure == 'wrong_hash':
            records[0]['sha256'] = 'wrong'
        report = {'status': 'completed', 'selected': records, 'job_id': 'worker-job-1',
                  'artifacts': ['panorama.png', 'coverage.png', 'report.json']}
        outputs = [FakeOutput('panorama.png', b'opaque panorama'),
                   FakeOutput('coverage.png', b'opaque coverage')]
        if self.transport.failure == 'oversize':
            outputs[0].size_bytes = 1  # malicious advertised size cannot bypass streaming bounds
        if self.transport.failure != 'missing':
            outputs.append(FakeOutput('report.json', json.dumps(report).encode()))
        return outputs


class FakeTransport:
    def __init__(self, sources):
        self.sources, self.submissions, self.recovered_ids = sources, [], []
        self.error = self.failure = None
        self.recovery_error = self.close_error = None

    def submit(self, graph, files, key):
        receipt = json.loads((files[0].parent / 'receipt.json').read_text())
        if receipt['state'] != 'submitting':
            raise AssertionError('Submission intent must be durable first')
        self.submissions.append({'graph': graph, 'files': files, 'key': key})
        if self.error:
            raise self.error
        return FakeJob(self)

    def get_job(self, job_id):
        self.recovered_ids.append(job_id)
        if self.recovery_error:
            raise self.recovery_error
        return FakeJob(self)

    def close(self):
        if self.close_error:
            raise self.close_error


if __name__ == '__main__':
    unittest.main()
