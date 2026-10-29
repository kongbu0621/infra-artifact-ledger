"""B opens/initializes/publishes without losing primary failures or handles."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger, storage


class BuildFault:
    def __init__(self, real, primary, cleanup):
        self.real, self.primary, self.cleanup = real, primary, cleanup
        self.closes = 0

    def __getattr__(self, name):
        return getattr(self.real, name)

    def execute(self, *args):
        cursor = self.real.execute(*args)
        cursor.close()
        raise self.primary

    def close(self):
        self.closes += 1
        self.real.close()
        raise self.cleanup


class StorageCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def original_cause(self, error, original):
        seen = set()
        while error is not original and error is not None and id(error) not in seen:
            seen.add(id(error))
            error = error.__cause__
        self.assertIs(error, original)

    def test_open_primary_survives_close_interruption(self):
        path = self.root / 'existing.sqlite'
        initialize(path).close()
        before = path.read_bytes()
        for cleanup_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(cleanup=cleanup_type.__name__):
                db = sqlite3.connect(path, isolation_level=None)
                primary = sqlite3.OperationalError('original open read failure')
                primary.sqlite_errorcode = sqlite3.SQLITE_BUSY
                fault = BuildFault(db, primary, cleanup_type('close'))
                try:
                    with patch.object(storage.sqlite3, 'connect', return_value=fault):
                        with self.assertRaises(BaseException) as caught:
                            open_ledger(path)
                    self.assertIsInstance(caught.exception, LedgerError)
                    self.assertEqual(caught.exception.code, 'BUSY')
                    self.original_cause(caught.exception, primary)
                    self.assertEqual(fault.closes, 1)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        db.execute('SELECT 1')
                finally:
                    db.close()
                self.assertEqual(path.read_bytes(), before)
                with open_ledger(path) as ledger:
                    self.assertEqual(ledger.verify()['counts']['idempotency_records'], 0)

    def test_initialization_build_error_survives_secondary_close_failure(self):
        real_connect = sqlite3.connect
        for primary_type in (sqlite3.OperationalError, KeyboardInterrupt, SystemExit):
            for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
                with self.subTest(primary=primary_type.__name__, cleanup=cleanup_type.__name__):
                    path = self.root / (primary_type.__name__ + cleanup_type.__name__ + '.sqlite')
                    primary = primary_type('original build failure')
                    captured = []
                    def connect(*args, **kwargs):
                        fault = BuildFault(real_connect(*args, **kwargs), primary, cleanup_type('close'))
                        captured.append(fault)
                        return fault
                    try:
                        with patch.object(storage.sqlite3, 'connect', side_effect=connect):
                            with self.assertRaises(BaseException) as caught:
                                initialize(path)
                        if isinstance(primary, Exception):
                            self.assertIsInstance(caught.exception, LedgerError)
                            self.assertEqual((caught.exception.code, caught.exception.details),
                                             ('IO_ERROR', {'target_published': False}))
                            self.original_cause(caught.exception, primary)
                        else:
                            self.assertIs(caught.exception, primary)
                        self.assertEqual(len(captured), 1)
                        self.assertEqual(captured[0].closes, 1)
                        self.assertFalse(path.exists())
                        self.assertFalse(list(self.root.glob('.ledger-b-*')))
                    finally:
                        for fault in captured:
                            fault.real.close()
                    initialize(path).close()

    def test_stage_cleanup_preserves_initialization_failure_before_and_after_publication(self):
        for published in (False, True):
            for primary_type in (OSError, KeyboardInterrupt, SystemExit):
                for cleanup_type in (KeyboardInterrupt, SystemExit):
                    with self.subTest(published=published, primary=primary_type.__name__, cleanup=cleanup_type.__name__):
                        path = self.root / f'{published}-{primary_type.__name__}-{cleanup_type.__name__}.sqlite'
                        primary = primary_type('original publication failure')
                        target = patch.object(storage, 'sync_directory', side_effect=primary) if published else \
                                 patch.object(storage.os, 'link', side_effect=primary)
                        with target, patch.object(storage.os, 'unlink', side_effect=cleanup_type('stage cleanup')):
                            with self.assertRaises(BaseException) as caught:
                                initialize(path)
                        if isinstance(primary, Exception):
                            self.assertIsInstance(caught.exception, LedgerError)
                            self.assertEqual((caught.exception.code, caught.exception.details),
                                             ('IO_ERROR', {'target_published': published}))
                            self.original_cause(caught.exception, primary)
                        else:
                            self.assertIs(caught.exception, primary)
                        self.assertEqual(path.exists(), published)
                        if published:
                            with open_ledger(path) as ledger:
                                self.assertEqual(ledger.verify()['counts']['idempotency_records'], 0)

    def test_initialized_handle_is_closed_if_stage_cleanup_prevents_return(self):
        real_connect = storage.connect
        for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
            with self.subTest(cleanup=cleanup_type.__name__):
                path = self.root / (cleanup_type.__name__ + '.sqlite')
                opened = []
                def capture(path):
                    db = real_connect(path)
                    opened.append(db)
                    return db
                error = cleanup_type('stage cleanup')
                try:
                    with patch.object(storage, 'connect', side_effect=capture), \
                         patch.object(storage.os, 'unlink', side_effect=error):
                        with self.assertRaises(BaseException) as caught:
                            initialize(path)
                    if isinstance(error, OSError):
                        self.assertIsInstance(caught.exception, LedgerError)
                        self.assertEqual(caught.exception.code, 'IO_ERROR')
                        self.original_cause(caught.exception, error)
                    else:
                        self.assertIs(caught.exception, error)
                    self.assertEqual(len(opened), 1)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        opened[0].execute('SELECT 1')
                finally:
                    for db in opened:
                        db.close()
                before = path.read_bytes()
                with open_ledger(path) as ledger:
                    self.assertEqual(ledger.verify()['counts']['idempotency_records'], 0)
                with self.assertRaises(LedgerError) as conflict:
                    initialize(path)
                self.assertEqual(conflict.exception.code, 'IDENTITY_CONFLICT')
                self.assertEqual(path.read_bytes(), before)

    def test_output_publication_error_survives_stage_cleanup_interruption(self):
        for published in (False, True):
            for cleanup_type in (KeyboardInterrupt, SystemExit):
                with self.subTest(published=published, cleanup=cleanup_type.__name__):
                    target = self.root / f'{published}-{cleanup_type.__name__}.bin'
                    primary = OSError('output publication failed')
                    failure = patch.object(storage, 'sync_directory', side_effect=primary) if published else \
                              patch.object(storage.os, 'link', side_effect=primary)
                    with failure, patch.object(storage.os, 'unlink', side_effect=cleanup_type('stage cleanup')):
                        with self.assertRaises(BaseException) as caught:
                            storage.publish(target, b'complete output')
                    self.assertIsInstance(caught.exception, LedgerError)
                    self.assertEqual(caught.exception.code, 'IO_ERROR')
                    self.original_cause(caught.exception, primary)
                    self.assertEqual(target.exists(), published)
                    if published:
                        self.assertEqual(target.read_bytes(), b'complete output')

    def test_output_cleanup_interruption_alone_is_reported(self):
        for cleanup_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(cleanup=cleanup_type.__name__):
                target = self.root / (cleanup_type.__name__ + '.bin')
                error = cleanup_type('stage cleanup only')
                with patch.object(storage.os, 'unlink', side_effect=error):
                    with self.assertRaises(BaseException) as caught:
                        storage.publish(target, b'complete output')
                self.assertIs(caught.exception, error)
                self.assertEqual(target.read_bytes(), b'complete output')

    def test_callers_handled_exception_does_not_hide_new_stage_failure(self):
        real_connect = storage.connect
        for operation in ('init', 'publish'):
            for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
                with self.subTest(operation=operation, cleanup=cleanup_type.__name__):
                    path = self.root / (operation + cleanup_type.__name__)
                    opened = []
                    def capture(path):
                        db = real_connect(path)
                        opened.append(db)
                        return db
                    error = cleanup_type('new stage cleanup failure')
                    try:
                        try:
                            raise ValueError('already handled by caller')
                        except ValueError:
                            with patch.object(storage, 'connect', side_effect=capture), \
                                 patch.object(storage.os, 'unlink', side_effect=error):
                                with self.assertRaises(BaseException) as caught:
                                    if operation == 'init':
                                        initialize(path)
                                    else:
                                        storage.publish(path, b'complete output')
                            if isinstance(error, OSError):
                                self.assertIsInstance(caught.exception, LedgerError)
                                self.original_cause(caught.exception, error)
                            else:
                                self.assertIs(caught.exception, error)
                        if operation == 'init':
                            self.assertEqual(len(opened), 1)
                            with self.assertRaises(sqlite3.ProgrammingError):
                                opened[0].execute('SELECT 1')
                        else:
                            self.assertEqual(path.read_bytes(), b'complete output')
                    finally:
                        for db in opened:
                            db.close()
