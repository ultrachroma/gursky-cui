"""Durable, explicit-run adapter. SDK and credentials are accessed only at runtime."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from .sources import hash_file, snapshot_source, validate_sources
from .connection import configured_endpoint
from .report_metadata import embedded_report, parse_report

RELEASE_ID = os.environ.get('PANORAMA_EXPECTED_RELEASE_ID', '')
BUILD_ID = os.environ.get('PANORAMA_EXPECTED_BUILD_ID', '')
ARTIFACT_LIMITS = {'panorama.png': 64 * 1024 * 1024,
                   'coverage.png': 16 * 1024 * 1024,
                   'report.json': 1024 * 1024}


def deployment_origin(value):
    try:
        parsed = urlparse(value)
        valid = (parsed.scheme == 'https' and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password
                 and re.fullmatch(r'[a-zA-Z0-9-]+\.run\.comfy\.app', parsed.hostname or '')
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError('An approved managed deployment HTTPS origin is required.')
    return 'https://' + parsed.hostname


def write_json(path, value):
    """Replace a receipt atomically; flush submission intent before remote work."""
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@dataclass(frozen=True)
class RunArtifacts:
    panorama: Path
    coverage: Path
    report: Path
    report_text: str
    receipt: Path


class LimitedWriter:
    """Stop an output stream before it exceeds the local artifact byte budget."""
    def __init__(self, stream, limit):
        self.stream, self.limit, self.written = stream, limit, 0

    def write(self, chunk):
        if self.written + len(chunk) > self.limit:
            raise ValueError('Artifact stream exceeds its byte budget.')
        count = self.stream.write(chunk)
        self.written += count
        return count


class CloudRunner:
    def __init__(self, state_root, endpoint, transport_factory):
        self.root = Path(state_root).resolve()
        self.endpoint = deployment_origin(endpoint)
        self.transport_factory = transport_factory

    def run(self, run_id, sources, allow_new_run=False):
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
            raise ValueError('Use a unique run ID with 1 to 64 letters, digits, underscores or hyphens.')
        sources = validate_sources(list(sources))
        identity = {'endpoint': self.endpoint, 'expected_release_id': RELEASE_ID,
                    'sources': [s.provenance() for s in sources], 'crop': False}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        directory = self.root / run_id
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError('Run directory escapes the state root.')
        receipt_path = directory / 'receipt.json'
        if not receipt_path.exists() and not allow_new_run:
            raise ValueError('Creating a new run requires explicit permission and a new run ID.')
        directory.mkdir(parents=True, exist_ok=True)
        # A crash leaves the lock behind deliberately. Never guess that remote work stopped.
        lock = directory / 'active.lock'
        transport = None
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError('Run is active or has a stale lock; manual reconciliation is required.') from None
        try:
            os.close(descriptor)
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
                if receipt.get('fingerprint') != fingerprint:
                    raise ValueError('Run ID already belongs to different inputs or a different endpoint.')
                if receipt.get('state') == 'completed':
                    return self._cached(directory, receipt)
                if receipt.get('state') != 'submitted' or not receipt.get('remote_job_id'):
                    raise ValueError('Previous submission requires manual reconciliation; it will not be resubmitted.')
                transport = self.transport_factory()
                try:
                    job = transport.get_job(receipt['remote_job_id'])
                except Exception:
                    raise ValueError('Remote job lookup failed; retain this run ID and resume later.') from None
            else:
                # Refuse debris from a failed snapshot or manually removed receipt.
                if any(p.name != 'active.lock' for p in directory.iterdir()):
                    raise ValueError('Run directory contains prior state; manual reconciliation is required.')
                files = []
                for index, source in enumerate(sources, 1):
                    path = directory / f'photo_{index}{Path(source.filename).suffix.lower()}'
                    snapshot_source(source, path)
                    files.append(path)
                inputs = {f'photo_{i}': '' for i in range(1, 7)}
                inputs['crop'] = False
                graph = {'1': {'class_type': 'PhotoPanorama', 'inputs': inputs}}
                # Factory checks runtime authorization before constructing an SDK client.
                transport = self.transport_factory()
                receipt = {**identity, 'fingerprint': fingerprint, 'run_id': run_id,
                           'expected_build_id': BUILD_ID, 'state': 'submitting', 'remote_job_id': None}
                write_json(receipt_path, receipt)
                try:
                    job = transport.submit(graph, files, run_id)
                    if not isinstance(job.id, str) or not job.id:
                        raise ValueError('Remote job ID missing.')
                except Exception:
                    receipt['state'] = 'uncertain'
                    write_json(receipt_path, receipt)
                    raise ValueError('Submission requires manual reconciliation; no automatic resubmit.') from None
                receipt.update(state='submitted', remote_job_id=job.id)
                write_json(receipt_path, receipt)
            # Once the job ID is durable, polling/download failures can resume by ID.
            try:
                job.result()
                artifacts, report_origin = self._download(job, directory, sources)
            except Exception:
                raise ValueError('Job results are incomplete or invalid; resume by this run ID, never resubmit.') from None
            receipt.update(state='completed', artifacts=artifacts, report_origin=report_origin)
            write_json(receipt_path, receipt)
            return self._cached(directory, receipt)
        finally:
            try:
                if transport is not None:
                    try:
                        transport.close()
                    except Exception:
                        # Pool cleanup must not expose provider details or overwrite durable job state.
                        pass
            finally:
                lock.unlink()

    def _download(self, job, directory, sources):
        found = {}
        for output in job.get_outputs('1'):
            # Remote names never become filesystem destinations.
            name = Path(output.name).name
            if name not in ARTIFACT_LIMITS:
                continue
            if name in found:
                raise ValueError('Ambiguous duplicate output name.')
            size = output.size_bytes
            if not isinstance(size, int) or not 0 < size <= ARTIFACT_LIMITS[name]:
                raise ValueError('Artifact advertised size exceeds the contract.')
            target = directory / name
            partial = target.with_suffix('.part')
            if partial.is_symlink() or target.is_symlink():
                raise ValueError('Artifact destination must not be a symbolic link.')
            with partial.open('wb') as stream:
                output.to_stream(LimitedWriter(stream, ARTIFACT_LIMITS[name]))
            digest, actual_size = hash_file(partial, ARTIFACT_LIMITS[name])
            if actual_size != size:
                raise ValueError('Artifact download is incomplete.')
            partial.replace(target)
            found[name] = {'sha256': digest, 'size_bytes': actual_size}
        if not {'panorama.png', 'coverage.png'} <= set(found):
            raise ValueError('Required panorama, coverage or report artifact is missing.')
        report_origin = 'direct_report_json'
        if 'report.json' in found:
            # A published report always wins, including when it is invalid. Do
            # not mask bad direct provenance with a second report in metadata.
            parse_report((directory / 'report.json').read_text(encoding='utf-8'), sources)
        else:
            text = embedded_report(directory / 'panorama.png', ARTIFACT_LIMITS['report.json'],
                                   ARTIFACT_LIMITS['panorama.png'])
            parse_report(text, sources)
            target = directory / 'report.json'
            partial = target.with_suffix('.part')
            if partial.is_symlink() or target.is_symlink():
                raise ValueError('Artifact destination must not be a symbolic link.')
            with partial.open('wb') as stream:
                LimitedWriter(stream, ARTIFACT_LIMITS['report.json']).write(text.encode('utf-8'))
            digest, actual_size = hash_file(partial, ARTIFACT_LIMITS['report.json'])
            partial.replace(target)
            found['report.json'] = {'sha256': digest, 'size_bytes': actual_size}
            report_origin = 'panorama_png_text'
        return found, report_origin

    def _cached(self, directory, receipt):
        artifacts = receipt.get('artifacts', {})
        if set(artifacts) != set(ARTIFACT_LIMITS):
            raise ValueError('Cached artifact inventory is incomplete.')
        for name, limit in ARTIFACT_LIMITS.items():
            digest, size = hash_file(directory / name, limit)
            if artifacts[name] != {'sha256': digest, 'size_bytes': size}:
                raise ValueError('Cached artifact changed; do not resubmit this run.')
        return RunArtifacts(directory / 'panorama.png', directory / 'coverage.png',
                            directory / 'report.json', (directory / 'report.json').read_text(encoding='utf-8'),
                            directory / 'receipt.json')


class SDKTransport:
    """Live SDK boundary. Never constructed by the offline mock suite."""
    def __init__(self, endpoint):
        if os.environ.get('PANORAMA_GRAPH_ALLOW_REMOTE') != 'approved':
            raise ValueError('Remote access disabled; obtain separate upload/run authorization.')
        expected = deployment_origin(endpoint)
        if deployment_origin(configured_endpoint()) != expected:
            raise ValueError('SDK deployment origin differs from the run receipt target.')
        key = os.environ.get('COMFY_API_KEY')
        if not key:
            raise ValueError('An approved backend API key is required at runtime.')
        # The official SDK reads this at construction. Never fall through to its Cloud default.
        os.environ['COMFY_BASE_URL'] = expected
        from comfy_sdk import Comfy
        self.client = Comfy(api_key=key)

    def submit(self, graph, files, key):
        workflow = self.client.workflows.from_json(graph)
        for index, path in enumerate(files, 1):
            workflow.set_input('1', f'photo_{index}', self.client.assets.from_file(path))
        return self.client.submit(workflow, idempotency_key=key)

    def get_job(self, job_id):
        return self.client.jobs.get(job_id)

    def close(self):
        self.client.close()
