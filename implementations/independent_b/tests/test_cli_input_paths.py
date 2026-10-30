"""The two public CLIs agree on invalid paths and genuine I/O failures."""
from contextlib import chdir
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, cli, initialize, open as open_ledger
from test_public_contract import append, create, import_request, wire


class CLIInputPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input = self.root / 'input'
        self.input.write_bytes(b'abc')
        self.name_limit = os.pathconf(self.root, 'PC_NAME_MAX')
        self.overlong = str(self.root / ('x' * (self.name_limit + 1)))

    def read(self, path):
        if hasattr(cli, 'read_file'):
            return cli.read_file(path, 3)
        return cli._read_file(path, 3, 'input')

    def invoke(self, args):
        # Bind source runs to the loaded package, not ambient import settings.
        # Installed runs keep -I and ignore ambient source search paths.
        isolation = ['-I'] if sys.flags.isolated else []
        environment = os.environ.copy()
        if not sys.flags.isolated:
            environment['PYTHONPATH'] = str(Path(cli.__file__).resolve().parents[1])
        return subprocess.run([sys.executable, *isolation, '-B', '-m', 'infra_artifact_ledger', *args],
                              cwd=self.root, env=environment, capture_output=True, timeout=20)

    def test_cli_uses_loaded_implementation_despite_ambient_import_paths(self):
        db = self.root / 'origin.sqlite'
        with initialize(db) as ledger:
            ledger.execute(wire(create()))
            expected = ledger.verify()
        before = db.read_bytes()
        poison = self.root / 'poison'
        package = poison / 'infra_artifact_ledger'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('raise RuntimeError("unselected implementation loaded")\n')
        for location, search in ((poison, 'inherited'), (self.root, 'absent'),
                                 (self.root, 'poison'), (poison, 'poison')):
            with self.subTest(poison_cwd=location == poison, pythonpath=search):
                with patch.dict(os.environ), chdir(location):
                    if search == 'absent':
                        os.environ.pop('PYTHONPATH', None)
                    elif search == 'poison':
                        os.environ['PYTHONPATH'] = str(poison)
                    response = self.invoke(['verify', '--db', str(db)])
                self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
                self.assertEqual(response.stderr, b'')
                self.assertEqual(json.loads(response.stdout),
                    dict(status='OK', commit_state='not_applicable', data=expected))
                self.assertEqual(db.read_bytes(), before)

    def test_invalid_input_names_are_not_io_failures(self):
        path_limit = os.pathconf(self.root, 'PC_PATH_MAX')
        for path in ('', 'nul\x00', '\ud800', self.overlong,
                     str(self.root) + '/x' * (path_limit // 2)):
            with self.subTest(length=len(path)):
                with self.assertRaises(LedgerError) as caught:
                    self.read(path)
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
                self.assertEqual(self.read(str(self.input)), b'abc')
        for size in (self.name_limit - 1, self.name_limit):
            path = self.root / ('x' * size)
            path.write_bytes(b'abc')
            self.assertEqual(self.read(str(path)), b'abc')

    def test_io_errors_keep_their_cause_without_closing_an_unowned_fd(self):
        for number in (errno.ENOENT, errno.EACCES, errno.EIO, errno.EMFILE):
            with self.subTest(errno=number):
                failure = OSError(number, 'controlled open failure')
                with patch.object(cli.os, 'open', side_effect=failure), patch.object(cli.os, 'close') as closing:
                    with self.assertRaises(LedgerError) as caught:
                        self.read(str(self.input))
                self.assertEqual(caught.exception.code, 'IO_ERROR')
                self.assertIs(caught.exception.__cause__, failure)
                closing.assert_not_called()
                self.assertEqual(self.read(str(self.input)), b'abc')
        with self.assertRaises(LedgerError) as caught:
            self.read(str(self.root / 'missing'))
        self.assertEqual(caught.exception.code, 'IO_ERROR')

    def test_each_cli_input_rejects_bad_path_then_accepts_original_request(self):
        db = self.root / 'target.sqlite'
        with initialize(db) as ledger:
            ledger.execute(wire(create()))
        source = self.root / 'source.sqlite'
        with initialize(source) as ledger:
            ledger.execute(wire(create('artifact:imported', 'source')))
            bundle = ledger.export_bundle()
        requests = {'create': create('artifact:new', 'new'),
                    'append': append(raw=b'payload'), 'import': import_request()}
        paths = {}
        for name, value in requests.items():
            paths[name] = self.root / (name + '.json')
            paths[name].write_bytes(wire(value))
        payload = self.root / 'payload'
        payload.write_bytes(b'payload')
        paths['mapping'] = self.root / 'mapping.json'
        paths['mapping'].write_bytes(wire([dict(blob_ref='blob:one', input_path=str(payload))]))
        for name, content in (('package', bundle['package_utf8']), ('descriptor', bundle['descriptor_utf8'])):
            paths[name] = self.root / (name + '.json')
            paths[name].write_bytes(content)
        paths['query'] = self.root / 'query.json'
        paths['query'].write_bytes(wire({key: requests['create'][key] for key in
            ('idempotency_scope_ref', 'operation_kind', 'idempotency_key')}))
        cases = [('create', '--request'), ('append', '--payload-map'),
                 ('import', '--package'), ('import', '--descriptor'), ('query', '--request')]
        for kind, option in cases:
            args = ['operation' if kind == 'query' else 'write', '--db', str(db), '--request', str(paths[kind])]
            if kind == 'append':
                args += ['--payload-map', str(paths['mapping'])]
            if kind == 'import':
                args += ['--package', str(paths['package']), '--descriptor', str(paths['descriptor'])]
            index = args.index(option) + 1
            before = db.read_bytes()
            with open_ledger(db) as ledger:
                baseline = ledger.export_bundle()
            for bad in ('', self.overlong):
                with self.subTest(kind=kind, option=option, empty=not bad):
                    rejected = self.invoke(args[:index] + [bad] + args[index + 1:])
                    result = json.loads(rejected.stdout)
                    self.assertEqual((rejected.returncode, result['code'], result['commit_state']),
                        (2, 'INVALID_INPUT', 'not_applicable' if kind == 'query' else 'not_committed'))
                    self.assertEqual(db.read_bytes(), before)
                    with open_ledger(db) as ledger:
                        self.assertEqual(ledger.export_bundle(), baseline)
            accepted = self.invoke(args)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            result = json.loads(accepted.stdout)
            self.assertEqual(result['status'], 'OK' if kind == 'query' else 'COMMITTED')
            replay = self.invoke(args)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(replay.stdout), result if kind == 'query' else dict(result, replayed=True))
        with open_ledger(db) as ledger:
            self.assertEqual(ledger.read_blob('blob:one'), b'payload')


if __name__ == '__main__':
    unittest.main()
