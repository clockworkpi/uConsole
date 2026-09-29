"""Remove only verified unused emulator padding from an owned export copy."""
import os
import hashlib
import re
from pathlib import Path
import stat
import struct


def source_capacity(config, base):
    """Use recorded capacity, or verify a legacy workspace's pinned base."""
    if 'base_bytes' in config:
        size = config['base_bytes']
        if type(size) is not int or size < 512 or size % 512:
            raise ValueError('Invalid recorded source-card capacity')
        return size
    pin = config.get('base_sha256')
    if pin is None:
        return None  # No trustworthy provenance in older/imported metadata.
    if not isinstance(pin, str) or not re.fullmatch('[0-9a-f]{64}', pin):
        raise ValueError('Invalid legacy source-card checksum')
    try:
        # Legacy workspaces may reference a shared immutable base by symlink.
        # Reading it is safe only after its bytes match the recorded checksum.
        fd = os.open(Path(base).resolve(strict=True), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('Cannot verify legacy source capacity; re-import the original image') from exc
    with os.fdopen(fd, 'rb') as stream:
        initial = os.fstat(stream.fileno())
        if not stat.S_ISREG(initial.st_mode) or initial.st_size < 512 or initial.st_size % 512:
            raise ValueError('Invalid legacy source-card image')
        hashed = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if hashed != pin or any(getattr(initial, key) != getattr(after, key) for key in fields):
            raise ValueError('Legacy source-card image differs from its pinned checksum')
        return initial.st_size


def trim_copy(path, base_bytes):
    """Preserve source-card capacity, never truncate used guest storage.

    The caller owns a stopped workspace and an unpublished temporary export.
    Legacy workspaces without a recorded source capacity must not guess one.
    """
    if type(base_bytes) is not int or base_bytes < 512 or base_bytes % 512:
        raise ValueError('Invalid recorded source-card capacity')
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        initial = os.fstat(fd)
        virtual_bytes = 1 << (base_bytes-1).bit_length()
        if (not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or
                initial.st_uid != os.getuid() or initial.st_size != virtual_bytes):
            raise ValueError('Export copy differs from the expected emulator capacity')
        mbr = os.pread(fd, 512, 0)
        if len(mbr) != 512 or mbr[510:] != b'\x55\xaa':
            raise ValueError('Capacity preservation requires an MBR image')
        for index in range(4):
            entry = mbr[446+16*index:462+16*index]
            kind, start, sectors = entry[4], *struct.unpack_from('<II', entry, 8)
            if kind == 0 and start == sectors == 0:
                continue
            if (kind in (0, 0x05, 0x0f, 0x85, 0xee) or not start or not sectors or
                    (start+sectors)*512 > base_bytes):
                raise ValueError('Guest partition layout cannot fit the original card; export padding is not removable')
        offset = base_bytes
        while offset < virtual_bytes:
            count = min(1048576, virtual_bytes-offset)
            data = os.pread(fd, count, offset)
            if len(data) != count or data != b'\0'*count:
                raise ValueError('Guest data occupies emulator padding; refusing to discard it')
            offset += count
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
        if any(getattr(initial, key) != getattr(after, key) for key in fields):
            raise ValueError('Export copy changed during capacity verification')
        if initial.st_size != base_bytes:
            os.ftruncate(fd, base_bytes)
        os.fsync(fd)
        return dict(source_bytes=base_bytes, emulator_bytes=virtual_bytes,
                    removed_padding_bytes=virtual_bytes-base_bytes)
    finally:
        os.close(fd)
