"""CLI cleanup must preserve the public failure and original operation identity."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import infra_artifact_ledger as public
from infra_artifact_ledger import cli, initialize, open as open_ledger, LedgerError
from cross_implementation import assert_metadata, portable_metadata
from test_cleanup_interrupts import ConnectionFault
from test_public_contract import append, create, wire


class CLICleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'ledger.sqlite'
        with initialize(self.path) as ledger:
            ledger.execute(wire(create()))
            self.result = ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
            self.expected = portable_metadata(ledger.export_bundle())
            op = ledger.get_operation('demo:scope', 'append_version', 'append')['idempotency_record']
            self.identity = {key: op[key] for key in ('result_ref', 'idempotency_scope_ref',
                'operation_kind', 'idempotency_key', 'recorded_at')}
        self.before = self.path.read_bytes()
        self.request = self.root / 'request.json'
        self.request.write_bytes(wire(append()))
        self.mapping = self.root / 'payloads.json'
        self.mapping.write_bytes(b'[]')

    def arguments(self, command):
        result = [command, '--db', str(self.path)]
        if command == 'write':
            result += ['--request', str(self.request), '--payload-map', str(self.mapping)]
        return result

    def dispatch(self, command):
        parser = cli._parser() if hasattr(cli, '_parser') else cli.parser()
        dispatch = cli._dispatch if hasattr(cli, '_dispatch') else cli.run
        return dispatch(parser.parse_args(self.arguments(command)))

    def preserved(self):
        self.assertEqual(self.path.read_bytes(), self.before)
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as observer:
            observer.execute('BEGIN EXCLUSIVE')
            observer.execute('ROLLBACK')
        with open_ledger(self.path) as ledger:
            assert_metadata(ledger, self.expected)
            self.assertEqual(ledger.execute(wire(append()), payloads={}), dict(self.result, replayed=True))
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_replay_failure_keeps_committed_identity_during_close_interruption(self):
        for cleanup_type in (KeyboardInterrupt, SystemExit):
            for after in (False, True):
                with self.subTest(cleanup=cleanup_type.__name__, after=after):
                    ledger = open_ledger(self.path)
                    close = ledger.close
                    primary = OSError('original replay read failure')
                    def interrupted_close():
                        if after:
                            close()
                        raise cleanup_type('secondary close failure')
                    try:
                        with patch.object(cli, 'open_ledger', return_value=ledger), \
                             patch.object(ledger, '_blob', side_effect=primary), \
                             patch.object(ledger, 'close', side_effect=interrupted_close):
                            with self.assertRaises(BaseException) as caught:
                                self.dispatch('write')
                        error = caught.exception
                        self.assertIsInstance(error, LedgerError)
                        self.assertEqual((error.code, error.commit_state), ('IO_ERROR', 'committed'))
                        self.assertEqual(error.details, self.identity)
                        self.assertIs(error.__cause__, primary)
                    finally:
                        close()
                        self.preserved()

    def test_primary_interruption_is_not_replaced_by_close_interruption(self):
        for primary_type in (KeyboardInterrupt, SystemExit):
            for cleanup_type in (KeyboardInterrupt, SystemExit):
                with self.subTest(primary=primary_type.__name__, cleanup=cleanup_type.__name__):
                    ledger = open_ledger(self.path)
                    close = ledger.close
                    owner, field = (ledger._store, 'connection') if hasattr(ledger, '_store') else (ledger, '_db')
                    primary = primary_type('original SELECT interruption')
                    fault = ConnectionFault(getattr(owner, field), statement='SELECT', primary=primary,
                                            close_error=cleanup_type('secondary close'), cleanup_after=True)
                    try:
                        with patch.object(cli, 'open_ledger', return_value=ledger), patch.object(owner, field, fault):
                            with self.assertRaises(BaseException) as caught:
                                self.dispatch('verify')
                        self.assertEqual(fault.hit, 1)
                        self.assertIs(caught.exception, primary)
                    finally:
                        close()
                        self.preserved()

    def test_close_interruption_alone_is_not_reported_as_success(self):
        for command in ('verify', 'write'):
            for cleanup_type in (KeyboardInterrupt, SystemExit):
                with self.subTest(command=command, cleanup=cleanup_type.__name__):
                    ledger = open_ledger(self.path)
                    close = ledger.close
                    cleanup = cleanup_type('standalone close')
                    def interrupted_close():
                        close()
                        raise cleanup
                    try:
                        with patch.object(cli, 'open_ledger', return_value=ledger), \
                             patch.object(ledger, 'close', side_effect=interrupted_close):
                            with self.assertRaises(BaseException) as caught:
                                self.dispatch(command)
                        self.assertIs(caught.exception, cleanup)
                    finally:
                        close()
                        self.preserved()

    def test_real_cli_process_keeps_error_envelope_and_commit_identity(self):
        code = '''import pathlib,sys
from unittest.mock import patch
import infra_artifact_ledger as public
from infra_artifact_ledger import cli
assert pathlib.Path(public.__file__).resolve() == pathlib.Path(sys.argv[1]).resolve()
real_open = cli.open_ledger
def open_with_fault(path):
    ledger = real_open(path)
    real_close = ledger.close
    def blob(*args, **kwargs):
        raise OSError('original replay error')
    def close():
        real_close()
        raise KeyboardInterrupt('secondary cleanup')
    ledger._blob, ledger.close = blob, close
    return ledger
with patch.object(cli, 'open_ledger', side_effect=open_with_fault):
    raise SystemExit(cli.main(sys.argv[2:]))
'''
        result = subprocess.run([sys.executable, *(['-I'] if sys.flags.isolated else []), '-B', '-c', code, public.__file__,
                                 *self.arguments('write')], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, b'')
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope['code'], 'IO_ERROR')
        self.assertEqual(envelope['commit_state'], 'committed')
        self.assertEqual(envelope['details'], self.identity)
        self.preserved()

    def test_callers_handled_exception_does_not_hide_new_close_failure(self):
        for cleanup in (KeyboardInterrupt('new close'), SystemExit('new close'),
                        LedgerError('IO_ERROR', 'new close')):
            with self.subTest(cleanup=type(cleanup).__name__):
                ledger = open_ledger(self.path)
                close = ledger.close
                def interrupted_close():
                    close()
                    raise cleanup
                try:
                    try:
                        raise ValueError('already handled by caller')
                    except ValueError:
                        with patch.object(cli, 'open_ledger', return_value=ledger), \
                             patch.object(ledger, 'close', side_effect=interrupted_close):
                            with self.assertRaises(BaseException) as caught:
                                self.dispatch('verify')
                        if isinstance(cleanup, LedgerError):
                            self.assertIsInstance(caught.exception, LedgerError)
                            self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                             ('IO_ERROR', 'not_applicable'))
                        else:
                            self.assertIs(caught.exception, cleanup)
                finally:
                    close()
                    self.preserved()
