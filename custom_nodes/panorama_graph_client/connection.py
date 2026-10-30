"""User-configured deployment selection; no credential or network access."""
import json
import os
from pathlib import Path


def configured_endpoint():
    override = os.environ.get('COMFY_BASE_URL', '').strip()
    if override:
        return override
    target = json.loads(Path(__file__).with_name('deployment_target.json').read_text(encoding='utf-8'))
    endpoint = target.get('base_url', '').strip()
    if not endpoint:
        raise ValueError('Set COMFY_BASE_URL to your own managed deployment HTTPS origin before use.')
    return endpoint
