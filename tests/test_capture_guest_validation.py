from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_capture_guest as validation


class CaptureGuestTests(unittest.TestCase):
    def test_frontend_options_fail_before_fixture(self):
        with patch.object(validation, 'fixture') as fixture:
            for options in ({'gui': True}, {'mcp': True},
                            {'hotplug': True, 'gui': True, 'mcp': True}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    validation.run('/missing/base', 'expected', '/missing/out', **options)
            fixture.assert_not_called()

    def test_guest_probe_compiles(self):
        compile(validation.PROBE, '<capture-probe>', 'exec')
        compile(validation.inspect.getsource(validation.verify_capture) + '\n' + validation.PROBE,
                '<capture-probe-with-verifier>', 'exec')

    def test_hash_mismatch_does_not_create_fixture(self):
        with patch.object(validation, 'sha256', return_value='changed'), \
                patch.object(validation, 'fixture') as fixture:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validation.run('/missing/base', 'expected', '/missing/output')
            fixture.assert_not_called()

    def test_failed_capture_is_retained_and_vm_stopped(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'guest'
            runtime = MagicMock()
            runtime.process.poll.return_value = None
            runtime.execute.side_effect = [{'exit_code': 0},
                                           {'exit_code': 1, 'stderr': 'capture discontinuity'}]
            with patch.object(validation, 'sha256', return_value='expected'), \
                    patch.object(validation.shutil, 'disk_usage', return_value=MagicMock(free=1024**3)), \
                    patch.object(validation, 'fixture', side_effect=lambda *args: output.mkdir()), \
                    patch.object(validation, 'Runtime', return_value=runtime), \
                    patch.object(validation, 'wait_for_log'):
                with self.assertRaisesRegex(ValueError, 'Guest capture failed'):
                    validation.run('/missing/base', 'expected', output)
            record = json.loads((output / 'capture-acceptance.json').read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['probe']['stderr'], 'capture discontinuity')
            runtime.stop.assert_called_once_with()
            runtime.control.assert_not_called()
