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
        self.injected = False

    @contextmanager
    def rejected_by(self, checker):
        # A setup/CLI failure is not evidence that the intended fault was caught.
        try:
            yield
        except AssertionError as exc:
            self.assertTrue(self.injected, 'fault was never injected')
            origin = exc.__traceback__
            while origin.tb_next is not None:
                origin = origin.tb_next
            self.assertIs(origin.tb_frame.f_code, checker.__code__,
                          'rejection came from an unrelated assertion')
        else:
            self.fail('injected fault was not rejected')

    def test_rejection_requires_injected_fault(self):
        def checker():
            raise AssertionError('unrelated setup failure')
        with self.assertRaisesRegex(AssertionError, 'fault was never injected'):
            with self.rejected_by(checker):
                checker()

    def test_rejection_requires_expected_origin(self):
        def checker():
            raise AssertionError('target check')
        def unrelated():
            raise AssertionError('unrelated failure')
        self.injected = True
        with self.assertRaisesRegex(AssertionError, 'rejection came from an unrelated assertion'):
            with self.rejected_by(checker):
                unrelated()

    def wrapper(self, factory, *, extra_on=None, bytearray_blob=False,
                wrong_result=False):
        probe = self.probe
        test = self

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
                    test.injected = True
                if wrong_result and result['result_ref'] == 'artifact:second':
                    result = {**result, 'result_ref': 'artifact:wrong'}
                    test.injected = True
                return result

            def get_operation(self, *args):
                result = self.ledger.get_operation(*args)
                if wrong_result and result['idempotency_record']['result_ref'] == 'artifact:second':
                    result['idempotency_record']['result_ref'] = 'artifact:wrong'
                    test.injected = True
                return result

            def read_blob(self, ref):
                raw = self.ledger.read_blob(ref)
                if bytearray_blob:
                    test.injected = True
                    return bytearray(raw)
                return raw

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
                    self.injected = True
            return code, result

        with patch.object(p.h, 'cli', extra), self.rejected_by(p.cross.assert_metadata):
            p.documents(self.root)

    def test_replay_write_is_rejected(self):
        p = self.probe
        wrapped = self.wrapper(p.public.open, extra_on=lambda r: r['replayed'])
        with patch.object(p.public, 'open', wrapped), self.rejected_by(p.cross.assert_metadata):
            p.documents(self.root)

    def test_wrong_result_shared_by_responses_is_rejected(self):
        p = self.probe
        cli = p.h.cli

        def wrong(root, *args):
            code, result = cli(root, *args)
            if code == 0 and result.get('result_ref') == 'artifact:second':
                result = {**result, 'result_ref': 'artifact:wrong'}
                self.injected = True
            return code, result

        wrapped = self.wrapper(p.public.open, wrong_result=True)
        with patch.object(p.h, 'cli', wrong), patch.object(p.public, 'open', wrapped):
            with self.rejected_by(p.cross.assert_response):
                p.documents(self.root)

    def test_document_blob_requires_bytes(self):
        p = self.probe
        wrapped = self.wrapper(p.public.open, bytearray_blob=True)
        with patch.object(p.public, 'open', wrapped), self.rejected_by(p.cross.assert_metadata):
            p.documents(self.root)

    def test_response_blob_requires_bytes(self):
        p = self.probe
        wrapped = self.wrapper(p.public.initialize, bytearray_blob=True)
        with patch.object(p.public, 'initialize', wrapped), self.rejected_by(p.responses):
            p.responses(self.root)

    def test_structural_continuation_checks_full_state(self):
        p = self.probe
        wrapped = self.wrapper(p.public.initialize,
            extra_on=lambda r: r['result_ref'] == 'artifact:after-structural')
        with patch.object(p.public, 'initialize', wrapped), self.rejected_by(p.cross.assert_metadata):
            p.structural(self.root)


if __name__ == '__main__':
    unittest.main()
