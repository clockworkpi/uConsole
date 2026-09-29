from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_firmware_gpio as validator


class FirmwareGPIOValidationTests(unittest.TestCase):
    def run_fixture(self, *, exit_code=0, console='', stop_error=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        workspace = Path(directory.name)
        (workspace / 'serial.log').write_text(console)
        runtime = Mock()
        runtime.process.poll.return_value = None
        runtime.execute.return_value = {'exit_code': exit_code, 'stdout': 'probe output'}

        def stop(force=False):
            if stop_error and not force:
                raise ValueError('read-only remount failed')
            runtime.process.poll.return_value = 0
        runtime.stop.side_effect = stop
        with patch.object(sys, 'argv', ['validator', '--workspace', str(workspace)]), \
                patch.object(validator, 'Runtime', return_value=runtime), \
                patch.object(validator, 'wait_for_log'):
            if exit_code or 'cleanup_srcu_struct' in console or stop_error:
                with self.assertRaises(ValueError):
                    validator.main()
            else:
                validator.main()
        records = list(workspace.glob('firmware-gpio-*.json'))
        self.assertEqual(len(records), 1)
        return json.loads(records[0].read_text()), runtime

    def test_success_requires_clean_stop(self):
        record, runtime = self.run_fixture()
        self.assertEqual(record['validation'], 'passed')
        self.assertEqual(record['physical_fidelity'], 'unverified')
        runtime.stop.assert_called_once_with()
        self.assertNotIn('forced_cleanup', record)

    def test_failed_probe_keeps_console_and_stops_cleanly(self):
        record, runtime = self.run_fixture(exit_code=1, console='diagnostic evidence')
        self.assertEqual(record['validation'], 'failed')
        self.assertEqual(record['console'], 'diagnostic evidence')
        runtime.stop.assert_called_once_with()
        self.assertNotIn('forced_cleanup', record)

    def test_boot_warning_is_not_success(self):
        record, _ = self.run_fixture(console='WARNING cleanup_srcu_struct')
        self.assertEqual(record['validation'], 'failed')

    def test_failed_clean_stop_records_forced_cleanup(self):
        record, runtime = self.run_fixture(stop_error=True)
        self.assertEqual(record['validation'], 'failed')
        self.assertTrue(record['forced_cleanup'])
        self.assertIn('remount failed', record['cleanup_error'])
        self.assertTrue(runtime.stop.call_args.kwargs['force'])


if __name__ == '__main__':
    unittest.main()
