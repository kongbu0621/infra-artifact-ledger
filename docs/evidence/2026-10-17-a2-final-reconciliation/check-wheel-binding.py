"""Local regression checks for the retained runner; no Ledger or NAS calls.

Run: python3 -I -B docs/evidence/2026-10-17-a2-final-reconciliation/check-wheel-binding.py
Only the real stdlib-only helpers are loaded from the runner's AST, so these
checks neither import the product nor execute its acceptance business.
"""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

RUNNER = Path(__file__).with_name('closure-probe.py')
TREE = ast.parse(RUNNER.read_text())
HELPERS = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
           and node.name in ('sha', 'wheel_modules', 'save_bytes', 'dump')]
SCOPE = dict(hashlib=hashlib, io=io, json=json, os=os, Path=Path, zipfile=zipfile)
exec(compile(ast.Module(body=HELPERS, type_ignores=[]), str(RUNNER), 'exec'), SCOPE)


class WheelBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ledger-wheel-check-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        self.members = {'infra_artifact_ledger/m%d.py' % i: b'original' for i in range(17)}
        for name, content in self.members.items():
            (self.package / name.split('/', 1)[1]).write_bytes(content)
        self.wheel = self.root / 'runtime.whl'
        self.write_wheel(self.wheel, self.members.items())

    def write_wheel(self, path, members):
        with warnings.catch_warnings(), zipfile.ZipFile(path, 'w') as archive:
            warnings.simplefilter('ignore', UserWarning)  # Intentional duplicate fixture.
            for name, content in members:
                archive.writestr(name, content)

    def check(self, expected=None):
        return SCOPE['wheel_modules'](self.wheel, expected or SCOPE['sha'](self.wheel.read_bytes()), self.package)

    def test_matching_image(self):
        self.assertEqual(self.check(), {name: SCOPE['sha'](data) for name, data in self.members.items()})

    def test_different_wheel_digest_is_rejected_before_archive_read(self):
        other = self.root / 'other.whl'
        self.write_wheel(other, [(n, b'different') for n in self.members])
        with patch.object(zipfile, 'ZipFile', side_effect=AssertionError('must verify hash first')):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.check(SCOPE['sha'](other.read_bytes()))

    def test_path_replacement_cannot_mix_two_wheel_images(self):
        expected = SCOPE['sha'](self.wheel.read_bytes())
        other = self.root / 'other.whl'
        self.write_wheel(other, [(n, b'different') for n in self.members])
        other_digest = SCOPE['sha'](other.read_bytes())
        actual_zipfile = zipfile.ZipFile

        def replace_path(source):
            self.assertIsInstance(source, io.BytesIO)
            other.replace(self.wheel)
            return actual_zipfile(source)

        with patch.object(zipfile, 'ZipFile', side_effect=replace_path):
            result = self.check(expected)
        self.assertEqual(result, {name: SCOPE['sha'](data) for name, data in self.members.items()})
        self.assertEqual(SCOPE['sha'](self.wheel.read_bytes()), other_digest)
        self.assertNotEqual(expected, other_digest)

    def test_duplicate_archive_member_is_rejected(self):
        self.write_wheel(self.wheel, [*self.members.items(), next(iter(self.members.items()))])
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            self.check()

    def test_equal_count_different_module_name_is_rejected(self):
        renamed = dict(self.members)
        renamed['infra_artifact_ledger/other.py'] = renamed.pop('infra_artifact_ledger/m0.py')
        self.write_wheel(self.wheel, renamed.items())
        with self.assertRaisesRegex(ValueError, 'membership'):
            self.check()

    def test_extra_installed_module_is_rejected(self):
        (self.package / 'extra.py').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'membership'):
            self.check()

    def test_same_size_changed_installed_bytes_are_rejected(self):
        (self.package / 'm0.py').write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            self.check()

    def test_linked_installed_module_is_rejected(self):
        module = self.package / 'm0.py'
        outside = self.root / 'outside.py'
        module.replace(outside)
        module.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'type or size'):
            self.check()

    def test_noncanonical_archive_path_is_rejected(self):
        renamed = dict(self.members)
        renamed['infra_artifact_ledger/sub/../m0.py'] = renamed.pop('infra_artifact_ledger/m0.py')
        self.write_wheel(self.wheel, renamed.items())
        with self.assertRaisesRegex(ValueError, 'membership'):
            self.check()

    def test_nested_module_uses_its_relative_path(self):
        (self.package / 'sub').mkdir()
        (self.package / 'm0.py').replace(self.package / 'sub/m0.py')
        renamed = dict(self.members)
        renamed['infra_artifact_ledger/sub/m0.py'] = renamed.pop('infra_artifact_ledger/m0.py')
        self.write_wheel(self.wheel, renamed.items())
        self.assertEqual(self.check(), {name: SCOPE['sha'](data) for name, data in renamed.items()})

    def test_optimization_is_rejected_before_product_import(self):
        for flag in ('-O', '-OO'):
            with self.subTest(flag=flag):
                completed = subprocess.run([sys.executable, '-I', '-B', flag, str(RUNNER),
                                            str(self.root / 'absent-plan.json')],
                                           capture_output=True, timeout=10)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, b'')
                self.assertEqual(completed.stderr, b'Acceptance requires assertions; run without -O or -OO.\n')

    def test_evidence_writers_preserve_existing_files_and_link_targets(self):
        for name, first, second in [('save_bytes', b'original', b'changed'),
                                    ('dump', {'original': True}, {'changed': True})]:
            with self.subTest(writer=name):
                target = self.root / name
                SCOPE[name](target, first)
                original = target.read_bytes()
                identity = target.stat()
                link = self.root / (name + '-link')
                link.symlink_to(target)
                for existing in (target, link):
                    with self.assertRaises(FileExistsError):
                        SCOPE[name](existing, second)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(target.stat().st_ino, identity.st_ino)
                self.assertEqual(target.stat().st_mtime_ns, identity.st_mtime_ns)
                self.assertTrue(link.is_symlink())


if __name__ == '__main__':
    unittest.main(verbosity=2)
