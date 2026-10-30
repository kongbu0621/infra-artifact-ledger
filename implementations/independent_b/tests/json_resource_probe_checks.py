"""Opt-in fault checks for the installed JSON capacity acceptance oracle.

Run with the selected installation's python -I -B. Adjacent probe/helper files
must be present. These checks retain the real public limits and use temporary
synthetic databases; injected behavior is confined to this test process.
"""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class JsonProbeChecks(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('json_probe',
            Path(__file__).with_name('json_resource_probe.py'))
        self.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.probe)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wrapper(self, factory, *, extra_on=None, bytearray_blob=False,
                wrong_result=False):
        probe = self.probe

        class Proxy:
            def __init__(self, ledger):
                self.ledger = ledger

            def __getattr__(self, name):
                return getattr(self.ledger, name)

            def execute(self, raw, **kwargs):
                result = self.ledger.execute(raw, **kwargs)
                if extra_on and extra_on(result):
                    self.ledger.execute(probe.f.wire(probe.f.create(
                        'artifact:unrequested', 'unrequested')))
                if wrong_result and result['result_ref'] == 'artifact:second':
                    result = {**result, 'result_ref': 'artifact:wrong'}
                return result

            def get_operation(self, *args):
                result = self.ledger.get_operation(*args)
                if wrong_result and result['idempotency_record']['result_ref'] == 'artifact:second':
                    result['idempotency_record']['result_ref'] = 'artifact:wrong'
                return result

            def read_blob(self, ref):
                raw = self.ledger.read_blob(ref)
                return bytearray(raw) if bytearray_blob else raw

        @contextmanager
        def wrapped(*args, **kwargs):
            with factory(*args, **kwargs) as ledger:
                yield Proxy(ledger)
        return wrapped

    def test_unrequested_cli_write_is_rejected(self):
        p = self.probe
        cli = p.h.cli

        def extra(root, *args):
            code, result = cli(root, *args)
            if code == 0 and args[0] == 'write':
                with p.public.open(args[args.index('--db')+1]) as ledger:
                    ledger.execute(p.f.wire(p.f.create('artifact:unrequested', 'unrequested')))
            return code, result

        with patch.object(p.h, 'cli', extra), self.assertRaises(AssertionError):
            p.documents(self.root)

    def test_replay_write_is_rejected(self):
        p = self.probe
        wrapped = self.wrapper(p.public.open, extra_on=lambda r: r['replayed'])
        with patch.object(p.public, 'open', wrapped), self.assertRaises(AssertionError):
            p.documents(self.root)

    def test_wrong_result_shared_by_responses_is_rejected(self):
        p = self.probe
        cli = p.h.cli

        def wrong(root, *args):
            code, result = cli(root, *args)
            if code == 0 and result.get('result_ref') == 'artifact:second':
                result = {**result, 'result_ref': 'artifact:wrong'}
            return code, result

        wrapped = self.wrapper(p.public.open, wrong_result=True)
        with patch.object(p.h, 'cli', wrong), patch.object(p.public, 'open', wrapped):
            with self.assertRaises(AssertionError):
                p.documents(self.root)

    def test_document_blob_requires_bytes(self):
        p = self.probe
        wrapped = self.wrapper(p.public.open, bytearray_blob=True)
        with patch.object(p.public, 'open', wrapped), self.assertRaises(AssertionError):
            p.documents(self.root)

    def test_response_blob_requires_bytes(self):
        p = self.probe
        wrapped = self.wrapper(p.public.initialize, bytearray_blob=True)
        with patch.object(p.public, 'initialize', wrapped), self.assertRaises(AssertionError):
            p.responses(self.root)

    def test_structural_continuation_checks_full_state(self):
        p = self.probe
        wrapped = self.wrapper(p.public.initialize,
            extra_on=lambda r: r['result_ref'] == 'artifact:after-structural')
        with patch.object(p.public, 'initialize', wrapped), self.assertRaises(AssertionError):
            p.structural(self.root)


if __name__ == '__main__':
    unittest.main()
