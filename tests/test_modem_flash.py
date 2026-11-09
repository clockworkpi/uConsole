import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'Code/scripts/uconsole-modem-flash.py'
spec = importlib.util.spec_from_file_location('modem_flash', SCRIPT)
modem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modem)


class ModemFlashTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.fastboot = self.directory / 'fastboot'
        self.fastboot.write_text('#!/bin/sh\nexit 0\n')
        self.fastboot.chmod(0o755)
        self.package = {'partitions': []}
        for partition in ('aboot', 'modem', 'system'):
            payload = partition.encode()
            filename = partition + '.img'
            (self.directory / filename).write_bytes(payload)
            self.package['partitions'].append(dict(partition=partition, file=filename,
                sha256=hashlib.sha256(payload).hexdigest()))

    def update(self, flash=True):
        modem.update(self.directory, self.package, self.fastboot, 'MDM9607', flash)

    def runner(self, output='MDM9607\tfastboot\n'):
        return patch.object(modem.subprocess, 'run',
                            return_value=subprocess.CompletedProcess([], 0, output, ''))

    def test_all_files_verified_before_any_usb_access(self):
        (self.directory / 'system.img').write_bytes(b'corrupt last partition')
        with self.runner() as run:
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                self.update()
            run.assert_not_called()

    def test_missing_file_stops_before_usb_access(self):
        (self.directory / 'modem.img').unlink()
        with self.runner() as run:
            with self.assertRaisesRegex(ValueError, 'Missing regular'):
                self.update()
            run.assert_not_called()

    def test_missing_tool_stops(self):
        self.fastboot.unlink()
        with self.runner() as run:
            with self.assertRaisesRegex(ValueError, 'executable not found'):
                self.update()
            run.assert_not_called()

    def test_ambiguous_missing_or_wrong_device_stops(self):
        for output in ('', 'other\tfastboot\n',
                       'MDM9607\tfastboot\nother\tfastboot\n',
                       'MDM9607\tfastboot\nMDM9607\tfastboot\n'):
            with self.subTest(output=output), self.runner(output) as run:
                with self.assertRaisesRegex(ValueError, 'exactly one'):
                    self.update()
                self.assertEqual(run.call_count, 1)

    def test_check_mode_only_lists_devices(self):
        with self.runner() as run:
            self.update(flash=False)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], [str(self.fastboot), 'devices'])

    def test_discovery_verifies_files_and_never_writes(self):
        manifest = self.directory / 'manifest.json'
        manifest.write_text(json.dumps({'test': self.package}))
        args = ['updater', str(self.directory), '--version', 'test', '--list-devices',
                '--fastboot', str(self.fastboot)]
        with patch.object(modem, 'MANIFEST', manifest), patch.object(modem.sys, 'argv', args), self.runner() as run, patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(modem.main(), 0)
            self.assertEqual(output.getvalue().strip(), 'MDM9607')
            self.assertEqual(run.call_count, 1)
            (self.directory / 'system.img').write_bytes(b'bad')
            run.reset_mock()
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(modem.main(), 1)
            run.assert_not_called()

    def test_installed_helper_rejects_alternate_executable(self):
        manifest = self.directory / 'manifest.json'
        manifest.write_text(json.dumps({'test': self.package}))
        args = ['updater', str(self.directory), '--version', 'test', '--list-devices',
                '--fastboot', str(self.fastboot)]
        with patch.object(modem, 'MANIFEST', manifest), patch.object(modem, 'SYSTEM_HELPER', SCRIPT), patch.object(modem.sys, 'argv', args), self.runner() as run, patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(modem.main(), 1)
            self.assertIn('system fastboot', errors.getvalue())
            run.assert_not_called()

    def test_partition_failure_or_timeout_stops_without_reboot(self):
        for error in (subprocess.CalledProcessError(1, 'flash'),
                      subprocess.TimeoutExpired('flash', 180)):
            with self.subTest(error=type(error).__name__), self.runner() as run:
                run.side_effect = [subprocess.CompletedProcess([], 0, 'MDM9607 fastboot\n', ''),
                                   subprocess.CompletedProcess([], 0), error]
                with self.assertRaises(type(error)):
                    self.update()
                self.assertEqual(run.call_count, 3)
                self.assertFalse(any('reboot' in call.args[0] for call in run.call_args_list))

    def test_success_targets_serial_for_every_write_and_reboot(self):
        with self.runner() as run:
            self.update()
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[1:], [
                [str(self.fastboot), '-s', 'MDM9607', 'flash', item['partition'],
                 str(self.directory / item['file'])] for item in self.package['partitions']
            ] + [[str(self.fastboot), '-s', 'MDM9607', 'reboot']])


if __name__ == '__main__':
    unittest.main()
