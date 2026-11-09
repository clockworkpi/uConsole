from pathlib import Path
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from forge_recovery_deadline import DeadlineFile
from forge_recovery_lease import Lease
from forge_recovery_lease_watchdog import (
    GETSUPPORT, GETTIMEOUT, KEEPALIVE, SETTIMEOUT, LeaseWatchdog, LinuxWatchdog)


class Device:
    def __init__(self):
        self.closed = False
        self.pings = 0
        self.fail = False

    def close(self):
        self.closed = True

    def ping(self):
        if self.closed or self.fail:
            raise OSError('Device cannot be fed')
        self.pings += 1


class LeaseWatchdogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name).resolve()
        self.time = 10
        self.initial = Lease.start('a'*32, '11111111-2222-3333-4444-555555555555', 'b'*64, 10)
        self.writer = DeadlineFile(self.path, self.initial)
        self.addCleanup(self.writer.close)
        self.writer.publish(self.initial, 10)
        self.device = Device()
        self.keeper = LeaseWatchdog(self.path, self.initial, self.device, now=lambda: self.time)
        self.addCleanup(self.keeper.close)

    def test_renewal_then_expiry_stops_pinging(self):
        self.keeper.step()
        request = dict(schema=1, purpose='offline-backup', nonce=self.initial.nonce,
                       boot_id=self.initial.boot_id, owner=self.initial.owner, sequence=1, seconds=300)
        renewed = self.initial.renew(request, 200)
        self.writer.publish(renewed, 200)
        self.time = 201
        self.keeper.step()
        self.time = 499
        self.keeper.step()
        self.time = 500
        with self.assertRaises(RuntimeError):
            self.keeper.step()
        self.assertTrue(self.device.closed)
        self.assertEqual(self.device.pings, 3)
        with self.assertRaises(RuntimeError):
            self.keeper.step()
        self.assertEqual(self.device.pings, 3)

    def test_bad_state_closes_without_ping(self):
        (self.path/'deadline.json').unlink()
        with self.assertRaises(RuntimeError):
            self.keeper.step()
        self.assertTrue(self.device.closed)
        self.assertEqual(self.device.pings, 0)

    def test_ping_failure_is_terminal(self):
        self.device.fail = True
        with self.assertRaises(OSError):
            self.keeper.step()
        self.assertTrue(self.device.closed)

    def test_interrupted_wait_closes_device(self):
        def interrupt(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.keeper.run(wait=interrupt)
        self.assertTrue(self.device.closed)
        self.assertEqual(self.device.pings, 1)

    def test_native_adapter_rejects_normal_host_before_open(self):
        with patch('forge_recovery_lease_watchdog.os.open') as opened:
            with self.assertRaises(ValueError):
                LinuxWatchdog(self.initial, 'not-the-host-kernel', '100000007b961d25')
            opened.assert_not_called()

    def native_adapter(self, *, timeout=15, identity=b'Broadcom BCM2835 Watchdog timer', flags=0x180):
        observed = dict(boot_id=self.initial.boot_id, uid=0, kernel='6.12.62-v8+',
                        serial='100000007b961d25',
                        cmdline='root=/dev/ram0 rdinit=/init uconsole.recovery=1 '
                                'uconsole.recovery_watchdog=1 uconsole.forge_trial=' + self.initial.nonce,
                        mountinfo='1 1 0:2 / / rw - rootfs rootfs rw\n')
        calls = []
        def ioctl(fd, operation, buffer, mutate=True):
            self.assertEqual(fd, 55)
            calls.append(operation)
            if operation == GETSUPPORT:
                buffer[:] = struct.pack('=II32s', flags, 0, identity)
            elif operation in (SETTIMEOUT, GETTIMEOUT):
                buffer[:] = struct.pack('=i', timeout)
            elif operation != KEEPALIVE:
                self.fail('Unexpected ioctl, including any disarm operation')
            return 0
        with (patch('forge_recovery_lease_watchdog.platform.system', return_value='Linux'),
              patch('forge_recovery_lease_watchdog.platform.machine', return_value='aarch64'),
              patch('forge_recovery_lease_watchdog.observe', return_value=observed),
              patch('forge_recovery_lease_watchdog.time.monotonic', return_value=10),
              patch('forge_recovery_lease_watchdog.Path.read_text', return_value='10:130'),
              patch('forge_recovery_lease_watchdog.os.open', return_value=55) as opened,
              patch('forge_recovery_lease_watchdog.os.fstat', return_value=SimpleNamespace(
                  st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(10, 130))),
              patch('forge_recovery_lease_watchdog.os.close') as closed,
              patch('forge_recovery_lease_watchdog.os.write') as wrote,
              patch('forge_recovery_lease_watchdog.fcntl.ioctl', side_effect=ioctl)):
            try:
                device = LinuxWatchdog(self.initial, '6.12.62-v8+', '100000007b961d25')
                device.ping()
                device.close()
                device.close()
            finally:
                opened.assert_called_once()
                closed.assert_called_once_with(55)
                wrote.assert_not_called()
        return calls

    def test_native_adapter_never_disarms(self):
        self.assertEqual(self.native_adapter(), [GETSUPPORT, SETTIMEOUT, GETTIMEOUT, KEEPALIVE])

    def test_native_adapter_rejects_wrong_driver_flags_and_timeout(self):
        for options in ({'identity': b'other'}, {'flags': 0}, {'timeout': 9}, {'timeout': 61}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.native_adapter(**options)

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('cc'), 'Linux C headers required')
    def test_ioctl_constants_match_native_linux_headers(self):
        source = self.path/'abi.c'
        source.write_text('#include <stdio.h>\n#include <sys/ioctl.h>\n#include <linux/watchdog.h>\n'
                          'int main(void) { printf("%lu %lu %lu %lu", (unsigned long)WDIOC_GETSUPPORT,'
                          '(unsigned long)WDIOC_KEEPALIVE, (unsigned long)WDIOC_SETTIMEOUT,'
                          '(unsigned long)WDIOC_GETTIMEOUT); return 0; }\n')
        binary = self.path/'abi'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
        # This binary prints header constants only; it never accesses devices.
        values = subprocess.check_output([str(binary)], text=True).split()
        self.assertEqual(list(map(int, values)), [GETSUPPORT, KEEPALIVE, SETTIMEOUT, GETTIMEOUT])
