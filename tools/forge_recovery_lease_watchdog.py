"""Lease-fed BCM2835 watchdog; not yet enabled by the recovery launcher.

Tests inject a device; never open a development host's watchdog. The native
adapter re-observes the local RAM session before opening its fixed device.
"""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import platform
import stat
import struct
import time

from forge_ram_identity import READER, verify
from forge_recovery_lease_timer import SoftwareTimer


# Linux asm-generic ioctl ABI used by the supported ARM64 recovery kernel.
GETSUPPORT = 0x80285700
KEEPALIVE = 0x80045705
SETTIMEOUT = 0xc0045706
GETTIMEOUT = 0x80045707


def observe():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(READER, {})
    return json.loads(output.getvalue())


class LinuxWatchdog:
    def __init__(self, initial, kernel, serial, *, mode='physical'):
        self.fd = None
        self.timeout = None
        if platform.system() != 'Linux' or platform.machine() != 'aarch64':
            raise ValueError('Native recovery watchdog requires Linux ARM64')
        observed = observe()
        checked = verify(observed, initial.nonce, kernel, serial, mode=mode)
        if checked['boot_id'] != initial.boot_id:
            raise ValueError('Recovery watchdog boot binding differs')
        if [x for x in observed['cmdline'].split() if x.startswith('uconsole.recovery_watchdog=')] != [
                'uconsole.recovery_watchdog=1']:
            raise ValueError('Recovery watchdog was not explicitly selected')
        initial.check(time.monotonic())
        expected = Path('/sys/class/watchdog/watchdog0/dev').read_text().strip()
        fd = os.open('/dev/watchdog0', os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.fd = fd
        try:
            info = os.fstat(fd)
            if (not stat.S_ISCHR(info.st_mode) or
                    f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' != expected):
                raise ValueError('Watchdog device differs from sysfs')
            support = bytearray(40)
            fcntl.ioctl(fd, GETSUPPORT, support, True)
            flags, _, identity = struct.unpack('=II32s', support)
            if (identity.split(b'\0', 1)[0] != b'Broadcom BCM2835 Watchdog timer' or
                    flags & 0x180 != 0x180):
                raise ValueError('Unsupported watchdog identity or capabilities')
            timeout = bytearray(struct.pack('=i', 15))
            fcntl.ioctl(fd, SETTIMEOUT, timeout, True)
            fcntl.ioctl(fd, GETTIMEOUT, timeout, True)
            self.timeout = struct.unpack('=i', timeout)[0]
            if not 10 <= self.timeout <= 60:
                raise ValueError('Watchdog timeout outside qualified bounds')
        except BaseException:
            self.close()
            raise

    def ping(self):
        if self.fd is None:
            raise RuntimeError('Watchdog is closed')
        fcntl.ioctl(self.fd, KEEPALIVE, 0)

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            # No write('V') and no SETOPTIONS/DISABLECARD. Leave expiry armed.
            os.close(fd)


class LeaseWatchdog:
    def __init__(self, directory, initial, device, *, now=time.monotonic):
        self.device = device
        self.failed = False
        try:
            self.timer = SoftwareTimer(directory, initial, device.close, now=now)
        except BaseException:
            device.close()
            raise

    def step(self):
        if self.failed:
            raise RuntimeError('Lease watchdog has stopped')
        try:
            delay = self.timer.step()
            self.device.ping()
            return delay
        except BaseException:
            self.failed = True
            self.device.close()
            raise

    def close(self):
        self.failed = True
        try:
            self.device.close()
        finally:
            self.timer.close()

    def run(self, *, wait=time.sleep):
        try:
            while True:
                wait(self.step())
        finally:
            self.close()
