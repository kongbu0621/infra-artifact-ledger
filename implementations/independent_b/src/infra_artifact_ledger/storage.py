"""Independent B's physical format. This is deliberately not A's SQLite schema."""
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from urllib.parse import quote
from . import contract as c

APPLICATION_ID = 0x414C4231  # ALB1; private storage identity, never portable.
SCHEMA = '''
CREATE TABLE b_objects (
    owned_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    document BLOB NOT NULL,
    content BLOB
);
CREATE TABLE b_operations (
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    request_key TEXT NOT NULL,
    document BLOB NOT NULL,
    PRIMARY KEY (scope, kind, request_key)
);
CREATE TABLE b_format (name TEXT PRIMARY KEY NOT NULL);
INSERT INTO b_format VALUES ('independent-b-v1');
'''


def path_value(path):
    try:
        result = Path(path).absolute()
        c.require('\x00' not in str(result), 'NUL in path')
        return result
    except (TypeError, ValueError) as exc:
        raise c.LedgerError('INVALID_INPUT', 'invalid database path') from exc


def connect(path):
    # Inspect the fixed header before SQLite can recover a foreign hot journal.
    # A clean-file application_id check after connecting would be too late.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        header = source.read(100)
    c.require(len(header) == 100 and header[:16] == b'SQLite format 3\x00'
              and int.from_bytes(header[68:72], 'big') == APPLICATION_ID
              and int.from_bytes(header[60:64], 'big') == 1,
              'not an independent B database; use portable import', 'UNSUPPORTED_VERSION')
    db = sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=rw',
                         uri=True, isolation_level=None, timeout=0)
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA trusted_schema=OFF')
        c.require(db.execute('PRAGMA application_id').fetchone()[0] == APPLICATION_ID
                  and db.execute('PRAGMA user_version').fetchone()[0] == 1,
                  'not an independent B database; use portable import', 'UNSUPPORTED_VERSION')
        c.require(db.execute('SELECT name FROM b_format').fetchall() == [('independent-b-v1',)],
                  'B format marker', 'UNSUPPORTED_VERSION')
        return db
    except BaseException:
        db.close()
        raise


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def initialize_connection(path):
    path = path_value(path)
    for candidate in (path, *(Path(str(path) + tail) for tail in ('-journal', '-wal', '-shm'))):
        c.require(not os.path.lexists(candidate), 'database target exists', 'IDENTITY_CONFLICT')
    stage = None
    published = False
    try:
        fd, stage = tempfile.mkstemp(prefix='.ledger-b-', dir=path.parent)
        os.close(fd)
        db = sqlite3.connect(stage, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('BEGIN IMMEDIATE;' + SCHEMA
                + f'PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version=1; COMMIT;')
        finally:
            db.close()
        with open(stage, 'rb') as source:
            os.fsync(source.fileno())
        # Hard-link publication is exclusive, including dangling symlinks.
        os.link(stage, path)
        published = True
        sync_directory(path.parent)
        return connect(path)
    except FileExistsError as exc:
        raise c.LedgerError('IDENTITY_CONFLICT', 'database target exists') from exc
    except (OSError, sqlite3.Error) as exc:
        raise c.LedgerError('IO_ERROR', 'database initialization failed',
                           details={'target_published': published}) from exc
    finally:
        if stage is not None:
            _remove_stage(stage)


def open_connection(path):
    path = path_value(path)
    try:
        info = path.lstat()
        c.require(stat.S_ISREG(info.st_mode), 'database must be a regular file')
        return connect(path)
    except FileNotFoundError as exc:
        raise c.LedgerError('NOT_FOUND', 'database does not exist') from exc
    except sqlite3.DatabaseError as exc:
        if (getattr(exc, 'sqlite_errorcode', 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise c.LedgerError('BUSY', 'database open is locked') from exc
        raise c.LedgerError('INTEGRITY_FAILURE', 'unreadable B database') from exc
    except OSError as exc:
        raise c.LedgerError('IO_ERROR', 'database open failed') from exc


def publish(path, raw):
    """Publish one complete new file. A two-file export is not an atomic pair."""
    target = path_value(path)
    stage = None
    try:
        fd, stage = tempfile.mkstemp(prefix='.ledger-b-output-', dir=target.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(stage, target)
        sync_directory(target.parent)
    except FileExistsError as exc:
        raise c.LedgerError('IDENTITY_CONFLICT', 'output target exists') from exc
    except OSError as exc:
        raise c.LedgerError('IO_ERROR', 'output publication failed') from exc
    finally:
        if stage is not None:
            _remove_stage(stage)


def _remove_stage(path):
    active_exception = sys.exc_info()[0] is not None
    try:
        os.unlink(path)
    except OSError as exc:
        if not active_exception:
            raise c.LedgerError('IO_ERROR', 'temporary file cleanup failed') from exc
