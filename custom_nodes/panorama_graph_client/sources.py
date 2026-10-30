"""Original-byte source identities. No image libraries, decoding or re-encoding."""
from dataclasses import dataclass
import hashlib
from pathlib import Path

MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class SourceRecord:
    input_root: Path
    filename: str
    sha256: str
    size_bytes: int

    def provenance(self):
        return {'filename': self.filename, 'sha256': self.sha256,
                'size_bytes': self.size_bytes}


def select_source(input_root, filename):
    """Constrain a chooser value to a JPEG/PNG input file and hash original bytes.

    Container/header/pixel validation remains authoritative on the managed worker.
    Filename extensions are only chooser filtering, not proof of an image format.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError('Choose an original photograph.')
    name = filename.removesuffix(' [input]')
    root = Path(input_root).resolve()
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or ':' in name:
        raise ValueError('Source must be inside the editor input directory.')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Source is missing or outside the editor input directory.')
    if path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}:
        raise ValueError('Choose a JPEG or PNG original, not an output/temp asset.')
    digest, size = hash_file(path, MAX_FILE_BYTES)
    return SourceRecord(root, path.relative_to(root).as_posix(), digest, size)


def hash_file(path, limit):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError('File exceeds the allowed byte limit.')
            digest.update(chunk)
    if not size:
        raise ValueError('Empty source or artifact is not accepted.')
    return digest.hexdigest(), size


def validate_sources(sources):
    if not 2 <= len(sources) <= 6:
        raise ValueError('Select 2 to 6 original photographs.')
    checked = []
    for source in sources:
        if not isinstance(source, SourceRecord):
            raise ValueError('Connect original-source sockets, not IMAGE tensors.')
        current = select_source(source.input_root, source.filename)
        if current != source:
            raise ValueError('Original bytes changed; select the source again with a new run ID.')
        checked.append(current)
    if sum(s.size_bytes for s in checked) > MAX_TOTAL_BYTES:
        raise ValueError('Selected originals exceed the 32 MiB total limit.')
    if len({s.sha256 for s in checked}) != len(checked):
        raise ValueError('Duplicate source content is not a panorama set.')
    return checked


def snapshot_source(source, destination):
    """Copy exact bytes to a run-owned snapshot and verify while streaming."""
    checked = select_source(source.input_root, source.filename)
    if checked != source:
        raise ValueError('Original bytes changed before snapshot creation.')
    digest, size = hashlib.sha256(), 0
    with (source.input_root / source.filename).open('rb') as incoming:
        with Path(destination).open('xb') as outgoing:
            while chunk := incoming.read(64 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError('Source grew beyond the allowed byte limit.')
                digest.update(chunk)
                outgoing.write(chunk)
    if size != source.size_bytes or digest.hexdigest() != source.sha256:
        raise ValueError('Source changed while snapshotting; no upload attempted.')
