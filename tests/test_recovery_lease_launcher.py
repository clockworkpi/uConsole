import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from build_recovery_initramfs import LEASE_MODULES, lease_resources
from forge_recovery_lease_launcher import await_children, binding, main, ready_file, run_child, report_failure
from forge_recovery_runtime import INIT


class LeaseLauncherTests(unittest.TestCase):
    def test_failure_diagnostic_has_only_bounded_reason_codes(self):
        with patch('forge_recovery_lease_launcher.os.open',return_value=42), \
                patch('forge_recovery_lease_launcher.os.write') as write, \
                patch('forge_recovery_lease_launcher.os.close'):
            report_failure('software',ValueError('Recovery lease expired or clock reversed'))
            self.assertIn(b'reason=expired-or-clock-reversed',write.call_args.args[1])
            report_failure('untrusted-role',ValueError('secret must not be logged'))
            self.assertEqual(write.call_args.args[1],b'Forge recovery lease failure: role=unknown reason=other\n')
        with patch('forge_recovery_lease_launcher.os.open',side_effect=OSError('console unavailable')):
            report_failure('reboot',ValueError('unsafe state'))

    def observation(self):
        return dict(boot_id='11111111-2222-3333-4444-555555555555', uid=0,
                    kernel='6.12.62-v8+', serial='100000007b961d25',
                    cmdline='root=/dev/ram0 rdinit=/init uconsole.recovery=1 '
                            'uconsole.recovery_watchdog=1 uconsole.recovery_lease=1 '
                            'uconsole.forge_trial='+'a'*32+' uconsole.recovery_owner='+'b'*64,
                    mountinfo='1 1 0:2 / / rw - rootfs rootfs rw\n')

    def test_binding_requires_ram_and_unambiguous_opt_in(self):
        observed = self.observation()
        self.assertEqual(binding(observed), ('a'*32, 'b'*64, 'physical'))
        for addition in (' uconsole.recovery_owner='+'c'*64, ' uconsole.recovery_lease=0',
                         ' root=/dev/mmcblk0p2', ' uconsole.recovery_watchdog=0'):
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                binding(dict(observed, cmdline=observed['cmdline']+addition))
        with self.assertRaises(ValueError):
            binding(dict(observed, mountinfo='1 1 179:2 / / rw - ext4 /dev/mmcblk0p2 rw\n'))

    def test_invalid_identity_precedes_any_side_effect(self):
        with (patch('forge_recovery_lease_launcher.observe', return_value=dict(self.observation(), uid=1000)),
              patch('forge_recovery_lease_launcher.Path.mkdir') as mkdir,
              patch('forge_recovery_lease_launcher.os.fork') as fork):
            with self.assertRaises(ValueError):
                main()
            mkdir.assert_not_called()
            fork.assert_not_called()

    def test_both_children_required_and_eof_rejected(self):
        readers, writers = zip(os.pipe(), os.pipe())
        try:
            for fd in writers:
                os.write(fd, b'R')
            await_children(readers, timeout=1)
        finally:
            for fd in readers+writers:
                os.close(fd)
        read, write = os.pipe()
        os.close(write)
        try:
            with self.assertRaises(RuntimeError):
                await_children([read], timeout=1)
        finally:
            os.close(read)

    def test_startup_timeout_is_bounded(self):
        read, write = os.pipe()
        try:
            with self.assertRaises(TimeoutError):
                await_children([read], timeout=0.01)
        finally:
            os.close(read)
            os.close(write)

    def test_resources_are_complete_and_readiness_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lease_resources(root)
            self.assertEqual({p.stem for p in root.glob('*.py')}, set(LEASE_MODULES)|{'lease-launch'})
            for path in root.glob('*.py'):
                compile(path.read_text(), str(path), 'exec')
            ready_file(root/'ready', {'ready': True})
            self.assertEqual((root/'ready').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                ready_file(root/'ready', {'ready': False})

    def test_init_shell_syntax_and_opt_in_path(self):
        subprocess.run(['sh', '-n'], input=INIT, text=True, check=True)
        self.assertIn('/usr/bin/python3 -I -S /etc/forge/lease-launch.py', INIT)
        self.assertIn('*) ( sleep 300; reboot -f ) & ;;', INIT)
        self.assertLess(INIT.index('stage=lease-readiness'), INIT.index('stage=network-driver'))

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX fork required')
    def test_timer_return_or_cleanup_error_cannot_enter_parent_code(self):
        for cleanup_failure in (False, True):
            class Consumer:
                def __init__(self, *args):
                    pass
                def step(self):
                    pass
                def run(self):
                    return
                def close(self):
                    if cleanup_failure:
                        raise OSError('injected cleanup failure')
            read, write = os.pipe()
            with patch('forge_recovery_lease_launcher.SoftwareTimer', Consumer):
                pid = os.fork()
                if pid == 0:
                    os.close(read)
                    run_child('software', None, {}, 'emulated', write)
                    os._exit(99)  # Must be unreachable, including on cleanup failure.
            os.close(write)
            try:
                await_children([read], timeout=2)
                child, status = os.waitpid(pid, 0)
                self.assertEqual(child, pid)
                self.assertEqual(os.waitstatus_to_exitcode(status), 1)
                self.assertEqual(os.read(read, 2), b'')
            finally:
                os.close(read)
