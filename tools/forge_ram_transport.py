"""Pinned-key read-only recovery SSH; no arbitrary command or mutation API."""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

from forge_ram_identity import command, verify
from forge_recovery_layout import reader as layout_reader, root_extent


def credential_pin(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != os.getuid() or before.st_mode & 0o077 or
                not 0 < before.st_size <= 65536):
            raise ValueError('Recovery SSH credentials must be private owned regular files')
        data = stream.read(65537)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
            raise ValueError('Recovery SSH credentials changed during capture')
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RecoveryProbe:
    host: str
    key: Path
    known_hosts: Path
    nonce: str
    kernel: str
    serial: str | None
    port: int = 2222
    mode: str = 'physical'
    _pins: tuple = field(init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.host, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', self.host):
            raise ValueError('Use a recovery hostname, not SSH options or a username')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('Invalid recovery port')
        if not isinstance(self.nonce, str) or not re.fullmatch('[0-9a-f]{32}', self.nonce):
            raise ValueError('Invalid recovery nonce')
        if not isinstance(self.kernel, str) or not re.fullmatch('[A-Za-z0-9.+_-]{1,128}', self.kernel):
            raise ValueError('Invalid expected recovery kernel')
        if self.mode not in ('physical', 'emulated'):
            raise ValueError('Invalid recovery mode')
        if self.mode == 'physical' and (not isinstance(self.serial, str) or
                                       not re.fullmatch('[0-9a-f]{16}', self.serial)):
            raise ValueError('Physical recovery requires an expected hardware serial')
        object.__setattr__(self, 'key', Path(self.key).absolute())
        object.__setattr__(self, 'known_hosts', Path(self.known_hosts).absolute())
        object.__setattr__(self, '_pins', (credential_pin(self.key), credential_pin(self.known_hosts)))

    def _argv(self, fixed_command):
        if (credential_pin(self.key), credential_pin(self.known_hosts)) != self._pins:
            raise ValueError('Recovery SSH identity files changed; explicit rebinding required')
        return ['ssh', '-F', '/dev/null', '-p', str(self.port), '-i', str(self.key),
                '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(self.known_hosts),
                '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=10',
                '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ClearAllForwardings=yes',
                '-o', 'RequestTTY=no', 'root@' + self.host, fixed_command]
    def _observe(self, fixed_command):
        result = subprocess.run(self._argv(fixed_command), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError('Pinned recovery SSH probe failed; no retry was performed')
        if len(result.stdout) > 2*1024*1024:
            raise ValueError('Oversized recovery observation')
        return json.loads(result.stdout)

    def inspect(self, *, expected_boot_id=None):
        if expected_boot_id is not None and (not isinstance(expected_boot_id, str) or
                not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', expected_boot_id)):
            raise ValueError('Invalid expected recovery boot identity')
        observed = self._observe(command())
        checked = verify(observed, self.nonce, self.kernel, self.serial, mode=self.mode)
        if expected_boot_id is not None and checked['boot_id'] != expected_boot_id:
            raise ValueError('Recovery rebooted; the previous session is no longer valid')
        return {'verification': checked, 'observation': observed}

    def inspect_storage(self, expected_cid, expected_disk_id, *, expected_boot_id, device='/dev/mmcblk0'):
        """Read layout only within an already-bound, still RAM-only session."""
        if expected_boot_id is None:
            raise ValueError('Storage inspection requires a bound recovery boot')
        source = layout_reader(device)
        before = self.inspect(expected_boot_id=expected_boot_id)
        observed = self._observe('/usr/bin/python3 -I -S -c ' + shlex.quote(source))
        extent = root_extent(observed, expected_cid, expected_disk_id, device=device)
        after = self.inspect(expected_boot_id=before['verification']['boot_id'])
        return {'verification': after['verification'], 'layout': observed, 'extent': extent}
