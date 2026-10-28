"""Exercise real CLI output files with bounded, local I/O fault injection."""
import errno
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize
from infra_artifact_ledger import cli, storage
from test_public_contract import append, create, import_request, wire


class OutputPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root/'ledger.sqlite'
        self.ledger = initialize(self.db)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.ledger.close)
        self.raw = b'x'*(1048576+17)
        self.ledger.execute(wire(create()))
        self.ledger.execute(wire(append(raw=self.raw)), payloads={'blob:one': self.raw})

    def output_args(self, directory, output):
        if output == 'blob':
            tokens = ['read-blob', '--db', self.db, '--blob-ref', 'blob:one', '--output', directory/'output']
        else:
            tokens = ['export', '--db', self.db, '--package', directory/'package', '--descriptor', directory/'descriptor']
        return cli.parser().parse_args(list(map(str, tokens)))

    def test_corrupt_staging_file_is_rejected_before_publication(self):
        before = self.db.read_bytes()
        original_operation = self.ledger.get_operation('demo:scope', 'append_version', 'append')
        original_bundle = self.ledger.export_bundle()
        real_fsync = os.fsync
        for output, stage in (('blob', 1), ('package', 1), ('descriptor', 2)):
            for damage in ('truncate', 'extend', 'change-byte'):
                with self.subTest(output=output, damage=damage):
                    directory = self.root/(output+'-'+damage)
                    directory.mkdir()
                    seen = 0
                    def damage_then_sync(fd):
                        nonlocal seen
                        info = os.fstat(fd)
                        if stat.S_ISREG(info.st_mode):
                            seen += 1
                            if seen == stage:
                                if damage == 'truncate':
                                    os.ftruncate(fd, info.st_size-1)
                                else:
                                    offset = info.st_size if damage == 'extend' else info.st_size//2
                                    os.pwrite(fd, b'!', offset)
                        return real_fsync(fd)
                    with patch.object(storage.os, 'fsync', side_effect=damage_then_sync):
                        with self.assertRaises(LedgerError) as caught:
                            cli.run(self.output_args(directory, output))
                    self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                     ('INTEGRITY_FAILURE', 'not_applicable'))
                    self.assertEqual(seen, stage)
                    target = directory/('output' if output == 'blob' else output)
                    self.assertFalse(target.exists())
                    if output == 'descriptor':
                        self.assertEqual((directory/'package').read_bytes(), original_bundle['package_utf8'])
                    elif output == 'package':
                        self.assertFalse((directory/'descriptor').exists())
                    self.assertFalse(list(directory.glob('.ledger-b-output-*')))
                    self.assertEqual(self.db.read_bytes(), before)
                    self.assertEqual(self.ledger.get_operation('demo:scope', 'append_version', 'append'), original_operation)

    def test_output_io_failures_preserve_existing_result_and_allow_new_output(self):
        before = self.db.read_bytes()
        real_fdopen, real_fsync, real_link = os.fdopen, os.fsync, os.link
        for fault in ('read', 'file-sync', 'link', 'directory-sync'):
            with self.subTest(fault=fault):
                directory = self.root/fault
                directory.mkdir()
                class ReadFailure:
                    def __init__(self, stream): self.stream = stream
                    def __getattr__(self, name): return getattr(self.stream, name)
                    def __enter__(self): return self
                    def __exit__(self, *args): return self.stream.__exit__(*args)
                    def read(self, *args): raise OSError(errno.EIO, 'injected output read failure')
                def open_output(fd, mode='r', *args, **kwargs):
                    stream = real_fdopen(fd, mode, *args, **kwargs)
                    return ReadFailure(stream) if fault == 'read' and 'w' in mode else stream
                def sync_output(fd):
                    is_file = stat.S_ISREG(os.fstat(fd).st_mode)
                    if (fault == 'file-sync' and is_file) or (fault == 'directory-sync' and not is_file):
                        raise OSError(errno.EIO, 'injected output synchronization failure')
                    return real_fsync(fd)
                def link_output(*args, **kwargs):
                    if fault == 'link': raise OSError(errno.ENOSPC, 'injected publication failure')
                    return real_link(*args, **kwargs)
                with patch.object(storage.os, 'fdopen', side_effect=open_output), \
                     patch.object(storage.os, 'fsync', side_effect=sync_output), \
                     patch.object(storage.os, 'link', side_effect=link_output):
                    with self.assertRaises(LedgerError) as caught:
                        cli.run(self.output_args(directory, 'blob'))
                self.assertEqual((caught.exception.code, caught.exception.commit_state), ('IO_ERROR', 'not_applicable'))
                target = directory/'output'
                self.assertEqual(target.exists(), fault == 'directory-sync')
                if target.exists(): self.assertEqual(target.read_bytes(), self.raw)
                self.assertEqual(self.db.read_bytes(), before)
                self.assertFalse(list(directory.glob('.ledger-b-output-*')))
                retry = directory/'retry'
                retry.mkdir()
                self.assertEqual(cli.run(self.output_args(retry, 'blob'))['status'], 'OK')
                self.assertEqual((retry/'output').read_bytes(), self.raw)

    def test_complete_empty_and_chunk_boundary_outputs_remain_importable(self):
        for size in (0, 1048575, 1048576, 1048577):
            raw = (bytes(range(256))*4097)[:size]
            name = str(size)
            self.ledger.execute(wire(append('version:'+name, 'blob:'+name, 'root:'+name, raw, key=name)),
                                payloads={'blob:'+name: raw})
            args = cli.parser().parse_args(['read-blob', '--db', str(self.db), '--blob-ref', 'blob:'+name,
                                            '--output', str(self.root/name)])
            self.assertEqual(cli.run(args)['status'], 'OK')
            self.assertEqual((self.root/name).read_bytes(), raw)
        directory = self.root/'complete'
        directory.mkdir()
        self.assertEqual(cli.run(self.output_args(directory, 'package'))['status'], 'OK')
        with initialize(self.root/'imported.sqlite') as imported:
            imported.execute(wire(import_request()), package=(directory/'package').read_bytes(),
                             descriptor=(directory/'descriptor').read_bytes())
            self.assertEqual(imported.get_history('artifact:report'), self.ledger.get_history('artifact:report'))
            self.assertEqual(imported.read_blob('blob:one'), self.raw)
            self.assertEqual(imported.verify()['counts']['versions'], 5)


if __name__ == '__main__':
    unittest.main()
