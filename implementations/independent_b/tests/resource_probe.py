"""Opt-in real 64/256 MiB limits. Uses a NEW synthetic local directory only."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

# -I intentionally excludes this directory; load only the public fixture builder.
import importlib.util
spec = importlib.util.spec_from_file_location('public_fixtures', Path(__file__).with_name('test_public_contract.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
from infra_artifact_ledger import LedgerError, initialize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workdir', type=Path, required=True)
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    raw = b'x' * (64 * 1048576)
    with initialize(args.workdir/'large.sqlite') as ledger:
        ledger.execute(fixture.wire(fixture.create()))
        for i in range(5):
            value = fixture.append('version:' + str(i), 'blob:' + str(i), 'root:' + str(i), raw, key='large:' + str(i))
            ledger.execute(fixture.wire(value), payloads={'blob:' + str(i): raw})
            assert hashlib.sha256(ledger.read_blob('blob:' + str(i))).digest() == hashlib.sha256(raw).digest()
        oversized = raw + b'x'
        try:
            ledger.execute(fixture.wire(fixture.append('version:over', 'blob:over', 'root:over', oversized, key='over')),
                           payloads={'blob:over': oversized})
        except LedgerError as exc:
            assert (exc.code, exc.commit_state) == ('RESOURCE_LIMIT', 'not_committed')
        else:
            raise AssertionError('accepted 64 MiB + 1')
        del oversized
        def reuse(count):
            value = fixture.append('version:reuse' + str(count), root='root:reuse' + str(count), key='reuse' + str(count))
            body = value['body']
            manifest = 'manifest:reuse' + str(count)
            entries = [dict(entry_key=str(i), blob_ref='blob:' + str(i)) for i in range(count)]
            # Repeat one BlobRef under a different logical key. It counts once.
            entries.append(dict(entry_key='same-ref', blob_ref='blob:0'))
            body.update(blobs=[], content_roots=[dict(content_root_ref='root:reuse' + str(count),
                kind='manifest', manifest_ref=manifest)], manifests=[dict(manifest_ref=manifest,
                entries=entries, digest=fixture.sha(fixture.wire(dict(entries=entries))))])
            return ledger.execute(fixture.wire(value), payloads={})
        reuse(4)  # 256 MiB, even though zero bytes were retransmitted.
        before = ledger.verify()
        assert before['verified_byte_length'] == 320 * 1048576
        try:
            reuse(5)
        except LedgerError as exc:
            assert (exc.code, exc.commit_state) == ('RESOURCE_LIMIT', 'not_committed')
        else:
            raise AssertionError('accepted 320 MiB closure')
        assert ledger.verify() == before
        # A whole-ledger export is bounded even when historical growth is legal.
        try:
            ledger.export_bundle()
        except LedgerError as exc:
            assert exc.code == 'RESOURCE_LIMIT' and exc.commit_state == 'not_applicable'
        else:
            raise AssertionError('exported >256 MiB payload')
    result = dict(status='PASS', blob_limit_bytes=64*1048576,
                  accepted_reuse_closure_bytes=256*1048576,
                  historical_bytes=320*1048576, elapsed_seconds=round(time.monotonic()-started, 3),
                  max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  python=sys.version.split()[0])
    (args.workdir/'summary.json').write_bytes(fixture.wire(result)+b'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
