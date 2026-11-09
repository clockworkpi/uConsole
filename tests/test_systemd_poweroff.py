"""Acceptance harness bookkeeping; these mocks do not qualify guest shutdown."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_systemd_poweroff as validator


class SystemdPoweroffTests(unittest.TestCase):
    def exercise(self, failure=None, expect_installed=False):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            module = workspace / 'candidate.ko'
            module.write_bytes(b'candidate')
            events, uploads = [], {}
            identity = 'a' * 32

            class FakeRuntime:
                def __init__(self, args):
                    self.args = args
                    self.process = self
                    self.returncode = None

                def start(self):
                    events.append(('start', self.args.mode))
                    (workspace / 'serial.log').write_text('booting')

                def poll(self):
                    return self.returncode

                def wait(self, timeout):
                    events.append(('wait', timeout))
                    if failure == 'timeout':
                        raise subprocess.TimeoutExpired('qemu', timeout)
                    self.returncode = 0
                    (workspace / 'serial.log').write_text(
                        'UCONSOLE_SYSTEMD_POWEROFF_' + identity + '\nreboot: Power down\n')

                def execute(self, script):
                    events.append(('execute', script))
                    return {'exit_code': int(failure == 'cleanup' and script.startswith('rm -f')),
                            'stdout': ''}

                def upload(self, source, target):
                    uploads[target] = Path(source).read_bytes()

                def stop(self, force=False):
                    events.append(('stop', force))
                    self.returncode = 0

                def release(self):
                    events.append(('release', self.args.mode))

            def check_root(*args):
                events.append(('root-check', None))
                if failure == 'unclean':
                    raise ValueError('unclean root')
                return {'clean_state': True, 'full_filesystem_check': False}

            argv = ['validator', '--workspace', directory, '--module', str(module),
                    '--boot-shutdown-timeout', '123']
            if expect_installed:
                argv.append('--expect-installed')
            with patch.object(sys, 'argv', argv), \
                 patch.object(validator, 'Runtime', FakeRuntime), \
                 patch.object(validator, 'wait_for_log'), \
                 patch.object(validator, 'wait_for_shutdown',
                              side_effect=lambda process, log, token, timeout: process.wait(timeout)), \
                 patch.object(validator, 'check_overlay_root', side_effect=check_root), \
                 patch.object(validator.uuid, 'uuid4', return_value=SimpleNamespace(hex=identity)), \
                 contextlib.redirect_stdout(io.StringIO()):
                if failure:
                    with self.assertRaises((subprocess.TimeoutExpired, ValueError, AssertionError)):
                        validator.main()
                else:
                    validator.main()
            evidence = json.loads((workspace / f'systemd-poweroff-{identity}.json').read_text())
            return evidence, events, uploads

    def test_installed_candidate_is_not_hot_replaced(self):
        evidence, _, uploads = self.exercise(expect_installed=True)
        self.assertTrue(evidence['expect_installed'])
        script = next(content for path, content in uploads.items() if path.endswith('/run.sh'))
        self.assertIn(b'modprobe i2c_bcm2835', script)
        self.assertIn(b'modinfo -F srcversion', script)
        for mutation in (b'unbind', b'insmod ', b'modprobe -r'):
            self.assertNotIn(mutation, script)

    def test_success_checks_stopped_root_before_cleanup_boot(self):
        evidence, events, uploads = self.exercise()
        self.assertEqual(evidence['validation'], 'passed')
        self.assertTrue(evidence['fixtures_removed'])
        self.assertEqual(evidence['boot_shutdown_timeout'], 123)
        self.assertIn(('wait', 123), events)
        check = events.index(('root-check', None))
        self.assertEqual(events[check - 1], ('release', 'normal'))
        cleanup_boot = [i for i, event in enumerate(events) if event == ('start', 'maintenance')][-1]
        self.assertLess(check, cleanup_boot)
        self.assertEqual(len(uploads), 6)
        cleanup_script = next(event[1] for event in events
                              if event[0] == 'execute' and event[1].startswith('rm -f'))
        for fixture in evidence['fixtures'][1:]:
            self.assertIn(fixture, cleanup_script)
        for target, content in uploads.items():
            if target.endswith(('.service', '.timer')):
                self.assertIn(b'ConditionKernelCommandLine=uconsole.poweroff-test=', content)
            if target.endswith('-diagnostic.service'):
                self.assertNotIn(b'After=multi-user.target', content)
                self.assertIn(b'systemctl --no-pager list-jobs', content)
            if target.endswith('/run.sh'):
                self.assertLess(content.index(b'/axp20x-i2c/unbind'),
                                content.index(b'modprobe -r i2c_bcm2835'))
                self.assertLess(content.index(b'insmod '),
                                content.index(b'modprobe axp20x_i2c'))

    def test_timeout_retains_console_and_forces_only_owned_runtime(self):
        evidence, events, _ = self.exercise('timeout')
        self.assertEqual(evidence['validation'], 'failed')
        self.assertTrue(evidence['forced_cleanup'])
        self.assertTrue(evidence['fixtures_removed'])
        self.assertEqual(evidence['last_console'], 'booting')
        self.assertIn(('stop', True), events)
        self.assertNotIn(('root-check', None), events)

    def test_wait_detects_guest_failure_before_deadline(self):
        process = MagicMock()
        process.wait.side_effect = subprocess.TimeoutExpired('qemu', 1)
        process.poll.return_value = None
        log = MagicMock()
        log.read_text.return_value = '[ 68.4] sh[835]: UCONSOLE_SYSTEMD_FAILURE_abc:1\n'
        with self.assertRaisesRegex(ValueError, 'fixture failed with exit 1'):
            validator.wait_for_shutdown(process, log, 'abc', 600)
        process.wait.assert_called_once_with(timeout=1)

    def test_wait_ignores_echo_trace_and_unrelated_run_markers(self):
        process, log = MagicMock(), MagicMock()
        process.poll.return_value = 0
        log.read_text.return_value = ('sh[835]: + echo UCONSOLE_SYSTEMD_FAILURE_abc:1\n'
                                      'sh[835]: UCONSOLE_SYSTEMD_FAILURE_other:1\n')
        validator.wait_for_shutdown(process, log, 'abc', 600)

    def test_wait_keeps_absolute_deadline(self):
        process, log = MagicMock(), MagicMock()
        process.wait.side_effect = subprocess.TimeoutExpired('qemu', 1)
        process.poll.return_value = None
        process.args = ['qemu']
        log.read_text.return_value = ''
        with patch.object(validator.time, 'monotonic', side_effect=[10, 10, 11]), \
             self.assertRaises(subprocess.TimeoutExpired) as raised:
            validator.wait_for_shutdown(process, log, 'abc', 1)
        self.assertEqual(raised.exception.timeout, 1)

    def test_unclean_root_cannot_pass_despite_clean_exit_and_fixture_removal(self):
        evidence, _, _ = self.exercise('unclean')
        self.assertEqual(evidence['validation'], 'failed')
        self.assertTrue(evidence['fixtures_removed'])
        self.assertNotIn('normal_shutdown_passed', evidence)

    def test_cleanup_failure_cannot_pass_despite_successful_shutdown(self):
        evidence, events, _ = self.exercise('cleanup')
        self.assertEqual(evidence['validation'], 'failed')
        self.assertTrue(evidence['normal_shutdown_passed'])
        self.assertNotIn('fixtures_removed', evidence)
        self.assertIn('cleanup_error', evidence)
        self.assertTrue(evidence['forced_fixture_cleanup'])
        self.assertIn(('stop', True), events)


if __name__ == '__main__':
    unittest.main()
