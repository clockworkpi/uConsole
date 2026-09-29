"""Read-only export guard for the supported MBR/ext4 guest layout.

This checks the primary superblock, not every inode or file. A passing result
is not an fsck result, a boot test, or hardware qualification.
Layout: https://www.kernel.org/doc/html/latest/filesystems/ext4/super.html
"""
from pathlib import Path
import json
import struct
import subprocess
import tempfile

from forge_workspace import WorkspaceLock


def crc32c(data):
    value = 0xffffffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x82f63b78 if value & 1 else 0)
    return value


def root_start(mbr, size):
    if len(mbr) != 512 or mbr[510:] != b'\x55\xaa' or mbr[466] != 0x83:
        raise ValueError('Export requires the supported MBR/Linux partition-2 layout')
    start, sectors = struct.unpack_from('<II', mbr, 470)
    if start < 1 or sectors < 4 or (start + sectors) * 512 > size:
        raise ValueError('Export root partition is truncated or out of bounds')
    return start


def check_export_root(image):
    image = Path(image)
    with image.open('rb') as stream:
        size = image.stat().st_size
        mbr = stream.read(512)
        start = root_start(mbr, size)
        stream.seek(start * 512 + 1024)
        block = stream.read(1024)
    return check_root_superblock(block)


def check_overlay_root(workspace, qemu_img):
    """Read just MBR/superblock from a stopped qcow2, without a full raw copy."""
    workspace = Path(workspace).resolve()
    disk = workspace / 'disk.qcow2'
    with WorkspaceLock(workspace), tempfile.TemporaryDirectory(prefix='uc-root-check-') as directory:
        info = subprocess.run([str(qemu_img), 'info', '-f', 'qcow2', '--output=json', str(disk)],
                              check=True, capture_output=True, text=True)
        size = json.loads(info.stdout)['virtual-size']
        if type(size) is not int or size < 512:
            raise ValueError('Invalid overlay virtual size')
        def read_sectors(name, start, count):
            target = Path(directory) / name
            # qemu-img dd's count is an input end offset, before skip is
            # subtracted, unlike the number of copied blocks in POSIX dd.
            subprocess.run([str(qemu_img), 'dd', '-f', 'qcow2', '-O', 'raw', 'bs=512',
                            f'skip={start}', f'count={start + count}', f'if={disk}', f'of={target}'],
                           check=True, capture_output=True)
            return target.read_bytes()
        start = root_start(read_sectors('mbr', 0, 1), size)
        return check_root_superblock(read_sectors('superblock', start + 2, 2))


def check_root_superblock(block):
    if len(block) != 1024 or struct.unpack_from('<H', block, 0x38)[0] != 0xef53:
        raise ValueError('Export requires an ext4 root superblock')
    state = struct.unpack_from('<H', block, 0x3a)[0]
    incompat, ro_compat = struct.unpack_from('<II', block, 0x60)
    if not incompat & 0x40:
        raise ValueError('Export root must use the supported ext4 extent format')
    if ro_compat & 0x400:
        if block[0x175] != 1 or crc32c(block[:0x3fc]) != struct.unpack_from('<I', block, 0x3fc)[0]:
            raise ValueError('Export root superblock checksum is invalid')
    orphan = struct.unpack_from('<I', block, 0xe8)[0]
    if state != 1 or incompat & 4 or ro_compat & 0x10000 or orphan:
        raise ValueError('Export refused: root filesystem is unclean or needs recovery; '
                         'preserve the workspace and repair a copy before exporting')
    return {'filesystem': 'ext4', 'clean_state': True,
            'superblock_checksum_checked': bool(ro_compat & 0x400),
            'full_filesystem_check': False}
