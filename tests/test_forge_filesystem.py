from pathlib import Path
import struct
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_filesystem import check_export_root, check_overlay_root, crc32c
from forge_workspace import WorkspaceLock
import uconsole_emulator as emulator


def sample(state=1, incompat=0x40, ro_compat=0x400, orphan=0):
    data = bytearray(8192)
    data[510:512] = b'\x55\xaa'
    data[466] = 0x83
    struct.pack_into('<II', data, 470, 8, 8)
    block = bytearray(1024)
    struct.pack_into('<HH', block, 0x38, 0xef53, state)
    struct.pack_into('<II', block, 0x60, incompat, ro_compat)
    struct.pack_into('<I', block, 0xe8, orphan)
    block[0x175] = 1
    struct.pack_into('<I', block, 0x3fc, crc32c(block[:0x3fc]))
    data[5120:6144] = block
    return data


class FilesystemTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('qemu-img'), 'needs qemu-img')
    def test_real_qcow2_metadata_read(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            raw = workspace / 'fixture.img'
            raw.write_bytes(sample())
            subprocess.run(['qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2',
                            str(raw), str(workspace / 'disk.qcow2')], check=True, capture_output=True)
            self.assertEqual(check_overlay_root(workspace, 'qemu-img'), check_export_root(raw))

    def test_overlay_reads_only_header_and_superblock_and_rejects_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            payload = sample()
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                self.assertNotIn('--force-share', command)
                if command[1] == 'info':
                    return SimpleNamespace(stdout='{"virtual-size":8192}')
                fields = dict(item.split('=', 1) for item in command if '=' in item)
                start, count = int(fields['skip']), int(fields['count'])
                Path(fields['of']).write_bytes(payload[start * 512:count * 512])
            with patch('forge_filesystem.subprocess.run', side_effect=run):
                self.assertTrue(check_overlay_root(workspace, 'qemu-img')['clean_state'])
                self.assertEqual(len(calls), 3)
                self.assertIn('count=1', calls[1])
                self.assertIn('count=12', calls[2])
                self.assertIn('skip=10', calls[2])
                payload = sample(state=0)
                with self.assertRaisesRegex(ValueError, 'unclean'):
                    check_overlay_root(workspace, 'qemu-img')
                calls.clear()
                with WorkspaceLock(workspace), self.assertRaisesRegex(ValueError, 'busy'):
                    check_overlay_root(workspace, 'qemu-img')
                self.assertEqual(calls, [])

    def test_clean_superblock_and_crc_vector(self):
        self.assertEqual(crc32c(b'123456789'), 0x1cf96d7c)
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'disk.img'
            for flags in (0, 0x400):
                payload = sample(ro_compat=flags)
                image.write_bytes(payload)
                result = check_export_root(image)
                self.assertTrue(result['clean_state'])
                self.assertEqual(result['superblock_checksum_checked'], bool(flags))
                self.assertFalse(result['full_filesystem_check'])
                self.assertEqual(image.read_bytes(), payload)

    def test_recovery_and_corrupt_inputs_rejected(self):
        invalid = [sample(state=0), sample(state=3), sample(state=5),
                   sample(incompat=0x44), sample(orphan=17),
                   sample(ro_compat=0x10400), sample(incompat=0),
                   sample()[:6000], b'not an image']
        bad_checksum = sample()
        bad_checksum[5120 + 0x30] ^= 1
        invalid.append(bad_checksum)
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'disk.img'
            for payload in invalid:
                image.write_bytes(payload)
                with self.assertRaises(ValueError):
                    check_export_root(image)
                self.assertEqual(image.read_bytes(), payload)

    def test_export_checks_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / 'machine.json').write_text('{}')
            (workspace / 'disk.qcow2').write_bytes(b'preserve source')
            target = workspace / 'export.img'
            args = emulator.parser().parse_args(['--workspace', directory, 'export', str(target)])
            for state in (3, 1):
                payload = sample(state=state)
                def convert(command, **kwargs):
                    Path(command[-1]).write_bytes(payload)
                with patch.object(emulator.subprocess, 'run', side_effect=convert):
                    if state != 1:
                        with self.assertRaisesRegex(ValueError, 'unclean'):
                            emulator.export(args)
                        self.assertFalse(target.exists())
                    else:
                        emulator.export(args)
                        self.assertEqual(target.read_bytes(), payload)
                self.assertFalse(target.with_name('export.img.partial').exists())
                self.assertEqual((workspace / 'disk.qcow2').read_bytes(), b'preserve source')


if __name__ == '__main__':
    unittest.main()
