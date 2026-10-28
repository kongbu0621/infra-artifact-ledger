"""Public-path regressions for output resource and database-file protection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from infra_artifact_ledger import LedgerError, initialize
from test_public_contract import append, create, wire


def nodes(value):
    pending, count = [value], 0
    while pending:
        item = pending.pop()
        count += 1
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return count


class ReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root/'data.sqlite'
        self.ledger = initialize(self.db)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.ledger.close)

    def cli(self, *args):
        isolation = ['-I'] if sys.flags.isolated else []
        return subprocess.run([sys.executable, *isolation, '-m', 'infra_artifact_ledger', *map(str, args)],
                              capture_output=True, timeout=20)

    def seed(self):
        self.ledger.execute(wire(create()))
        self.ledger.execute(wire(append(raw=b'original')), payloads={'blob:one': b'original'})

    def test_output_targets_cannot_publish_into_database_sidecars(self):
        self.seed()
        original = self.db.read_bytes()
        alias = self.root/'parent-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        for index, base in enumerate((self.db, alias/self.db.name)):
            for suffix in ('-journal', '-wal', '-shm'):
                for command in ('read-blob', 'package', 'descriptor'):
                    with self.subTest(alias=index, suffix=suffix, command=command):
                        protected = Path(str(base)+suffix)
                        other = self.root/('other-'+str(index)+suffix+command)
                        if command == 'read-blob':
                            args = ('read-blob', '--db', self.db, '--blob-ref', 'blob:one', '--output', protected)
                        elif command == 'package':
                            args = ('export', '--db', self.db, '--package', protected, '--descriptor', other)
                        else:
                            args = ('export', '--db', self.db, '--package', other, '--descriptor', protected)
                        result = self.cli(*args)
                        self.assertEqual(result.returncode, 2, result.stdout)
                        self.assertEqual(json.loads(result.stdout)['code'], 'INVALID_INPUT')
                        self.assertFalse(protected.exists())
                        self.assertFalse(other.exists(), 'all destinations must be checked before publication')
                        self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(self.ledger.read_blob('blob:one'), b'original')
        # Similar names are not reserved; ordinary output remains usable.
        output = self.root/'data.sqlite-journal-copy'
        self.assertEqual(self.cli('read-blob', '--db', self.db, '--blob-ref', 'blob:one', '--output', output).returncode, 0)
        self.assertEqual(output.read_bytes(), b'original')

    def test_fifo_request_is_rejected_without_blocking_or_writing(self):
        fifo = self.root/'request.pipe'
        os.mkfifo(fifo)
        isolation = ['-I'] if sys.flags.isolated else []
        process = subprocess.Popen([sys.executable, *isolation, '-m', 'infra_artifact_ledger',
            'write', '--db', str(self.db), '--request', str(fifo)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            try:
                output, error = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail('CLI blocked opening a non-regular input')
            self.assertEqual(process.returncode, 2, (output, error))
            self.assertEqual(json.loads(output)['code'], 'INVALID_INPUT')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(self.ledger.verify()['counts']['artifacts'], 0)

    def test_history_response_at_node_limit_and_one_over(self):
        # Each input and each version is valid; the combined response is what
        # crosses the limit. Construct actual public writes, not private rows.
        for limit in (500000, 500001):
            with self.subTest(nodes=limit):
                db = self.root/f'history-{limit}.sqlite'
                with initialize(db) as ledger:
                    ledger.execute(wire(create()))
                    first = append(raw=b'')
                    first['body']['version']['external_source_refs'] = [str(i) for i in range(249970)]
                    ledger.execute(wire(first), payloads={'blob:one': b''})
                    second = append('version:two', raw=b'', parents=['version:one'], key='second')
                    second['body'].update(content_roots=[], blobs=[])
                    second['body']['version']['external_source_refs'] = []
                    future = {**second['body']['version'], 'recorded_at': '2026-10-29T00:00:00Z'}
                    history = ledger.get_history('artifact:report')
                    history['versions'].append(future)
                    envelope = dict(status='OK', commit_state='not_applicable', data=history)
                    refs = [str(i) for i in range(limit-nodes(envelope))]
                    future['external_source_refs'] = refs
                    self.assertEqual(nodes(envelope), limit)
                    self.assertLess(len(wire(envelope))+1, 8*1048576)
                    second['body']['version']['external_source_refs'] = refs
                    ledger.execute(wire(second), payloads={})
                    before = db.read_bytes()
                    if limit == 500000:
                        self.assertEqual(nodes(dict(status='OK', commit_state='not_applicable',
                            data=ledger.get_history('artifact:report'))), limit)
                    else:
                        with self.assertRaises(LedgerError) as caught:
                            ledger.get_history('artifact:report')
                        self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                         ('RESOURCE_LIMIT', 'not_applicable'))
                    result = self.cli('history', '--db', db, '--artifact-id', 'artifact:report')
                    self.assertEqual(result.returncode, 0 if limit == 500000 else 2, result.stderr)
                    if limit > 500000:
                        self.assertEqual(json.loads(result.stdout)['code'], 'RESOURCE_LIMIT')
                    self.assertEqual(db.read_bytes(), before)
                    self.assertEqual(ledger.get_operation('demo:scope', 'append_version', 'second')['result']['version_id'], 'version:two')
                    self.assertEqual(ledger.read_blob('blob:one'), b'')


if __name__ == '__main__':
    unittest.main()
