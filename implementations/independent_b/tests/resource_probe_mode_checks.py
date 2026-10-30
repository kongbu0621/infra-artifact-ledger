"""Opt-in regression checks for installed acceptance execution modes.

Run with either existing A/B installation's python -I -B. Child processes
exercise the real entry points; normal-mode work is stopped at its first
fixture so these checks do not repeat large capacity acceptance.
"""
import importlib.metadata
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PROBES = ('string_resource_probe', 'json_resource_probe', 'transport_resource_probe')
CHILD = r'''
import importlib.util
from pathlib import Path
import sys
target, implementation, destination = sys.argv[1:]
spec = importlib.util.spec_from_file_location('probe', target)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
def work_started(*args):
    print('FIXTURE_REACHED', flush=True)
    raise SystemExit(91)
name = Path(target).stem
setattr(p, {'string_resource_probe': 'axes', 'json_resource_probe': 'documents',
            'transport_resource_probe': 'fixture'}[name], work_started)
sys.argv = [target, '--implementation', implementation, '--workdir', destination]
if name == 'transport_resource_probe':
    sys.argv.extend(['--axis', 'descriptor'])
p.main()
'''


class ResourceProbeModeChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try:
            importlib.metadata.distribution('infra-artifact-ledger-b')
        except importlib.metadata.PackageNotFoundError:
            self.implementation = 'a'
        else:
            self.implementation = 'b'

    def run_entry(self, name, flags, destination):
        return subprocess.run([sys.executable, *flags, '-c', CHILD,
            str(Path(__file__).with_name(name + '.py')), self.implementation,
            str(destination)], cwd=self.root, capture_output=True, timeout=30)

    def assert_refused(self, result, destination):
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertIn(b'error: use an installed interpreter', result.stderr)
        self.assertEqual(result.stdout, b'')
        self.assertFalse(destination.exists(), 'refused entry created output or a database')

    def test_optimized_entry_refused_before_work(self):
        for name in PROBES:
            for optimization in ('-O', '-OO'):
                with self.subTest(probe=name, optimization=optimization):
                    destination = self.root / (name + optimization)
                    self.assert_refused(self.run_entry(name,
                        ['-I', '-B', optimization], destination), destination)

    def test_nonisolated_entry_refused_before_work(self):
        for name in PROBES:
            with self.subTest(probe=name):
                destination = self.root / name
                self.assert_refused(self.run_entry(name, ['-B'], destination), destination)

    def test_normal_entry_reaches_work(self):
        for name in PROBES:
            with self.subTest(probe=name):
                destination = self.root / name
                result = self.run_entry(name, ['-I', '-B'], destination)
                self.assertEqual(result.returncode, 91, result.stderr.decode())
                self.assertEqual(result.stderr, b'')
                self.assertEqual(result.stdout, b'FIXTURE_REACHED\n')
                self.assertTrue(destination.is_dir())

    def test_string_rejection_checks_exact_error_and_state(self):
        spec = importlib.util.spec_from_file_location('probe',
            Path(__file__).with_name('string_resource_probe.py'))
        p = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(p)
        selected = ('artifact_id', 'version_id', 'import_receipt_id')
        original = p.invoke
        for axis in (a for a in p.axes() if a['name'] in selected):
            for mode in ('api', 'cli'):
                for field, wrong in (('code', 'IO_ERROR'), ('commit_state', 'unknown')):
                    with self.subTest(axis=axis['name'], mode=mode, field=field):
                        injected = []

                        def corrupt(*args):
                            response = original(*args)
                            if args[-1] == 'boundary':
                                self.assertEqual((response.get('code'), response.get('commit_state')),
                                                 ('INVALID_INPUT', 'not_committed'))
                                injected.append(True)
                                return {**response, field: wrong}
                            return response

                        destination = self.root / (axis['name'] + mode + field)
                        with patch.object(p, 'invoke', corrupt):
                            try:
                                p.check(destination, axis, 'ascii', 2, mode)
                            except AssertionError as exc:
                                self.assertEqual(injected, [True], 'fault was not injected exactly once')
                                origin = exc.__traceback__
                                while origin.tb_next:
                                    origin = origin.tb_next
                                self.assertIs(origin.tb_frame.f_code, p.check.__code__,
                                              'failure came from an unrelated assertion')
                            else:
                                self.fail('incorrect rejection was accepted')


if __name__ == '__main__':
    unittest.main()
