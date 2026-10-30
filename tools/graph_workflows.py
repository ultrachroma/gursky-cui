"""Author/check this project's editor templates without importing or executing nodes.

This is a bounded exporter for the five listed node types and frontend Note,
not a general replacement for ComfyUI's frontend graphToPrompt exporter.
"""
import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {
    'PanoramaOriginalPhoto': {
        'widgets': ['image'], 'widget_types': [str], 'inputs': [], 'required': [],
        'outputs': ['PANORAMA_SOURCE'], 'output_names': ['original_source'],
    },
    'PanoramaCloudRun': {
        'widgets': ['run_id', 'allow_new_run'], 'widget_types': [str, bool],
        'inputs': [(f'photo_{i}', 'PANORAMA_SOURCE') for i in range(1, 7)],
        'required': ['photo_1', 'photo_2'],
        'outputs': ['IMAGE', 'MASK', 'STRING', 'PANORAMA_ARTIFACTS'],
        'output_names': ['panorama', 'coverage_white_is_valid', 'report_json', 'original_artifacts'],
    },
    'PreviewImage': {
        'widgets': [], 'widget_types': [], 'inputs': [('images', 'IMAGE')],
        'required': ['images'], 'outputs': ['IMAGE'], 'output_names': ['images'],
    },
    'MaskToImage': {
        'widgets': [], 'widget_types': [], 'inputs': [('mask', 'MASK')],
        'required': ['mask'], 'outputs': ['IMAGE'], 'output_names': ['IMAGE'],
    },
    'PreviewAny': {
        'widgets': [], 'widget_types': [], 'inputs': [('source', '*')],
        'required': ['source'], 'outputs': ['STRING'], 'output_names': ['STRING'],
    },
}
TEMPLATES = ((2, 'panorama_editor'), (6, 'panorama_editor_six_photos'))


def make_node(node_id, kind, title, pos, size, widgets=None):
    contract = CONTRACTS[kind]
    return {'id': node_id, 'type': kind, 'title': title, 'pos': pos, 'size': size,
            'flags': {}, 'order': node_id, 'mode': 0,
            'inputs': [{'name': name, 'type': datatype, 'link': None}
                       for name, datatype in contract['inputs']],
            'outputs': [{'name': name, 'type': datatype, 'links': [], 'slot_index': index}
                        for index, (name, datatype) in enumerate(zip(contract['output_names'], contract['outputs']))],
            'properties': {'Node name for S&R': kind},
            'widgets_values': [] if widgets is None else widgets}


def build_editor(count=2):
    if type(count) is not int or not 2 <= count <= 6:
        raise ValueError('Editor template needs 2 to 6 source loaders.')
    source_height = max(650, count * 250 + 40)
    nodes = [make_node(i, 'PanoramaOriginalPhoto', f'Photo {i} — original bytes',
                       [40, 60 + (i - 1) * 250], [320, 210], ['']) for i in range(1, count + 1)]
    nodes += [
        make_node(10, 'PanoramaCloudRun', 'Panorama — managed cloud execution', [460, 130], [390, 310], ['', False]),
        make_node(20, 'PreviewImage', 'Panorama — uncropped preview', [950, 70], [580, 330]),
        make_node(21, 'MaskToImage', 'Coverage display conversion', [460, 540], [330, 90]),
        make_node(22, 'PreviewImage', 'Coverage — white = covered', [950, 480], [580, 330]),
        make_node(23, 'PreviewAny', 'Worker report — source hashes and diagnostics', [1630, 70], [620, 740]),
        {'id': 99, 'type': 'Note', 'title': 'Setup and review', 'pos': [440, 730], 'size': [420, 280],
         'flags': {}, 'order': 99, 'mode': 0, 'inputs': [], 'outputs': [], 'properties': {},
         'widgets_values': [
             'SOURCE TEMPLATE — not runtime validated.\n'
             'Select 2–6 originals using the loader upload controls.\n'
             'Two-photo template: duplicate loaders for optional sockets photo_3–photo_6.\n'
             'Keep auto-queue off. Blank run_id and allow_new_run=false are deliberate.\n'
             'Cloud access still needs separate approval and backend configuration.\n'
             'Review uncropped panorama, white-valid coverage and worker report.\n'
             'Same run_id reuses its receipt; never change it to bypass an uncertain job.\n'
             'Core preview files are derivatives. Retain downloaded originals and report.\n'
             'No crop/manual alignment tools in this slice. See workflows/README.md.']},
    ]
    by_id = {node['id']: node for node in nodes}
    links = []

    def connect(origin_id, origin_slot, target_id, target_slot):
        link_id = len(links) + 1
        source = by_id[origin_id]['outputs'][origin_slot]
        target = by_id[target_id]['inputs'][target_slot]
        links.append({'id': link_id, 'origin_id': origin_id, 'origin_slot': origin_slot,
                      'target_id': target_id, 'target_slot': target_slot, 'type': source['type']})
        source['links'].append(link_id)
        target['link'] = link_id

    for index in range(count):
        connect(index + 1, 0, 10, index)
    connect(10, 0, 20, 0)
    connect(10, 1, 21, 0)
    connect(21, 0, 22, 0)
    connect(10, 2, 23, 0)
    groups = [
        {'id': 1, 'title': '1. Choose original photos', 'bounding': [10, 10, 380, source_height], 'color': '#3f789e'},
        {'id': 2, 'title': '2. Cloud run and receipt', 'bounding': [420, 10, 470, 1040], 'color': '#797345'},
        {'id': 3, 'title': '3. Review panorama and coverage', 'bounding': [920, 10, 640, 840], 'color': '#47775a'},
        {'id': 4, 'title': '4. Review provenance and diagnostics', 'bounding': [1600, 10, 680, 840], 'color': '#6c527c'},
    ]
    editor = {'version': 1, 'state': {'lastGroupid': 4, 'lastNodeId': 99,
                                     'lastLinkId': len(links), 'lastRerouteId': 0},
              'nodes': nodes, 'links': links, 'groups': groups, 'config': {},
              'extra': {'ds': {'scale': 0.65, 'offset': [30, 40]}}}
    validate_editor(editor)
    return editor


def validate_editor(editor):
    """Validate this bounded v1 graph's documented shape, sockets and backreferences.

    This is project contract validation, not full official JSON Schema validation
    and not proof that a particular frontend can open/render the file.
    """
    if editor.get('version') != 1 or not isinstance(editor.get('state'), dict):
        raise ValueError('Expected an editor-format v1 graph with state.')
    nodes, links = editor.get('nodes'), editor.get('links')
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise ValueError('Editor nodes and links must be lists.')
    by_id = {}
    for node in nodes:
        node_id = node.get('id')
        if type(node_id) is not int or node_id in by_id:
            raise ValueError('Node IDs must be unique integers.')
        by_id[node_id] = node
        if node.get('mode') != 0:
            raise ValueError('This exporter supports active nodes only, not mute/bypass modes.')
        if not isinstance(node.get('flags'), dict) or not isinstance(node.get('properties'), dict):
            raise ValueError('Node flags and properties must be objects.')
        for field in ('pos', 'size'):
            value = node.get(field)
            if (not isinstance(value, list) or len(value) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
                    or (field == 'size' and min(value) <= 0)):
                raise ValueError('Node geometry must contain two finite coordinates and positive sizes.')
        kind = node.get('type')
        if kind == 'Note':
            if node.get('inputs') or node.get('outputs'):
                raise ValueError('Frontend Note cannot carry execution links.')
            continue
        if kind not in CONTRACTS:
            raise ValueError('Unknown node type in bounded workflow exporter.')
        contract = CONTRACTS[kind]
        if [(s.get('name'), s.get('type')) for s in node.get('inputs', [])] != contract['inputs']:
            raise ValueError('Node input socket order or type differs from its contract.')
        if [s.get('type') for s in node.get('outputs', [])] != contract['outputs']:
            raise ValueError('Node output socket order or type differs from its contract.')
        values = node.get('widgets_values')
        if (not isinstance(values, list) or len(values) != len(contract['widgets'])
                or any(type(v) is not expected for v, expected in zip(values, contract['widget_types']))):
            raise ValueError('Widget count/type/order differs from its node contract.')
    link_ids, destinations = set(), set()
    link_targets = {}
    expected_outputs = {}
    for link in links:
        link_id = link.get('id')
        if type(link_id) is not int or link_id in link_ids:
            raise ValueError('Link IDs must be unique integers.')
        link_ids.add(link_id)
        try:
            origin, target = by_id[link['origin_id']], by_id[link['target_id']]
            a, b = link['origin_slot'], link['target_slot']
            if type(a) is not int or type(b) is not int or min(a, b) < 0:
                raise ValueError('Link slots must be nonnegative integers.')
            output, input_slot = origin['outputs'][a], target['inputs'][b]
        except (KeyError, IndexError, TypeError):
            raise ValueError('Link references a missing node/socket.') from None
        destination = (target['id'], b)
        if destination in destinations or input_slot.get('link') != link_id:
            raise ValueError('Input link backreference is missing or ambiguous.')
        destinations.add(destination)
        link_targets[link_id] = destination
        if link.get('type') != output['type'] or input_slot['type'] not in (output['type'], '*'):
            raise ValueError('Link socket types do not match.')
        expected_outputs.setdefault((origin['id'], a), []).append(link_id)
    for node in nodes:
        for index, output in enumerate(node.get('outputs', [])):
            if sorted(output.get('links') or []) != sorted(expected_outputs.get((node['id'], index), [])):
                raise ValueError('Output link backreference is missing or stale.')
        for index, input_slot in enumerate(node.get('inputs', [])):
            if (input_slot.get('link') is not None
                    and link_targets.get(input_slot['link']) != (node['id'], index)):
                raise ValueError('Input references a missing link or another socket\'s link.')
            if (input_slot.get('link') is None
                    and input_slot['name'] in CONTRACTS.get(node['type'], {}).get('required', [])):
                raise ValueError('A required input socket is disconnected.')
    if nodes and editor['state'].get('lastNodeId', -1) < max(by_id):
        raise ValueError('Editor node counter is stale.')
    if links and editor['state'].get('lastLinkId', -1) < max(link_ids):
        raise ValueError('Editor link counter is stale.')
    indegree = {node_id: 0 for node_id in by_id}
    children = {node_id: [] for node_id in by_id}
    for link in links:
        indegree[link['target_id']] += 1
        children[link['origin_id']].append(link['target_id'])
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node_id = ready.pop()
        visited += 1
        for child in children[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(by_id):
        raise ValueError('Execution graph contains a cycle.')


def export_api(editor):
    validate_editor(editor)
    links = {link['id']: link for link in editor['links']}
    api = {}
    for node in editor['nodes']:
        if node['type'] == 'Note':
            continue
        contract = CONTRACTS[node['type']]
        inputs = dict(zip(contract['widgets'], node['widgets_values']))
        for input_slot in node['inputs']:
            if input_slot.get('link') is not None:
                link = links[input_slot['link']]
                inputs[input_slot['name']] = [str(link['origin_id']), link['origin_slot']]
        api[str(node['id'])] = {'class_type': node['type'], 'inputs': inputs,
                               '_meta': {'title': node.get('title', node['type'])}}
    return api


def check_templates():
    for count, stem in TEMPLATES:
        editor = json.loads((ROOT / 'workflows' / f'{stem}.json').read_text(encoding='utf-8'))
        api = json.loads((ROOT / 'workflows' / f'{stem}_api.json').read_text(encoding='utf-8'))
        if editor != build_editor(count) or api != export_api(editor):
            raise ValueError(f'Template/export drift: {stem}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Write only the four project template JSON files.')
    args = parser.parse_args()
    if args.write:
        (ROOT / 'workflows').mkdir(exist_ok=True)
        for count, stem in TEMPLATES:
            editor = build_editor(count)
            for suffix, value in (('', editor), ('_api', export_api(editor))):
                (ROOT / 'workflows' / f'{stem}{suffix}.json').write_text(
                    json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    check_templates()
    print('PASS: both editor templates and client API exports have consistent typed links and disabled submissions. No nodes executed.')


if __name__ == '__main__':
    main()
