"""Read-only root partition claim held across a recovery boot-file commit.

Linux O_EXCL on the partition prevents competing exclusive opens and mounts;
it does not stop a privileged raw writer that deliberately ignores claims.
The caller must additionally fence Forge writers and verify RAM identity and
layout before entry, during preparation, and after unmounting boot storage.
This module grants no write permission and never mounts a filesystem.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import struct


def block_identity(fd, device, length):
    info = os.fstat(fd)
    expected = Path('/sys/class/block', device.rsplit('/', 1)[1], 'dev').read_text().strip()
    if not stat.S_ISBLK(info.st_mode) or f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' != expected:
        raise ValueError('Claimed block device identity differs')
    size = bytearray(8)
    fcntl.ioctl(fd, 0x80081272, size, True)
    if struct.unpack('=Q', size)[0] != length:
        raise ValueError('Claimed block device length differs')


def range_digest(fd, offset, length, *, progress=None):
    if any(type(value) is not int or value < 0 for value in (offset, length)):
        raise ValueError('Expected nonnegative integer byte range')
    digest = hashlib.sha256()
    consumed = 0
    while consumed < length:
        data = os.pread(fd, min(1024 * 1024, length-consumed), offset+consumed)
        if not data:
            raise ValueError('Claimed device read ended early')
        digest.update(data)
        consumed += len(data)
        if progress is not None:
            progress(consumed, length)
    return digest.hexdigest()


class RootClaim:
    def __init__(self, root_fd, card_fd, extent):
        self.root_fd, self.card_fd = root_fd, card_fd
        self.extent = dict(extent)
        self.active = True

    def verify_guards(self, guards, *, progress=None):
        """Hash through held descriptors, rejecting all range/digest mismatches.

        Prefix checks apply before boot writes, not after FAT metadata changes.
        Root and suffix can be checked again after the boot filesystem unmounts.
        """
        if not self.active:
            raise ValueError('Root claim has been released')
        if not isinstance(guards, dict) or not guards or set(guards) - {'root', 'prefix', 'suffix'}:
            raise ValueError('Expected explicit protected ranges')
        start = self.extent['offset_bytes']
        end = start + self.extent['length_bytes']
        ranges = {'root': (self.root_fd, 0, start, end-start),
                  'prefix': (self.card_fd, 0, 0, start),
                  'suffix': (self.card_fd, end, end, self.extent['disk_bytes']-end)}
        # Validate every range before beginning a potentially long read.
        for name, guard in guards.items():
            _, _, offset, length = ranges[name]
            if (not isinstance(guard, dict) or set(guard) != {'offset', 'bytes', 'sha256'} or
                    type(guard['offset']) is not int or guard['offset'] != offset or
                    type(guard['bytes']) is not int or guard['bytes'] != length or
                    not isinstance(guard['sha256'], str) or
                    not re.fullmatch('[0-9a-f]{64}', guard['sha256'])):
                raise ValueError('Protected range differs: ' + name)
        for name, guard in guards.items():
            fd, source_offset, _, length = ranges[name]
            callback = None if progress is None else lambda done, total: progress(name, done, total)
            if range_digest(fd, source_offset, length, progress=callback) != guard['sha256']:
                raise ValueError('Protected digest differs: ' + name)
        return {'status': 'matched', 'ranges': sorted(guards)}


@contextmanager
def claim_root(device, extent, check):
    """Hold root O_RDONLY|O_EXCL while permitting a separate boot mount.

    check() must return the freshly verified root extent from the bound RAM
    session. It is deliberately called before acquisition, after acquisition,
    and on successful exit; the caller must unmount boot before leaving.
    """
    if not isinstance(device, str) or not re.fullmatch('/dev/mmcblk[0-9]{1,2}', device):
        raise ValueError('Expected whole MMC device')
    extent = dict(extent)
    if (extent.get('device') != device+'p2' or
            any(type(extent.get(key)) is not int or extent[key] <= 0 or extent[key] % 512
                for key in ('offset_bytes', 'length_bytes', 'disk_bytes')) or
            extent['offset_bytes']+extent['length_bytes'] > extent['disk_bytes']):
        raise ValueError('Invalid claimed root geometry')
    if check() != extent:
        raise ValueError('Root layout changed before claim')
    root_fd = os.open(device+'p2', os.O_RDONLY | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK)
    claim = None
    try:
        block_identity(root_fd, device+'p2', extent['length_bytes'])
        card_fd = os.open(device, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            block_identity(card_fd, device, extent['disk_bytes'])
            if check() != extent:
                raise ValueError('Root layout changed after claim')
            claim = RootClaim(root_fd, card_fd, extent)
            yield claim
            if check() != extent:
                raise ValueError('Root layout changed before release')
        finally:
            if claim is not None:
                claim.active = False
            os.close(card_fd)
    finally:
        os.close(root_fd)
