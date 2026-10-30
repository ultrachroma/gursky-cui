"""Offline metadata-only tests: synthetic chunk bytes, no image decode or SDK."""
import hashlib
import json
import struct
import unittest
from unittest.mock import patch
import zlib

import test_graph_client as harness


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def container(text=None, *, kind=b'tEXt', duplicate=False, width=2, height=1):
    # The IDAT payload deliberately is NOT a valid compressed image. This suite
    # tests the metadata container only and must never invoke pixel decoding.
    result = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    if text is not None:
        record = chunk(kind, b'panorama_report\0' + text)
        result += record * (2 if duplicate else 1)
    return result + chunk(b'IDAT', b'opaque non-image bytes') + chunk(b'IEND', b'')


class GraphReportFallbackTests(unittest.TestCase):
    setUp = harness.GraphClientTests.setUp
    photo = harness.GraphClientTests.photo
    setup_run = harness.GraphClientTests.setup_run

    def report(self, sources):
        return json.dumps({'status': 'completed', 'selected': [
            {'sha256': source.sha256, 'status': 'used'} for source in sources]}).encode()

    def outputs(self, png, direct=None):
        result = [harness.FakeOutput('panorama.png', png),
                  harness.FakeOutput('coverage.png', b'opaque coverage')]
        if direct is not None:
            result.append(harness.FakeOutput('report.json', direct))
        return result

    def test_fallback_preserves_downloaded_png_and_caches_report_offline(self):
        sources, transport, runner = self.setup_run()
        text = self.report(sources)
        png = container(text)
        with patch.object(harness.FakeJob, 'get_outputs', return_value=self.outputs(png)):
            result = runner.run('fallback', sources, allow_new_run=True)
        self.assertEqual(result.panorama.read_bytes(), png)
        self.assertEqual(result.report.read_bytes(), text)
        receipt = json.loads(result.receipt.read_text())
        self.assertEqual(receipt['report_origin'], 'panorama_png_text')
        self.assertEqual(receipt['artifacts']['report.json'], {
            'sha256': hashlib.sha256(text).hexdigest(), 'size_bytes': len(text)})
        runner.transport_factory = lambda: self.fail('Cache must remain offline')
        self.assertEqual(runner.run('fallback', sources).report_text, text.decode())
        result.report.write_text('{}')
        with self.assertRaises(ValueError):
            runner.run('fallback', sources)
        self.assertEqual(len(transport.submissions), 1)
        for module in ('PIL.Image', 'numpy', 'torch', 'cv2', 'comfy_sdk'):
            self.assertNotIn(module, harness.sys.modules)

    def test_direct_report_priority_does_not_read_png_metadata(self):
        sources, _, runner = self.setup_run()
        # Opaque, malformed panorama metadata cannot interfere with direct JSON.
        with patch.object(harness.FakeJob, 'get_outputs', return_value=self.outputs(b'opaque', self.report(sources))), \
                patch.object(self.adapter, 'embedded_report', side_effect=AssertionError('Fallback forbidden')):
            result = runner.run('direct', sources, allow_new_run=True)
        self.assertEqual(json.loads(result.receipt.read_text())['report_origin'], 'direct_report_json')

    def test_invalid_direct_report_cannot_be_masked_by_valid_embedded_report(self):
        sources, transport, runner = self.setup_run()
        with patch.object(harness.FakeJob, 'get_outputs', return_value=self.outputs(container(self.report(sources)), b'{}')), \
                patch.object(self.adapter, 'embedded_report', side_effect=AssertionError('Fallback forbidden')) as fallback:
            with self.assertRaises(ValueError):
                runner.run('invalid-direct', sources, allow_new_run=True)
            fallback.assert_not_called()
        self.assertEqual(len(transport.submissions), 1)
        self.assertEqual(json.loads((runner.root / 'invalid-direct' / 'receipt.json').read_text())['state'], 'submitted')

    def test_malformed_metadata_remains_submitted_then_resumes_by_id(self):
        sources, transport, runner = self.setup_run()
        text = self.report(sources)
        bad_crc = bytearray(container(text))
        bad_crc[-1] ^= 1
        cases = [b'not png', container(), container(b'{'), container(b'[]'),
                 container(text, duplicate=True), container(text, kind=b'zTXt'),
                 container(text, kind=b'iTXt'), container(text, width=16001),
                 container(text, width=6000, height=6000), container(text, width=0),
                 bytes(bad_crc), container(text)[:-1], container(text) + b'trailing',
                 container(text.replace(sources[0].sha256.encode(), b'wrong')),
                 container(text.replace(b'"completed"', b'"failed"')),
                 container(text.replace(b'"used"', b'"omitted"')),
                 container(text[:-1] + b',"status":"completed"}'),
                 container(text[:-1] + b',"value":NaN}'),
                 container(text[:-1] + b',"value":1e999}'),
                 container(text.replace(b'"selected": [', b'"selected": [null, ')),
                 container(b'{"status":"completed","selected":{}}')]
        for index, png in enumerate(cases):
            run_id = f'bad-{index}'
            with self.subTest(index=index), patch.object(harness.FakeJob, 'get_outputs', return_value=self.outputs(png)):
                with self.assertRaises(ValueError):
                    runner.run(run_id, sources, allow_new_run=True)
                self.assertEqual(json.loads((runner.root / run_id / 'receipt.json').read_text())['state'], 'submitted')
                self.assertFalse((runner.root / run_id / 'report.json').exists())
            with patch.object(harness.FakeJob, 'get_outputs', return_value=self.outputs(container(text))):
                runner.run(run_id, sources)
        self.assertEqual(len(transport.submissions), len(cases))
        self.assertEqual(transport.recovered_ids, ['remote-job-1'] * len(cases))

    def test_missing_coverage_does_not_allow_report_recovery(self):
        sources, _, runner = self.setup_run()
        outputs = self.outputs(container(self.report(sources)))[:1]
        with patch.object(harness.FakeJob, 'get_outputs', return_value=outputs):
            with self.assertRaises(ValueError):
                runner.run('missing-coverage', sources, allow_new_run=True)

    def test_embedded_report_limit_checked_before_persisting(self):
        sources, _, runner = self.setup_run()
        png = container(self.report(sources))
        with patch.object(harness.FakeJob, 'get_outputs', return_value=self.outputs(png)), \
                patch.dict(self.adapter.ARTIFACT_LIMITS, {'report.json': 32}):
            with self.assertRaises(ValueError):
                runner.run('metadata-budget', sources, allow_new_run=True)
        self.assertFalse((runner.root / 'metadata-budget' / 'report.json').exists())

    def test_stale_report_from_prior_download_is_not_used_as_direct_report(self):
        sources, _, runner = self.setup_run()
        # A failed attempt may leave a direct report file but no complete inventory.
        outputs = self.outputs(container(), self.report(sources))
        outputs.pop(1)
        with patch.object(harness.FakeJob, 'get_outputs', return_value=outputs):
            with self.assertRaises(ValueError):
                runner.run('stale', sources, allow_new_run=True)
        with patch.object(harness.FakeJob, 'get_outputs', return_value=self.outputs(container())):
            with self.assertRaises(ValueError):
                runner.run('stale', sources)

    def test_duplicate_direct_report_is_rejected(self):
        sources, _, runner = self.setup_run()
        outputs = self.outputs(container(self.report(sources)), self.report(sources))
        outputs.append(outputs[-1])
        with patch.object(harness.FakeJob, 'get_outputs', return_value=outputs):
            with self.assertRaises(ValueError):
                runner.run('duplicate-direct', sources, allow_new_run=True)

    def test_png_container_budgets_and_structure_fail_closed(self):
        metadata = harness.importlib.import_module('panorama_graph_client.report_metadata')
        valid = container(b'{}')
        header = valid[:33]
        cases = [valid[:8] + struct.pack('>I4s', 0xffffffff, b'tEXt'),
                 valid[:8] + chunk(b'IDAT', b''),
                 header + header[8:] + valid[33:],
                 header + chunk(b'tEXt', b'panorama_report') + valid[33:],
                 header + chunk(b'tEXt', b'panorama_report\0{}\0') + valid[33:],
                 header + chunk(b'tEXt', b'panorama_report\0{}') + chunk(b'IEND', b''),
                 header + chunk(b'abcd', b'') * 10000 + valid[33:]]
        path = self.root / 'metadata.png'
        for index, content in enumerate(cases):
            with self.subTest(index=index):
                path.write_bytes(content)
                with self.assertRaises(ValueError):
                    metadata.embedded_report(path, 1024, 1024 * 1024)
        path.write_bytes(valid)
        with self.assertRaises(ValueError):
            metadata.embedded_report(path, 1024, len(valid) - 1)

    def test_direct_json_uses_same_strict_json_validation(self):
        sources, _, runner = self.setup_run()
        text = self.report(sources)
        for index, direct in enumerate((text[:-1] + b',"status":"completed"}',
                                        text[:-1] + b',"value":Infinity}',
                                        text[:-1] + b',"value":1e999}')):
            with self.subTest(index=index), patch.object(harness.FakeJob, 'get_outputs',
                    return_value=self.outputs(container(text), direct)):
                with self.assertRaises(ValueError):
                    runner.run(f'strict-direct-{index}', sources, allow_new_run=True)


if __name__ == '__main__':
    unittest.main()
