"""Current-disk boot artifact selection and preflight failure preservation."""
import gzip
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_boot import boot_profile, refresh_boot, validate_kernel
from forge_workspace import WorkspaceLock, sha256
from uconsole_emulator import executable


def cm4_dtb():
    def word(*values):
        return struct.pack('>' + 'I' * len(values), *values)

    def node(name, children=b''):
        data = name.encode() + b'\0'
        return word(1) + data + b'\0' * (-len(data) % 4) + children + word(2)

    tree = node('', node('soc', node('usb@7e980000') +
                        node('serial@7e201000', node('bluetooth')))) + word(9)
    return word(0xd00dfeed, 56 + len(tree), 56, 56 + len(tree), 40,
                17, 16, 0, 0, len(tree)) + b'\0' * 16 + tree


def disk_fixture(kernel=None, config=None):
    kernel = kernel or (b'\0' * 56 + b'ARM\x64' + b'\0' * 196)
    config = config or b'[pi4]\nkernel=kernel8.img\ndevice_tree=cm4.dtb\n'
    data = bytearray(512 * 100)
    data[510:512] = b'\x55\xaa'
    data[450] = 6
    data[466] = 0x83
    struct.pack_into('<I', data, 440, 0x12345678)
    struct.pack_into('<II', data, 454, 1, 99)
    struct.pack_into('<H', data, 523, 512)
    data[525] = 1
    struct.pack_into('<H', data, 526, 1)
    data[528] = 1
    struct.pack_into('<H', data, 529, 32)
    struct.pack_into('<H', data, 534, 1)
    for index, (name, contents) in enumerate(((b'CONFIG  TXT', config),
                                             (b'KERNEL8 IMG', kernel),
                                             (b'CM4     DTB', cm4_dtb()))):
        assert len(contents) <= 512
        cluster = index + 2
        struct.pack_into('<H', data, 1024 + cluster * 2, 0xffff)
        entry = 1536 + index * 32
        data[entry:entry + 11] = name
        struct.pack_into('<H', data, entry + 26, cluster)
        struct.pack_into('<I', data, entry + 28, len(contents))
        offset = 2560 + index * 512
        data[offset:offset + len(contents)] = contents
    return bytes(data)


class BootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.old = {'root': 'PARTUUID=old-02', 'surrogate_desktop': {'schema': 7}}
        (self.path / 'machine.json').write_text(json.dumps(self.old))
        for name in ('kernel8.img', 'cm4-original.dtb', 'cm4-qemu.dtb'):
            (self.path / name).write_bytes(b'old artifacts')
        self.disk = disk_fixture()

    def copy_prefix(self, argv, **kwargs):
        self.assertEqual(argv[1:6], ['dd', '-f', 'qcow2', '-O', 'raw'])
        self.assertNotIn('-U', argv)
        count = int(next(item[6:] for item in argv if item.startswith('count=')))
        block = int(next(item[3:] for item in argv if item.startswith('bs=')))
        target = Path(next(item[3:] for item in argv if item.startswith('of=')))
        target.write_bytes(self.disk[:count * block])

    def test_refresh_replaces_stale_files_and_tracks_current_identity(self):
        with patch('forge_boot.subprocess.run', side_effect=self.copy_prefix):
            with WorkspaceLock(self.path):
                config = refresh_boot(self.path, 'fixture-qemu-img')
        self.assertEqual(config['root'], 'PARTUUID=12345678-02')
        self.assertEqual(config['surrogate_desktop'], self.old['surrogate_desktop'])
        self.assertEqual(config['kernel_sha256'], sha256(self.path / 'kernel8.img'))
        self.assertIn(b'brcm,bcm2835-usb', (self.path / 'cm4-qemu.dtb').read_bytes())
        self.assertEqual(config['boot_profile']['device_tree'], 'cm4.dtb')

    def test_invalid_kernel_preserves_old_files_and_manifest(self):
        self.disk = disk_fixture(kernel=b'not a kernel')
        with patch('forge_boot.subprocess.run', side_effect=self.copy_prefix):
            with self.assertRaisesRegex(ValueError, 'ARM64'):
                refresh_boot(self.path, 'fixture-qemu-img')
        self.assertEqual((self.path / 'kernel8.img').read_bytes(), b'old artifacts')
        self.assertEqual(json.loads((self.path / 'machine.json').read_text()), self.old)

    def test_insufficient_space_blocks_large_copy_and_preserves_artifacts(self):
        with patch('forge_boot.subprocess.run', side_effect=self.copy_prefix) as copy, \
                patch('forge_boot.shutil.disk_usage') as usage:
            usage.return_value.free = 1048575
            with self.assertRaisesRegex(ValueError, 'need at least 1048576 bytes'):
                refresh_boot(self.path, 'fixture-qemu-img')
            self.assertEqual(copy.call_count, 1)  # Only the 512-byte MBR was read.
        self.assertEqual(json.loads((self.path / 'machine.json').read_text()), self.old)
        for name in ('kernel8.img', 'cm4-original.dtb', 'cm4-qemu.dtb'):
            self.assertEqual((self.path / name).read_bytes(), b'old artifacts')
        self.assertEqual(list(self.path.glob('.boot-refresh-*')), [])

    def test_profile_selection_and_unsupported_directives(self):
        profile = boot_profile('[pi5]\nkernel=wrong.img\n[pi4]\nkernel=custom.img\n[all]\n')
        self.assertEqual(profile['kernel'], 'custom.img')
        for directive in ('include custom.txt', 'initramfs initrd followkernel',
                          'arm_64bit=0', 'auto_initramfs=1', 'kernel=../escape',
                          'os_prefix=alternate/', '[EDID=unknown]'):
            with self.assertRaises(ValueError, msg=directive):
                boot_profile(directive)

    def test_gzip_validation_detects_truncated_payload(self):
        kernel = self.path / 'compressed.img'
        payload = b'\0' * 56 + b'ARM\x64' + b'\0' * 196
        kernel.write_bytes(gzip.compress(payload))
        validate_kernel(kernel)
        kernel.write_bytes(kernel.read_bytes()[:-5])
        with self.assertRaises(ValueError):
            validate_kernel(kernel)


QEMU_IMG = executable('qemu-img')
QEMU_IO = executable('qemu-io')


@unittest.skipUnless(shutil.which(QEMU_IMG) and shutil.which(QEMU_IO), 'requires QEMU image tools')
class OverlayBootTests(unittest.TestCase):
    def test_refresh_reads_overlay_not_backing_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            base = path / 'base.img'
            base.write_bytes(disk_fixture())
            base_hash = sha256(base)
            (path / 'machine.json').write_text('{}')
            subprocess.run([QEMU_IMG, 'create', '-f', 'qcow2', '-F', 'raw',
                            '-b', str(base), str(path / 'disk.qcow2')], check=True, capture_output=True)
            with WorkspaceLock(path):
                first = refresh_boot(path, QEMU_IMG)
                subprocess.run([QEMU_IO, '-f', 'qcow2', '-c', 'write -P 42 3136 16',
                                str(path / 'disk.qcow2')], check=True, capture_output=True)
                second = refresh_boot(path, QEMU_IMG)
            self.assertNotEqual(first['kernel_sha256'], second['kernel_sha256'])
            self.assertEqual((path / 'kernel8.img').read_bytes()[64:80], b'*' * 16)
            self.assertEqual(sha256(base), base_hash)


if __name__ == '__main__':
    unittest.main()
