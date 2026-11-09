"""Explicit Linux target fixtures for portable host-side tests.

Do not emulate Linux filesystem guarantees on another kernel. Tests which
execute the actual target worker belong to the Linux gate instead.
"""
import base64
import hashlib
from pathlib import Path
import sys
import unittest


linux_target = unittest.skipUnless(sys.platform.startswith('linux'),
    'executes Linux target worker: requires Linux identity, xattrs and O_NOATIME')


def identity():
    return {'machine_id': 'a' * 32, 'boot_id': '9542e0f8-0d83-4e63-9fb1-625830fe3519'}


_read_text = Path.read_text


def target_identity_text(path, *args, **kwargs):
    """Supply only target identity files; never swallow unrelated file reads."""
    fields = {'/etc/machine-id': 'machine_id', '/proc/sys/kernel/random/boot_id': 'boot_id'}
    if str(path) in fields:
        return identity()[fields[str(path)]] + '\n'
    return _read_text(path, *args, **kwargs)


def file_backup(path, data=b'original\0payload'):
    """Wire-format preimage, independent of the host filesystem's capabilities."""
    return {'schema': 1, 'machine_id': identity()['machine_id'], 'files': [{
        'path': str(path), 'kind': 'file', 'data': base64.b64encode(data).decode(),
        'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
        'mode': 0o751, 'uid': 1000, 'gid': 1000, 'atime_ns': 1, 'mtime_ns': 1,
        'xattrs': {'user.forge-test': 'bWV0YWRhdGE='}}]}
