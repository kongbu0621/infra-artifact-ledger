"""A/B cleanup interruption contract on disposable, real SQLite databases.

Private adapters inject failures at statement/close boundaries. Public results,
original request replay, independent locks and durable bytes are the oracles.
This does not model power loss or assert that a lost COMMIT response is success.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from cross_implementation import assert_metadata, portable_metadata
from test_public_contract import append, create, import_request, wire


class ConnectionFault:
    def __init__(self, real, statement=None, primary=None, cleanup=None,
                 cleanup_after=False, close_error=None):
        self.real, self.statement, self.primary = real, statement, primary
        self.cleanup, self.cleanup_after, self.close_error = cleanup, cleanup_after, close_error
        self.hit = self.cleanup_hits = 0

    def __getattr__(self, name):
        return getattr(self.real, name)

    def execute(self, sql, *args, **kwargs):
        if sql == 'ROLLBACK' and self.cleanup is not None:
            self.cleanup_hits += 1
            if self.cleanup_after:
                self.real.execute(sql, *args, **kwargs)
            raise self.cleanup
        result = self.real.execute(sql, *args, **kwargs)
        if self.statement and sql.startswith(self.statement) and not self.hit:
            self.hit += 1
            # Complete/finalize the injected statement: a cursor retained by
            # this test's traceback must not manufacture a persistent lock.
            result.close()
            raise self.primary
        return result

    def close(self):
        if self.close_error is None or self.cleanup_after:
            self.real.close()
        if self.close_error is not None:
            raise self.close_error


class CleanupInterruptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'ledger.sqlite'
        self.ledger = initialize(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.ledger.execute(wire(create()))
        self.original = self.ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
        self.before = self.path.read_bytes()
        self.expected = portable_metadata(self.ledger.export_bundle())
        self.bundle = self.ledger.export_bundle()
        self.operation = self.ledger.get_operation('demo:scope', 'append_version', 'append')

    @contextmanager
    def injected(self, **kwargs):
        owner, field = (self.ledger._store, 'connection') if hasattr(self.ledger, '_store') else (self.ledger, '_db')
        fault = ConnectionFault(getattr(owner, field), **kwargs)
        with patch.object(owner, field, fault):
            yield fault

    def capture(self, call):
        try:
            call()
        except BaseException as error:
            return error
        self.fail('injected failure did not reach the caller')

    def assert_original_cause(self, error, original):
        seen = set()
        while error is not original and error is not None and id(error) not in seen:
            seen.add(id(error))
            error = error.__cause__
        self.assertIs(error, original)

    def reads(self):
        return [lambda: self.ledger.get_record('artifact', 'artifact:report'),
                lambda: self.ledger.get_history('artifact:report'),
                lambda: self.ledger.get_operation('demo:scope', 'append_version', 'append'),
                lambda: self.ledger.read_blob('blob:one'), self.ledger.verify, self.ledger.export_bundle]

    def assert_released(self):
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as observer:
            observer.execute('BEGIN EXCLUSIVE')
            observer.execute('ROLLBACK')

    def reopen_preserved(self):
        self.ledger.close()
        self.ledger = open_ledger(self.path)
        self.assert_released()
        self.assertEqual(self.path.read_bytes(), self.before)
        assert_metadata(self.ledger, self.expected)
        self.assertEqual(self.ledger.execute(wire(append()), payloads={}), dict(self.original, replayed=True))
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_read_error_survives_cleanup_interruption(self):
        for index in range(6):
            for primary_type in (OSError, KeyboardInterrupt, SystemExit):
                for after in (False, True):
                    for cleanup_type in (KeyboardInterrupt, SystemExit):
                        with self.subTest(read=index, primary=primary_type.__name__, after=after,
                                          cleanup=cleanup_type.__name__):
                            primary, cleanup = primary_type('original read'), cleanup_type('cleanup')
                            try:
                                with self.injected(statement='SELECT', primary=primary, cleanup=cleanup,
                                                   cleanup_after=after) as fault:
                                    caught = self.capture(self.reads()[index])
                                self.assertEqual(fault.hit, 1)
                                self.assertEqual(fault.cleanup_hits, 1)
                                if isinstance(primary, Exception):
                                    self.assertIsInstance(caught, LedgerError)
                                    self.assertEqual((caught.code, caught.commit_state), ('IO_ERROR', 'not_applicable'))
                                    self.assert_original_cause(caught, primary)
                                else:
                                    self.assertIs(caught, primary)
                            finally:
                                self.reopen_preserved()

    def test_successful_read_preserves_cleanup_interruption(self):
        for index in range(6):
            for after in (False, True):
                with self.subTest(read=index, after=after):
                    error = KeyboardInterrupt('read cleanup')
                    try:
                        with self.injected(cleanup=error, cleanup_after=after):
                            caught = self.capture(self.reads()[index])
                        self.assertIs(caught, error)
                    finally:
                        self.reopen_preserved()

    def test_new_write_cleanup_failure_preserves_interruption_or_unknown(self):
        calls = [(create('artifact:new', key='new'), {}),
                 (append('version:two', 'blob:two', 'root:two', b'next', ['version:one'], 'two'),
                  {'payloads': {'blob:two': b'next'}}),
                 (import_request(), dict(package=self.bundle['package_utf8'], descriptor=self.bundle['descriptor_utf8']))]
        for request, kwargs in calls:
            for primary_type in (OSError, KeyboardInterrupt, SystemExit):
                for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
                    for after in (False, True):
                        with self.subTest(kind=request['operation_kind'], primary=primary_type.__name__,
                                          cleanup=cleanup_type.__name__, after=after):
                            primary, cleanup = primary_type('original write'), cleanup_type('cleanup')
                            try:
                                with self.injected(statement='INSERT', primary=primary, cleanup=cleanup,
                                                   cleanup_after=after) as fault:
                                    caught = self.capture(lambda: self.ledger.execute(wire(request), **kwargs))
                                self.assertEqual(fault.hit, 1)
                                self.assertEqual(fault.cleanup_hits, 1)
                                if isinstance(primary, Exception):
                                    self.assertIsInstance(caught, LedgerError)
                                    self.assertEqual((caught.code, caught.commit_state), ('DURABILITY_UNKNOWN', 'unknown'))
                                    self.assertIs(caught.__cause__, cleanup)
                                    self.assertIs(cleanup.__context__, primary)
                                else:
                                    self.assertIs(caught, primary)
                            finally:
                                self.reopen_preserved()

    def test_replay_error_cleanup_preserves_original_commit(self):
        for primary_type in (OSError, KeyboardInterrupt, SystemExit):
            for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
                for after in (False, True):
                    with self.subTest(primary=primary_type.__name__, cleanup=cleanup_type.__name__, after=after):
                        primary, cleanup = primary_type('replay read'), cleanup_type('cleanup')
                        try:
                            with patch.object(self.ledger, '_blob', side_effect=primary), \
                                 self.injected(cleanup=cleanup, cleanup_after=after) as fault:
                                caught = self.capture(lambda: self.ledger.execute(wire(append()), payloads={}))
                            self.assertEqual(fault.cleanup_hits, 1)
                            if isinstance(primary, Exception):
                                self.assertIsInstance(caught, LedgerError)
                                self.assertEqual((caught.code, caught.commit_state), ('IO_ERROR', 'committed'))
                                self.assert_original_cause(caught, primary)
                                op = self.operation['idempotency_record']
                                self.assertEqual(caught.details, {key: op[key] for key in
                                    ('result_ref', 'idempotency_scope_ref', 'operation_kind', 'idempotency_key', 'recorded_at')})
                            else:
                                self.assertIs(caught, primary)
                        finally:
                            self.reopen_preserved()

    def test_commit_acknowledgement_interruption_is_not_replaced(self):
        for exception in (KeyboardInterrupt, SystemExit):
            request = create('artifact:commit-' + exception.__name__, key=exception.__name__)
            error = exception('COMMIT returned but acknowledgement interrupted')
            with self.subTest(exception=exception.__name__):
                with self.injected(statement='COMMIT', primary=error) as fault:
                    caught = self.capture(lambda: self.ledger.execute(wire(request)))
                self.assertEqual(fault.hit, 1)
                self.assertIs(caught, error)
                self.assert_released()
                with open_ledger(self.path) as reopened:
                    operation = reopened.get_operation('demo:scope', 'create_artifact', request['idempotency_key'])
                    self.assertEqual(operation['result']['artifact_id'], request['body']['artifact']['artifact_id'])
                    self.assertTrue(reopened.execute(wire(request))['replayed'])
                self.before = self.path.read_bytes()
                self.expected = portable_metadata(self.ledger.export_bundle())
                self.reopen_preserved()

    def test_context_cleanup_preserves_primary_and_confirmed_identity(self):
        for primary_type in (OSError, KeyboardInterrupt, SystemExit, LedgerError):
            for cleanup_type in (KeyboardInterrupt, SystemExit):
                for after in (False, True):
                    with self.subTest(primary=primary_type.__name__, cleanup=cleanup_type.__name__, after=after):
                        # Produce a real known-committed error through public replay.
                        if primary_type is LedgerError:
                            with patch.object(self.ledger, '_blob', side_effect=OSError('replay read')):
                                primary = self.capture(lambda: self.ledger.execute(wire(append()), payloads={}))
                            self.assertIsInstance(primary, LedgerError)
                            self.assertEqual(primary.commit_state, 'committed')
                            details = dict(primary.details)
                        else:
                            primary = primary_type('original context failure')
                        def body():
                            with self.ledger:
                                raise primary
                        try:
                            with self.injected(close_error=cleanup_type('close interrupted'), cleanup_after=after):
                                caught = self.capture(body)
                            self.assertIs(caught, primary)
                            if primary_type is LedgerError:
                                self.assertEqual(caught.details, details)
                                self.assertEqual(caught.commit_state, 'committed')
                        finally:
                            self.reopen_preserved()

    def test_context_without_primary_keeps_close_interruption(self):
        for cleanup_type in (KeyboardInterrupt, SystemExit):
            for after in (False, True):
                with self.subTest(cleanup=cleanup_type.__name__, after=after):
                    error = cleanup_type('standalone close interruption')
                    def body():
                        with self.ledger:
                            pass
                    try:
                        with self.injected(close_error=error, cleanup_after=after):
                            caught = self.capture(body)
                        self.assertIs(caught, error)
                    finally:
                        self.reopen_preserved()
