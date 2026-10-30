"""Static editor/API wiring checks. Never execute image or cloud nodes."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'custom_nodes'))


class EditorWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'tools' / 'graph_workflows.py'
        if not path.is_file():
            raise AssertionError('Editor workflow tooling has not been implemented.')
        spec = importlib.util.spec_from_file_location('graph_workflows', path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def setUp(self):
        guard = patch('socket.socket', side_effect=AssertionError('No network in source tests'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_default_and_six_photo_artifacts_have_consistent_links_and_api_exports(self):
        for count, stem in ((2, 'panorama_editor'), (6, 'panorama_editor_six_photos')):
            with self.subTest(count=count):
                editor = json.loads((ROOT / 'workflows' / f'{stem}.json').read_text())
                api = json.loads((ROOT / 'workflows' / f'{stem}_api.json').read_text())
                self.tool.validate_editor(editor)
                self.assertEqual(self.tool.export_api(editor), api)
                self.assertEqual(len([n for n in editor['nodes'] if n['type'] == 'PanoramaOriginalPhoto']), count)
                self.assertEqual(api['10']['inputs']['photo_1'], ['1', 0])
                self.assertEqual(api['10']['inputs']['photo_2'], ['2', 0])
                self.assertEqual(api['20']['inputs'], {'images': ['10', 0]})
                self.assertEqual(api['21']['inputs'], {'mask': ['10', 1]})
                self.assertEqual(api['22']['inputs'], {'images': ['21', 0]})
                self.assertEqual(api['23']['inputs'], {'source': ['10', 2]})
                self.assertEqual(api['23']['class_type'], 'PreviewAny')
                self.assertFalse(api['10']['inputs']['allow_new_run'])
                self.assertEqual(api['10']['inputs']['run_id'], '')
                for index in range(1, count + 1):
                    self.assertEqual(api[str(index)]['inputs']['image'], '')
                if count == 2:
                    self.assertNotIn('photo_3', api['10']['inputs'])
                    run = next(n for n in editor['nodes'] if n['id'] == 10)
                    self.assertEqual([s['link'] for s in run['inputs'][2:]], [None] * 4)
                else:
                    self.assertEqual(api['10']['inputs']['photo_6'], ['6', 0])
                self.assertNotIn('99', api)  # frontend Note is never submitted

    def test_widget_order_and_socket_contract_match_installed_client_source(self):
        import panorama_graph_client
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {'folder_paths': types.SimpleNamespace(get_input_directory=lambda: directory)}):
                for name, node in panorama_graph_client.NODE_CLASS_MAPPINGS.items():
                    schema = node.INPUT_TYPES()
                    contract = self.tool.CONTRACTS[name]
                    widgets = [key for key, value in schema['required'].items()
                               if isinstance(value[0], list) or value[0] in ('STRING', 'BOOLEAN')]
                    self.assertEqual(widgets, contract['widgets'])
                    sockets = {key: value[0] for group in ('required', 'optional')
                               for key, value in schema.get(group, {}).items() if key not in widgets}
                    self.assertEqual(sockets, dict(contract['inputs']))
                    self.assertEqual(list(node.RETURN_TYPES), contract['outputs'])

    def test_editor_serialization_roundtrip_preserves_edited_widget_values(self):
        editor = self.tool.build_editor(2)
        for node in editor['nodes']:
            if node['id'] == 1:
                node['widgets_values'] = ['folder/first.png']
            if node['id'] == 10:
                node['widgets_values'] = ['existing-run-123', False]
        reopened = json.loads(json.dumps(editor))
        api = self.tool.export_api(reopened)
        self.assertEqual(api['1']['inputs']['image'], 'folder/first.png')
        self.assertEqual(api['10']['inputs']['run_id'], 'existing-run-123')
        self.assertFalse(api['10']['inputs']['allow_new_run'])

    def test_export_rejects_broken_typed_link_and_dangling_socket(self):
        editor = self.tool.build_editor(2)
        broken = copy.deepcopy(editor)
        link = next(link for link in broken['links'] if link['target_id'] == 21)
        link['origin_slot'] = 0  # RGB must never be silently treated as geometric coverage
        with self.assertRaises(ValueError):
            self.tool.export_api(broken)
        broken = copy.deepcopy(editor)
        node = next(n for n in broken['nodes'] if n['id'] == 20)
        node['inputs'][0]['link'] = None
        with self.assertRaises(ValueError):
            self.tool.export_api(broken)

    def test_optional_socket_cannot_reuse_a_link_owned_by_another_input(self):
        editor = self.tool.build_editor(2)
        run = next(n for n in editor['nodes'] if n['id'] == 10)
        run['inputs'][2]['link'] = run['inputs'][0]['link']
        with self.assertRaises(ValueError):
            self.tool.export_api(editor)

    def test_export_rejects_missing_source_unknown_node_and_duplicate_id(self):
        editor = self.tool.build_editor(2)
        for mutation in ('missing', 'unknown', 'duplicate'):
            broken = copy.deepcopy(editor)
            if mutation == 'missing':
                broken['nodes'] = [n for n in broken['nodes'] if n['id'] != 1]
            elif mutation == 'unknown':
                broken['nodes'][0]['type'] = 'InventedMultiImageUpload'
            else:
                broken['nodes'][0]['id'] = broken['nodes'][1]['id']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.tool.export_api(broken)

    def test_misordered_or_missing_widget_values_cannot_shift_run_permission(self):
        editor = self.tool.build_editor(2)
        run = next(n for n in editor['nodes'] if n['id'] == 10)
        for values in ([False, 'run'], ['run'], ['run', 'false']):
            run['widgets_values'] = values
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.tool.export_api(editor)

    def test_layout_has_separate_visible_review_panels_and_no_saved_results(self):
        editor = self.tool.build_editor(2)
        self.assertEqual(editor['version'], 1)
        panels = [n for n in editor['nodes'] if n['id'] in (20, 22, 23)]
        self.assertEqual(len(panels), 3)
        for panel in panels:
            self.assertEqual(panel['mode'], 0)
            self.assertFalse(panel['flags'].get('collapsed', False))
            self.assertGreaterEqual(panel['size'][0], 350)
            self.assertEqual(panel['widgets_values'], [])
        self.assertIn('white', next(n['title'] for n in panels if n['id'] == 22).lower())
        self.assertEqual(editor['state']['lastNodeId'], max(n['id'] for n in editor['nodes']))
        self.assertEqual(editor['state']['lastLinkId'], max(n['id'] for n in editor['links']))

    def test_no_execution_or_heavy_imports_during_workflow_checks(self):
        before = set(sys.modules)
        self.tool.export_api(self.tool.build_editor(6))
        self.assertFalse({'cv2', 'numpy', 'PIL.Image', 'torch', 'comfy_sdk'} & (set(sys.modules) - before))

    def test_export_rejects_cycles_in_preview_branches(self):
        editor = self.tool.build_editor(2)
        by_id = {n['id']: n for n in editor['nodes']}
        # Give the two image preview nodes each other's output. Types alone permit this.
        for link in editor['links']:
            if link['target_id'] in (20, 22):
                previous = by_id[link['origin_id']]['outputs'][link['origin_slot']]
                previous['links'].remove(link['id'])
                link['origin_id'] = 22 if link['target_id'] == 20 else 20
                link['origin_slot'] = 0
                by_id[link['origin_id']]['outputs'][0]['links'].append(link['id'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.tool.export_api(editor)

    def test_mock_graph_routes_original_sources_and_all_review_branches_once(self):
        from panorama_graph_client.nodes import PanoramaOriginalPhoto, PanoramaCloudRun
        from panorama_graph_client.sources import select_source
        from panorama_graph_client.adapter import CloudRunner
        from test_graph_client import FakeTransport
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.jpg').write_bytes(b'opaque workflow source A')
            (root / 'b.png').write_bytes(b'opaque workflow source B')
            sources = [select_source(root, 'a.jpg'), select_source(root, 'b.png')]
            transport = FakeTransport(sources)
            runner = CloudRunner(root / 'runs', 'https://mock.run.comfy.app', lambda: transport)
            cloud_node = PanoramaCloudRun(runner=runner,
                                         materialize=lambda _: ('PANORAMA_SENTINEL', 'COVERAGE_SENTINEL'))
            editor = self.tool.build_editor(2)
            for node in editor['nodes']:
                if node['id'] in (1, 2):
                    node['widgets_values'] = ['a.jpg' if node['id'] == 1 else 'b.png']
                if node['id'] == 10:
                    node['widgets_values'] = ['mock-graph', True]
            api = self.tool.export_api(editor)
            results, visible = {}, {}

            def evaluate(node_id):
                if node_id in results:
                    return results[node_id]
                node = api[node_id]
                values = {key: evaluate(value[0])[value[1]] if isinstance(value, list) else value
                          for key, value in node['inputs'].items()}
                kind = node['class_type']
                if kind == 'PanoramaOriginalPhoto':
                    result = PanoramaOriginalPhoto().load(**values)
                elif kind == 'PanoramaCloudRun':
                    result = cloud_node.execute(**values)['result']
                elif kind == 'MaskToImage':
                    self.assertEqual(values['mask'], 'COVERAGE_SENTINEL')
                    result = ('COVERAGE_DISPLAY_SENTINEL',)
                elif kind == 'PreviewImage':
                    visible[node_id] = values['images']
                    result = (values['images'],)
                else:
                    self.assertEqual(kind, 'PreviewAny')
                    visible[node_id] = values['source']
                    result = (values['source'],)
                results[node_id] = result
                return result

            with patch.dict(sys.modules, {'folder_paths': types.SimpleNamespace(get_input_directory=lambda: directory)}):
                for node_id in ('20', '22', '23'):
                    evaluate(node_id)
                self.assertEqual(visible['20'], 'PANORAMA_SENTINEL')
                self.assertEqual(visible['22'], 'COVERAGE_DISPLAY_SENTINEL')
                self.assertEqual(json.loads(visible['23'])['selected'][0]['sha256'], sources[0].sha256)
                self.assertEqual(len(transport.submissions), 1)
                self.assertEqual(transport.submissions[0]['files'][0].read_bytes(), b'opaque workflow source A')
                # Re-evaluate the same graph without caching node execution: receipts prevent resubmission.
                results.clear()
                for node_id in ('20', '22', '23'):
                    evaluate(node_id)
                self.assertEqual(len(transport.submissions), 1)


if __name__ == '__main__':
    unittest.main()
