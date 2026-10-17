"""Read-only audit of the private, post-exercise NAS evidence archive.

Run: python3 audit.py /path/to/private/archive
The input databases were copied only after the NAS-only restore completed.
This checker neither connects to NAS nor replays Ledger operations.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(root, name):
    return json.loads((root / name).read_text(encoding='utf-8'))


def database(path):
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        require(connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'SQLite integrity')
        require(not connection.execute('PRAGMA foreign_key_check').fetchall(), 'SQLite references')
        tables = {}
        for table, order in [('ledger_format', 'version,profile'), ('records', 'id'),
                             ('refs', 'source_id,field,target_id'),
                             ('operations', 'scope,kind,key'), ('payloads', 'blob_ref')]:
            tables[table] = connection.execute(f'SELECT * FROM {table} ORDER BY {order}').fetchall()
        return tables
    finally:
        connection.close()


def audit(root):
    index = read_json(root, 'readback-index.json')
    names = [item['name'] for item in index['files']]
    required_names = {
        'run/' + name for name in ('storage-config.json', 'OWNED.json', 'expected.json',
                                  'report.json', 'disposable-ownership.json',
                                  'snapshot-reference.json', 'restored/ledger.sqlite')
    } | {
        'preparation/' + name for name in ('prepare_nas_mount.py', 'mount-started.json',
            'mount-observation.json', 'mount-result.json', 'storage-config.json',
            'exercise-started.json', 'a2-nas-console.log', 'a2-nas-command-status.json')
    } | {'nas-generation/' + name for name in ('ledger.sqlite', 'manifest.json', 'COMMITTED.json')}
    require(len(names) == len(set(names)) and set(names) == required_names, 'Evidence membership')
    for entry in index['files']:
        content = (root / entry['name']).read_bytes()
        require(len(content) == entry['size'] and digest(content) == entry['sha256'], 'Evidence bytes')
    report = read_json(root, 'run/report.json')
    expected = read_json(root, 'run/expected.json')
    reference = read_json(root, 'run/snapshot-reference.json')
    manifest_bytes = (root / 'nas-generation/manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    marker = read_json(root, 'nas-generation/COMMITTED.json')
    command = read_json(root, 'preparation/a2-nas-command-status.json')
    console = read_json(root, 'preparation/a2-nas-console.log')
    mount = read_json(root, 'preparation/mount-result.json')
    require(command['shell_command_status'] == 0 and console['status'] == report['status'] == 'PASS', 'Execution result')
    require(console['run_id'] == report['run_id'] and console['scope'] == 'synthetic_nas_roundtrip', 'Run identity')
    require(index['disposable_absent'] and all(report['stages']['synthetic_local_loss'].values()), 'Local loss evidence')
    config = read_json(root, 'run/storage-config.json')
    current_mount = index['current_mount']['filesystems']
    require(len(current_mount) == 1 and current_mount[0] == mount['observed_mount'], 'Mount unchanged at readback')
    for key, observed in [('mount_point', 'target'), ('mount_source', 'source'),
                          ('mount_root', 'fsroot'), ('fs_type', 'fstype')]:
        require(config[key] == current_mount[0][observed], 'Configured mount identity')
    for key, observed in [('mount_point', 'point'), ('mount_source', 'source'), ('mount_root', 'root')]:
        require(config[key] == report['endpoints']['nas'][observed], 'Exercise mount identity')
    require(config['fs_type'] == report['endpoints']['nas']['fs_type'] == 'cifs', 'Real CIFS binding')
    require(report['endpoints']['local']['fs_type'] == 'ext4', 'Local storage')
    require('vers=3.1.1' in current_mount[0]['options'].split(','), 'SMB dialect')
    require(manifest['snapshot_id'] == marker['snapshot_id'] == reference['snapshot_id'], 'Snapshot identity')
    require(digest(manifest_bytes) == marker['manifest_sha256'] == reference['manifest_sha256'], 'Manifest digest')
    nas_bytes = (root / 'nas-generation/ledger.sqlite').read_bytes()
    require(digest(nas_bytes) == manifest['database']['sha256'] == reference['database_sha256'], 'Database digest')
    require(len(nas_bytes) == manifest['database']['byte_length'], 'Database length')
    require(manifest['summary'] == expected['summary'], 'Snapshot summary')
    require(manifest['producer']['source_commit'] == report['source_commit'], 'Producer identity')
    require(report['runtime']['installed_package_wheel_bytes'] == 'MATCH', 'Installed wheel binding')
    for stage in ('create', 'publish', 'independent_nas_verify', 'independent_nas_restore', 'check_restore'):
        value = report['stages'][stage]
        require(value['status'] == 'OK' and value['data']['database_sha256'] == reference['database_sha256'], 'Stage result')
        require(value['data']['summary'] == expected['summary'], 'Stage summary')
        if stage != 'check_restore':
            require(all(value['data'][key] == reference[key] for key in ('snapshot_id', 'manifest_sha256')), 'Stage snapshot identity')
    require([c['response']['operation'] for c in report['commands']] == ['verify', 'restore'], 'Child operations')
    require(all(c['exit_code'] == 0 for c in report['commands']), 'Child exits')
    require(report['stages']['concurrent_directory_probe'] == {'already_exists': 1, 'created': 1, 'processes': 2}, 'Directory contention')

    before = database(root / 'nas-generation/ledger.sqlite')
    after = database(root / 'run/restored/ledger.sqlite')
    for table, value in expected['image']['rows'].items():
        raw = json.dumps(before[table], ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
        require(len(before[table]) == value['count'] and digest(raw) == value['sha256'], 'Every original SQL row')
    payloads = {key: {'length': len(value), 'sha256': digest(value)} for key, value in before['payloads']}
    require(payloads == expected['image']['payloads'], 'Every original payload')
    deltas = {'ledger_format': 0, 'records': 3, 'refs': 4, 'operations': 1, 'payloads': 1}
    for table, count in deltas.items():
        require(set(before[table]) <= set(after[table]), 'Existing rows and bytes unchanged')
        require(len(after[table]) - len(before[table]) == count, 'Only expected additions')
    added_records = {key: (kind, json.loads(value)) for key, kind, value in set(after['records']) - set(before['records'])}
    require(set(added_records) == {'version:nas-after-restore', 'root:nas-after-restore', 'blob:nas-after-restore'}, 'Added identities')
    require({key: kind for key, (kind, _) in added_records.items()} == {
        'version:nas-after-restore': 'version', 'root:nas-after-restore': 'content_root',
        'blob:nas-after-restore': 'blob'}, 'Added record kinds')
    version = added_records['version:nas-after-restore'][1]
    require(version['artifact_id'] == 'artifact:nas-seed' and version['parent_version_refs'] == ['version:nas-merged'], 'Restored graph extension')
    added_refs = set(after['refs']) - set(before['refs'])
    require(added_refs == {
        ('version:nas-after-restore', 'parent_version_refs.0', 'version:nas-merged'),
        ('version:nas-after-restore', 'content_root_ref', 'root:nas-after-restore'),
        ('version:nas-after-restore', 'artifact_id', 'artifact:nas-seed'),
        ('root:nas-after-restore', 'blob_ref', 'blob:nas-after-restore'),
    }, 'Exact additional relations')
    addition = list(set(after['payloads']) - set(before['payloads']))
    require(addition == [('blob:nas-after-restore', 'NAS 恢复后的新增版本\n'.encode())], 'Actual new payload bytes')
    blob = added_records['blob:nas-after-restore'][1]
    require(blob['byte_length'] == len(addition[0][1]) and blob['digest'] == {
        'algorithm': 'sha256', 'value': digest(addition[0][1])}, 'New payload metadata')
    operation = list(set(after['operations']) - set(before['operations']))
    require(len(operation) == 1 and operation[0][:3] == ('acceptance:nas', 'append_version', 'append-after-restore')
            and operation[0][-1] == 'version:nas-after-restore', 'Only the new operation added')
    stage = report['stages']['exact_rows_payloads_replay_new_write']
    require(stage['all_rows_and_blobs_equal'] and stage['replay_preserved_state']
            and stage['new_synthetic_write'] == 'PASS', 'Recorded pre-write comparison and replay')
    return {
        'status': 'PASS', 'scope': 'post-exercise independent read-only audit',
        'readback_files': len(names), 'all_file_hashes_match': True,
        'nas_generation_matches_independent_reference': True,
        'nas_database_matches_all_expected_rows_and_payloads': True,
        'restored_database_preserves_every_original_row_and_payload': True,
        'only_expected_new_version_relations_operation_payload_added': True,
        'table_rows_before': {table: len(rows) for table, rows in before.items()},
        'table_rows_after': {table: len(rows) for table, rows in after.items()},
        'payload_bytes_before': sum(len(value) for _, value in before['payloads']),
        'payload_bytes_after': sum(len(value) for _, value in after['payloads']),
        'nas_exercise_replayed': False,
        'limit': 'Readback checks current bytes and retained execution evidence; not NAS power-loss durability.',
    }


if __name__ == '__main__':
    print(json.dumps(audit(Path(sys.argv[1])), ensure_ascii=False, indent=2))
