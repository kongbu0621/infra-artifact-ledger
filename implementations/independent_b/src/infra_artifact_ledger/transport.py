"""A1 portable bytes, implemented independently from the published profile."""
import base64
import binascii
from . import contract as c


def unpack(package, descriptor):
    c.require(type(package) is bytes, 'package bytes')
    c.bounded(len(package), c.PACKAGE, 'package bytes')
    desc = c.parse(descriptor, 4096)
    c.fields(desc, ('transport_version', 'package_byte_length', 'package_digest'))
    c.require(desc['transport_version'] == c.VERSION, 'transport version', 'UNSUPPORTED_VERSION')
    c.integer(desc['package_byte_length'])
    c.bounded(desc['package_byte_length'], c.PACKAGE, 'package bytes')
    c.digest_shape(desc['package_digest'])
    c.require(len(package) == desc['package_byte_length'] and c.digest(package) == desc['package_digest'],
              'package descriptor mismatch', 'INTEGRITY_FAILURE')
    value = c.parse(package, c.PACKAGE)
    c.fields(value, ('transport_version', 'profile', 'metadata', 'payloads'))
    c.require(value['transport_version'] == c.VERSION, 'transport version', 'UNSUPPORTED_VERSION')
    c.require(value['profile'] == c.PROFILE, 'profile', 'UNSUPPORTED_PROFILE')
    meta_raw = decode_entry(value['metadata'], c.DOCUMENT)
    meta = c.parse(meta_raw)
    objects, operations = c.metadata(meta)
    c.require(type(value['payloads']) is list, 'payload array')
    expected = {identity for identity, (kind, _) in objects.items() if kind == 'blob'}
    payloads, total = {}, 0
    for entry in value['payloads']:
        c.fields(entry, ('blob_ref', 'encoding', 'data', 'byte_length', 'digest'))
        c.stable(entry['blob_ref'])
        identity = entry['blob_ref']
        c.require(identity in expected and identity not in payloads, 'payload coverage')
        raw = decode_entry({key: item for key, item in entry.items() if key != 'blob_ref'},
                           min(c.BLOB, c.PAYLOAD - total))
        total += len(raw)
        c.check_bytes(objects[identity][1], raw)
        payloads[identity] = raw
    c.require(payloads.keys() == expected, 'missing payload', 'INTEGRITY_FAILURE')
    return meta, objects, operations, payloads


def decode_entry(entry, maximum):
    c.fields(entry, ('encoding', 'data', 'byte_length', 'digest'))
    c.require(entry['encoding'] == 'base64' and type(entry['data']) is str, 'base64 encoding')
    c.integer(entry['byte_length'])
    c.digest_shape(entry['digest'])
    c.bounded(entry['byte_length'], maximum, 'decoded bytes')
    c.bounded(len(entry['data']), 4 * ((maximum + 2) // 3), 'encoded bytes')
    try:
        raw = base64.b64decode(entry['data'], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise c.LedgerError('INVALID_INPUT', 'invalid base64') from exc
    c.bounded(len(raw), maximum, 'decoded bytes')
    c.require(base64.b64encode(raw).decode('ascii') == entry['data'], 'noncanonical base64')
    c.check_bytes(entry, raw)
    return raw


def encode_entry(raw):
    return dict(encoding='base64', data=base64.b64encode(raw).decode('ascii'),
                byte_length=len(raw), digest=c.digest(raw))


def pack(meta, read_blob):
    raw_meta = c.encode(meta)
    c.bounded(len(raw_meta), c.DOCUMENT, 'metadata bytes')
    # Also enforce JSON depth and nodes on exports, not just imports.
    c.parse(raw_meta)
    total = sum(item['byte_length'] for item in meta['blobs'])
    c.bounded(total, c.PAYLOAD, 'export payload bytes')
    payloads = [dict(blob_ref=item['blob_ref'], **encode_entry(read_blob(item['blob_ref'])))
                for item in meta['blobs']]
    value = dict(transport_version=c.VERSION, profile=c.PROFILE,
                 metadata=encode_entry(raw_meta), payloads=payloads)
    package = c.encode(value)
    c.bounded(len(package), c.PACKAGE, 'package bytes')
    c.parse(package, c.PACKAGE)
    descriptor = c.encode(dict(transport_version=c.VERSION, package_byte_length=len(package),
                               package_digest=c.digest(package)))
    return dict(package_utf8=package, descriptor_utf8=descriptor)
