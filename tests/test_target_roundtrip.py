from pathlib import Path
import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_target_roundtrip as validation


class TargetRebootTests(unittest.TestCase):
    def setUp(self):
        self.before = {'boot_id': 'a' * 36, 'machine_id': 'b' * 32,
                       'native_boot_hashes': ['config', 'cmdline'], 'system_state': 'running'}

    def test_same_boot_or_starting_does_not_prove_reboot(self):
        starting = dict(self.before, boot_id='c' * 36, system_state='starting')
        after = dict(starting, system_state='running')
        with patch.object(validation, 'boot_state', side_effect=[self.before, starting, after]), \
                patch.object(validation.threading, 'Event'):
            result = validation.wait_reboot('target', self.before)
        self.assertEqual(result['reconnect_attempts'], 3)

    def test_temporary_connection_failure_is_retried(self):
        after = dict(self.before, boot_id='c' * 36)
        with patch.object(validation, 'boot_state', side_effect=[ValueError('offline'), after]), \
                patch.object(validation.threading, 'Event'):
            self.assertEqual(validation.wait_reboot('target', self.before)['reconnect_attempts'], 2)

    def test_wrong_machine_and_boot_changes_fail(self):
        for field, value in [('machine_id', 'd' * 32), ('native_boot_hashes', ['changed'])]:
            after = dict(self.before, boot_id='c' * 36)
            after[field] = value
            with self.subTest(field=field), patch.object(validation, 'boot_state', return_value=after):
                with self.assertRaises(ValueError):
                    validation.wait_reboot('target', self.before)

    def test_deadline_does_not_report_success(self):
        with patch.object(validation.time, 'monotonic', side_effect=[0, 301]):
            with self.assertRaises(TimeoutError):
                validation.wait_reboot('target', self.before)

    def test_boot_capture_rejects_missing_lines(self):
        with patch.object(validation, 'ssh_run', return_value=subprocess.CompletedProcess([], 255, '', 'offline')):
            with self.assertRaises(ValueError):
                validation.boot_state('target')

    def test_boot_capture_parses_exact_paths_and_uuid(self):
        output = ('9542e0f8-0d83-4e63-9fb1-625830fe3519\n' + 'b' * 32 + '\n' +
                  'c' * 64 + '  /boot/firmware/config.txt\n' +
                  'd' * 64 + '  /boot/firmware/cmdline.txt\nrunning\n')
        with patch.object(validation, 'ssh_run', return_value=subprocess.CompletedProcess([], 0, output, '')):
            self.assertEqual(validation.boot_state('target')['system_state'], 'running')
        for invalid in (output.replace('config.txt', 'configXtxt'),
                        output.replace('9542e0f8-0d83-4e63-9fb1-625830fe3519', 'a' * 36)):
            with patch.object(validation, 'ssh_run', return_value=subprocess.CompletedProcess([], 0, invalid, '')):
                with self.assertRaises(ValueError):
                    validation.boot_state('target')

    def test_gui_and_mcp_modes_mutually_exclusive_before_capture(self):
        with patch.object(sys, 'argv', ['validator', '--host', 'host', '--source', 'source',
                '--sha256', 'a' * 64, '--proof', 'attached-forge-' + 'b' * 32,
                '--output', 'output', '--gui', '--mcp']), \
                patch.object(validation, 'capture') as capture, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                validation.main()
            capture.assert_not_called()

    def test_non_inert_fixture_rejected_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'script'
            data = b'#!/bin/sh\nexit 0\n'
            source.write_bytes(data)
            output = Path(directory) / 'output'
            with patch.object(sys, 'argv', ['validator', '--host', 'host', '--source', str(source),
                    '--sha256', hashlib.sha256(data).hexdigest(), '--proof', 'attached-forge-' + 'b' * 32,
                    '--output', str(output), '--gui']), patch.object(validation, 'capture') as capture:
                with self.assertRaises(ValueError):
                    validation.main()
                capture.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
