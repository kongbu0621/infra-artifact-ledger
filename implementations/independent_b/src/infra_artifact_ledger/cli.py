"""Thin file/argument adapter; all write semantics go through execute()."""
import argparse
import errno
import os
import stat
import sys
from . import contract as c
from . import initialize, open as open_ledger
from .storage import _closing, path_value, publish

EXITS = {**dict.fromkeys(('INVALID_INPUT', 'UNSUPPORTED_VERSION', 'UNSUPPORTED_PROFILE', 'RESOURCE_LIMIT'), 2),
         'NOT_FOUND': 3, 'IDENTITY_CONFLICT': 4, 'IDEMPOTENCY_CONFLICT': 4,
         'INTEGRITY_FAILURE': 5, 'BUSY': 6, 'DURABILITY_UNKNOWN': 7}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise c.LedgerError('INVALID_INPUT', message)


def read_file(path, maximum):
    try:
        c.require(bool(os.fspath(path)), 'input path must be nonempty')
        # Nonblocking open permits fstat to reject FIFOs without waiting for a
        # writer. Freeze bytes from this same regular-file descriptor.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            c.require(stat.S_ISREG(info.st_mode), 'input must be a regular file')
            c.bounded(info.st_size, maximum, 'input file bytes')
            with _closing(os.fdopen(fd, 'rb', closefd=False)) as stream:
                raw = stream.read(maximum + 1)
                c.bounded(len(raw), maximum, 'input file bytes')
        except BaseException as primary:
            try:
                os.close(fd)
            except BaseException as cleanup:
                primary.add_note('Input descriptor close also failed: ' + type(cleanup).__name__)
            raise
        else:
            os.close(fd)
        return raw
    except ValueError as exc:
        raise c.LedgerError('INVALID_INPUT', 'invalid input path') from exc
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            raise c.LedgerError('INVALID_INPUT', 'invalid input path') from exc
        raise c.LedgerError('IO_ERROR', 'input file read failed') from exc


def parser():
    result = Parser(prog='artifact-ledger')
    commands = result.add_subparsers(dest='command', required=True, parser_class=Parser)
    for name in ('init', 'write', 'get', 'history', 'operation', 'read-blob', 'verify', 'export'):
        command = commands.add_parser(name)
        command.add_argument('--db', required=True)
        if name in ('write', 'operation'):
            command.add_argument('--request', required=True)
        if name in ('write', 'export'):
            command.add_argument('--package', required=name == 'export')
            command.add_argument('--descriptor', required=name == 'export')
        if name == 'write':
            command.add_argument('--payload-map')
        if name == 'get':
            command.add_argument('--kind', required=True)
            command.add_argument('--id', required=True)
        if name == 'history':
            command.add_argument('--artifact-id', required=True)
        if name == 'read-blob':
            command.add_argument('--blob-ref', required=True)
            command.add_argument('--output', required=True)
    return result


def run(args):
    if args.command in ('read-blob', 'export'):
        # Check every destination before opening or publishing anything. Even
        # absent SQLite sidecars are reserved by the active database.
        destinations = (args.output,) if args.command == 'read-blob' else (args.package, args.descriptor)
        try:
            database = path_value(args.db).resolve()
            protected = {str(database) + suffix for suffix in ('', '-journal', '-wal', '-shm')}
            for destination in destinations:
                c.require(str(path_value(destination).resolve()) not in protected,
                          'output overlaps database or SQLite sidecar')
        except (OSError, RuntimeError) as exc:
            raise c.LedgerError('INVALID_INPUT', 'cannot resolve output path') from exc
    ledger = initialize(args.db) if args.command == 'init' else open_ledger(args.db)
    result, write_identity = None, {}
    primary = None
    try:
        result = _run(args, ledger, write_identity)
        return result
    except BaseException as exc:
        primary = exc
        raise
    finally:
        # Track this call's failure, not an exception handled by its caller.
        try:
            ledger.close()
        except BaseException as exc:
            if primary is not None:
                primary.add_note('Ledger close also failed: ' + type(exc).__name__)
            else:
                if isinstance(exc, c.LedgerError) and result is not None and result.get('status') == 'COMMITTED':
                    exc.commit_state = 'committed'
                    exc.details = dict(write_identity, result_ref=result['result_ref'],
                                       recorded_at=result['recorded_at'])
                raise


def _run(args, ledger, write_identity):
    command = args.command
    if command == 'init':
        data = dict(profile=c.PROFILE, storage_schema_version=1)
    elif command == 'write':
        raw = read_file(args.request, c.DOCUMENT)
        request = c.parse(raw)
        c.require(type(request) is dict, 'request object')
        kind = request.get('operation_kind')
        c.require(kind in ('create_artifact', 'append_version', 'import_bundle'), 'operation kind')
        c.require((args.payload_map is not None) == (kind == 'append_version')
                  and (args.package is not None) == (kind == 'import_bundle')
                  and (args.descriptor is not None) == (kind == 'import_bundle'), 'write arguments')
        payloads = None
        if args.payload_map is not None:
            entries = c.parse(read_file(args.payload_map, c.DOCUMENT))
            c.require(type(entries) is list, 'payload map array')
            paths = {}
            for entry in entries:
                c.fields(entry, ('blob_ref', 'input_path'))
                c.stable(entry['blob_ref'])
                c.string(entry['input_path'])
                c.require('\x00' not in entry['input_path'], 'NUL input path')
                c.require(entry['blob_ref'] not in paths, 'duplicate payload map identity')
                paths[entry['blob_ref']] = entry['input_path']
            payloads, total = {}, 0
            for identity, path in paths.items():
                payloads[identity] = read_file(path, min(c.BLOB, c.PAYLOAD - total))
                total += len(payloads[identity])
        write_identity.update({name: request.get(name) for name in
            ('idempotency_scope_ref', 'operation_kind', 'idempotency_key')})
        return ledger.execute(raw, payloads=payloads,
            package=read_file(args.package, c.PACKAGE) if args.package is not None else None,
            descriptor=read_file(args.descriptor, 4096) if args.descriptor is not None else None)
    elif command == 'get':
        data = ledger.get_record(args.kind, args.id)
    elif command == 'history':
        data = ledger.get_history(args.artifact_id)
    elif command == 'operation':
        query = c.parse(read_file(args.request, c.DOCUMENT))
        c.fields(query, ('idempotency_scope_ref', 'operation_kind', 'idempotency_key'))
        data = ledger.get_operation(*c.operation_identity(query))
    elif command == 'read-blob':
        raw = ledger.read_blob(args.blob_ref)
        data = dict(blob_ref=args.blob_ref, byte_length=len(raw), digest=c.digest(raw))
        publish(args.output, raw)
    elif command == 'verify':
        data = ledger.verify()
    else:
        bundle = ledger.export_bundle()
        publish(args.package, bundle['package_utf8'])
        publish(args.descriptor, bundle['descriptor_utf8'])
        data = dict(descriptor=c.parse(bundle['descriptor_utf8'], 4096))
    c.success_data(data)
    return dict(status='OK', commit_state='not_applicable', data=data)


def main(argv=None):
    args = None
    try:
        args = parser().parse_args(argv)
        response = run(args)
        status = 0
    except c.LedgerError as exc:
        tokens = sys.argv[1:] if argv is None else argv
        writing = (args is not None and args.command == 'write') or (bool(tokens) and tokens[0] == 'write')
        if writing and exc.commit_state == 'not_applicable':
            exc.commit_state = 'not_committed'
        response, status = exc.envelope(), EXITS.get(exc.code, 1)
    raw = c.encode(response) + b'\n'
    # os.write can short-write. Never re-run the operation to replace a lost response.
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(sys.stdout.fileno(), raw[offset:])
            if count <= 0:
                raise OSError('zero-length response write')
            offset += count
    except OSError:
        return 1
    return status
