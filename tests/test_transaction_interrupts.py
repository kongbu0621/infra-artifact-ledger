"""Interruptions release owned transactions without replaying or discarding results."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from acceptance_helpers import ARTIFACT, SCOPE, append, create, encode, import_kwargs, import_request


class TransactionInterruptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'ledger.sqlite'
        self.ledger = initialize(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.ledger.execute(encode(create()))
        request, payloads = append('v1', b'original\x00\xff')
        self.ledger.execute(encode(request), payloads=payloads)
        self.baseline = self.path.read_bytes()
        self.bundle = self.ledger.export_bundle()

    def reads(self):
        return [lambda: self.ledger.get_record('artifact', ARTIFACT),
                lambda: self.ledger.get_history(ARTIFACT),
                lambda: self.ledger.get_operation(SCOPE, 'create_artifact', 'create-quarterly'),
                lambda: self.ledger.read_blob('blob:quarterly-v1'),
                self.ledger.verify, self.ledger.export_bundle]

    def assert_released(self):
        self.assertFalse(self.ledger._store.connection.in_transaction)
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as observer:
            observer.execute('BEGIN EXCLUSIVE')
            observer.execute('ROLLBACK')

    def assert_preserved(self):
        self.assert_released()
        self.assertEqual(self.path.read_bytes(), self.baseline)
        self.assertEqual(self.ledger.export_bundle(), self.bundle)
        self.assertEqual(self.ledger.read_blob('blob:quarterly-v1'), b'original\x00\xff')
        self.assertTrue(self.ledger.execute(encode(create()))['replayed'])
        with open_ledger(self.path) as reopened:
            self.assertEqual(reopened.export_bundle(), self.bundle)

    def test_begin_interrupts_release_read_and_write_transactions(self):
        calls = self.reads() + [lambda: self.ledger.execute(encode(create())),
                               lambda: self.ledger.execute(encode(create('artifact:new', key='new')))]
        begin = self.ledger._store.begin
        for index, call in enumerate(calls):
            for after in (False, True):
                for exception in (KeyboardInterrupt, SystemExit):
                    error = exception('controlled interruption')
                    def interrupted(*args, **kwargs):
                        if after:
                            begin(*args, **kwargs)
                        raise error
                    with self.subTest(call=index, after=after, exception=exception.__name__):
                        try:
                            with patch.object(self.ledger._store, 'begin', side_effect=interrupted):
                                with self.assertRaises(exception) as caught:
                                    call()
                            self.assertIs(caught.exception, error)
                            self.assert_preserved()
                        finally:
                            if self.ledger._store.connection.in_transaction:
                                self.ledger._store.rollback()

    def test_interrupted_read_releases_actual_read_lock(self):
        execute = self.ledger._store.execute
        error = KeyboardInterrupt('interrupted SELECT')
        def interrupted(sql, *args):
            result = execute(sql, *args)
            if sql.startswith('SELECT'):
                raise error
            return result
        for index, call in enumerate(self.reads()):
            with self.subTest(call=index):
                try:
                    with patch.object(self.ledger._store, 'execute', side_effect=interrupted):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            call()
                    self.assertIs(caught.exception, error)
                    self.assert_preserved()
                finally:
                    if self.ledger._store.connection.in_transaction:
                        self.ledger._store.rollback()

    def test_interrupted_new_write_rolls_back_partial_records(self):
        appended, payloads = append('v2', b'new', parents=['version:quarterly-v1'])
        calls = [(create('artifact:new', key='new'), {}), (appended, dict(payloads=payloads)),
                 (import_request(), import_kwargs(self.bundle))]
        insert = self.ledger._store.insert_operation
        error = KeyboardInterrupt('interrupted operation insert')
        def interrupted(*args):
            insert(*args)
            raise error
        for request, kwargs in calls:
            with self.subTest(kind=request['operation_kind']):
                try:
                    with patch.object(self.ledger._store, 'insert_operation', side_effect=interrupted):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            self.ledger.execute(encode(request), **kwargs)
                    self.assertIs(caught.exception, error)
                    self.assert_preserved()
                finally:
                    if self.ledger._store.connection.in_transaction:
                        self.ledger._store.rollback()

    def test_replay_interruption_preserves_original_result(self):
        error = KeyboardInterrupt('interrupted replay')
        with patch.object(self.ledger, '_check_replay', side_effect=error):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.ledger.execute(encode(create()))
        self.assertIs(caught.exception, error)
        self.assert_preserved()

    def test_commit_interruption_preserves_confirmed_database_state(self):
        commit = self.ledger._store.commit
        for after in (False, True):
            request = create('artifact:commit-' + str(after), key='commit-' + str(after))
            error = KeyboardInterrupt('interrupted COMMIT')
            def interrupted():
                if after:
                    commit()
                raise error
            with self.subTest(after=after):
                try:
                    with patch.object(self.ledger._store, 'commit', side_effect=interrupted):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            self.ledger.execute(encode(request))
                    self.assertIs(caught.exception, error)
                    self.assert_released()
                    if after:
                        self.assertEqual(self.ledger.get_operation(SCOPE, 'create_artifact', request['idempotency_key'])
                                         ['result']['artifact_id'], request['body']['artifact']['artifact_id'])
                    else:
                        self.assertEqual(self.path.read_bytes(), self.baseline)
                    result = self.ledger.execute(encode(request))
                    self.assertIs(result['replayed'], after)
                    self.baseline = self.path.read_bytes()
                    self.bundle = self.ledger.export_bundle()
                    self.assert_preserved()
                finally:
                    if self.ledger._store.connection.in_transaction:
                        self.ledger._store.rollback()

    def test_failed_interrupt_cleanup_preserves_primary_then_close_recovers(self):
        for write in (False, True):
            for cleanup_error in (OSError('rollback unavailable'), KeyboardInterrupt('second interruption')):
                begin = self.ledger._store.begin
                error = KeyboardInterrupt('original interruption')
                def interrupted(*args, **kwargs):
                    begin(*args, **kwargs)
                    raise error
                with self.subTest(write=write, cleanup=type(cleanup_error).__name__):
                    with patch.object(self.ledger._store, 'begin', side_effect=interrupted), \
                         patch.object(self.ledger._store, 'rollback', side_effect=cleanup_error):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            if write:
                                self.ledger.execute(encode(create()))
                            else:
                                self.ledger.get_history(ARTIFACT)
                    self.assertIs(caught.exception, error)
                    self.assertTrue(getattr(error, '__notes__', []))
                self.ledger.close()
                self.ledger = open_ledger(self.path)
                self.assert_preserved()

    def test_rejected_nested_call_does_not_rollback_preexisting_transaction(self):
        for call in self.reads() + [lambda: self.ledger.execute(encode(create()))]:
            with self.subTest(call=call):
                self.ledger._store.begin(write=True)
                try:
                    with patch.object(self.ledger._store, 'rollback', wraps=self.ledger._store.rollback) as rollback:
                        with self.assertRaises(LedgerError):
                            call()
                        rollback.assert_not_called()
                    self.assertTrue(self.ledger._store.connection.in_transaction)
                finally:
                    if self.ledger._store.connection.in_transaction:
                        self.ledger._store.rollback()
                self.assert_preserved()
