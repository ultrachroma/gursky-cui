"""Read only the sealed worker's uncompressed PNG report, without decoding pixels."""
import json
import math
import struct
import zlib


def parse_report(text, sources):
    """Apply identical strict JSON and ordered source provenance checks to both paths."""
    def reject_constant(value):
        raise ValueError('Non-finite JSON number.')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Non-finite JSON number.')
        return number

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON member.')
            result[key] = value
        return result

    report = json.loads(text, parse_constant=reject_constant, parse_float=finite_float,
                        object_pairs_hook=unique_object)
    if not isinstance(report, dict):
        raise ValueError('Worker report must be a JSON object.')
    records = report.get('selected')
    if (report.get('status') != 'completed' or not isinstance(records, list)
            or len(records) != len(sources)
            or any(not isinstance(record, dict) or record.get('status') != 'used'
                   or record.get('sha256') != source.sha256
                   for record, source in zip(records, sources))):
        raise ValueError('Worker report does not account for every original source.')
    return report


def embedded_report(path, report_limit, png_limit):
    """Scan bounded PNG chunks/CRCs; accept only the worker's tEXt encoding.

    This validates the metadata container, not pixel data or image quality. The
    runtime materializer retains responsibility for actual PNG/pixel acceptance.
    Compressed/international report chunks fail closed rather than introducing a
    decompression path. The sealed worker uses PngInfo.add_text with ASCII JSON.
    """
    if not 0 < path.stat().st_size <= png_limit:
        raise ValueError('PNG exceeds its byte budget.')
    report = None
    text_bytes = 0
    saw_data = False
    with path.open('rb') as stream:
        if stream.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Report fallback requires a PNG container.')
        for index in range(10000):
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError('Truncated PNG chunk header.')
            length, kind = struct.unpack('>I4s', header)
            if (length > png_limit or length + 4 > path.stat().st_size - stream.tell()
                    or not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in kind)):
                raise ValueError('Invalid PNG chunk bounds or type.')
            if (index == 0 and (kind != b'IHDR' or length != 13)
                    or index > 0 and kind == b'IHDR'):
                raise ValueError('Invalid PNG header placement.')
            metadata = kind in (b'tEXt', b'zTXt', b'iTXt')
            if metadata:
                text_bytes += length
                if text_bytes > report_limit + 80:
                    raise ValueError('PNG text exceeds its metadata budget.')
            crc = zlib.crc32(kind)
            chunks = []
            remaining = length
            while remaining:
                data = stream.read(min(remaining, 65536))
                if not data:
                    raise ValueError('Truncated PNG chunk.')
                remaining -= len(data)
                crc = zlib.crc32(data, crc)
                if metadata or kind == b'IHDR':
                    chunks.append(data)
            checksum = stream.read(4)
            if len(checksum) != 4 or struct.unpack('>I', checksum)[0] != crc & 0xffffffff:
                raise ValueError('PNG chunk checksum mismatch.')
            if kind == b'IHDR':
                width, height = struct.unpack('>II', b''.join(chunks)[:8])
                if not width or not height or max(width, height) > 16000 or width * height > 24000000:
                    raise ValueError('PNG dimensions exceed the worker output bounds.')
            if metadata:
                keyword, separator, value = b''.join(chunks).partition(b'\0')
                if not separator or not 1 <= len(keyword) <= 79:
                    raise ValueError('Malformed PNG text keyword.')
                if keyword == b'panorama_report':
                    if kind != b'tEXt' or report is not None or not value or b'\0' in value:
                        raise ValueError('Unsupported, duplicate or malformed embedded report.')
                    report = value.decode('latin-1')
                    if len(report.encode('utf-8')) > report_limit:
                        raise ValueError('Embedded report exceeds its byte budget.')
            if kind == b'IDAT':
                saw_data = True
            if kind == b'IEND':
                if length or not saw_data or stream.read(1) or report is None:
                    raise ValueError('Incomplete or ambiguous PNG report container.')
                return report
        raise ValueError('PNG chunk count exceeds its budget.')
