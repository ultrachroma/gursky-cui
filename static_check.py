import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parent
errors = []
excluded = {'.venv', '.venv-app', '.test_runs', '__pycache__', '.uv-cache', 'cli_state'}
python_files = sorted(p for p in root.rglob('*.py') if not excluded.intersection(p.relative_to(root).parts))
for path in python_files:
    try:
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    except (SyntaxError, UnicodeError) as exc:
        errors.append(f'{path.relative_to(root)}: {exc}')
workflow = json.loads((root / 'workflow_api.json').read_text())
assert set(workflow) == {'1'}
node = workflow['1']
assert node['class_type'] == 'PhotoPanorama'
assert set(node['inputs']) == {*(f'photo_{i}' for i in range(1, 7)), 'crop'}
for path in (p for p in root.rglob('*.json') if not excluded.intersection(p.relative_to(root).parts)):
    json.loads(path.read_text(encoding='utf-8-sig'))
if errors:
    raise SystemExit('\n'.join(errors))
print(f'PASS: parsed {len(python_files)} Python sources; JSON syntax and workflow contract checked. No project imports or image processing.')


