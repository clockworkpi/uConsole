import json
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from forge_export_capacity import source_capacity, trim_copy
import uconsole_emulator as emulator
from test_forge_filesystem import sample


class ExportCapacityTests(unittest.TestCase):
    def test_prepare_records_private_original_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, workspace = root/'source.img', root/'workspace'
            data = self.fixture(image)[:3072]
            image.write_bytes(data)
            args = emulator.parser().parse_args(['--workspace', str(workspace), 'prepare',
                str(image), '--sha256', hashlib.sha256(data).hexdigest()])
            with patch.object(emulator, 'BootPartition') as boot, \
                    patch.object(emulator, 'patch_strings', return_value=b'fixture-dtb'), \
                    patch.object(emulator.subprocess, 'run') as run:
                boot.return_value.__enter__.return_value.extract.side_effect = \
                    lambda name, destination: destination.write_bytes(b'fixture-boot-file')
                emulator.prepare(args)
            self.assertEqual(json.loads((workspace/'machine.json').read_text())['base_bytes'], 3072)
            self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
            self.assertEqual((workspace/'base.img').stat().st_mode & 0o777, 0o600)
            self.assertEqual(run.call_args.args[0][-1], '4096')
            self.assertEqual(image.read_bytes(), data)

    def test_legacy_capacity_requires_verified_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)/'base.img'
            data = b'x'*3072
            base.write_bytes(data)
            config = dict(base_sha256=hashlib.sha256(data).hexdigest())
            self.assertEqual(source_capacity(config, base), 3072)
            alias = Path(directory)/'shared-base.img'
            alias.symlink_to(base)
            self.assertEqual(source_capacity(config, alias), 3072)
            self.assertIsNone(source_capacity({}, base))
            self.assertEqual(source_capacity(dict(base_bytes=3072), base), 3072)
            with self.assertRaises(ValueError):
                source_capacity(dict(base_bytes=True), base)
            base.write_bytes(b'y'*3072)
            with self.assertRaisesRegex(ValueError, 'pinned checksum'):
                source_capacity(config, base)
            base.unlink()
            with self.assertRaisesRegex(ValueError, 're-import'):
                source_capacity(config, base)

    @unittest.skipUnless(shutil.which('qemu-img'), 'qemu-img required')
    def test_real_qcow2_export_fits_original_non_power_of_two_card(self):
        for occupied in (False, True):
            with self.subTest(occupied=occupied), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                base_bytes = 24576
                original = bytes(sample()) + bytes(base_bytes-8192)
                base = workspace/'base.img'
                base.write_bytes(original)
                (workspace/'machine.json').write_text(json.dumps(dict(schema=1, base_bytes=base_bytes)))
                raw = workspace/'guest.img'
                data = bytearray(original + bytes(32768-base_bytes))
                if occupied:
                    data[-1] = 1
                raw.write_bytes(data)
                subprocess.run(['qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2',
                                str(raw), str(workspace/'disk.qcow2')], check=True, capture_output=True)
                target = workspace/'export.img'
                args = emulator.parser().parse_args(['--workspace', directory, 'export', str(target)])
                args.qemu_img = shutil.which('qemu-img')
                if occupied:
                    with self.assertRaisesRegex(ValueError, 'padding'):
                        emulator.export(args)
                    self.assertFalse(target.exists())
                else:
                    emulator.export(args)
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(base.read_bytes(), original)
                self.assertFalse((workspace/'export.img.partial').exists())

    def fixture(self, path, *, base=3072):
        virtual = 1 << (base-1).bit_length()
        data = bytearray(virtual)
        data[510:512] = b'\x55\xaa'
        data[450], data[466] = 0x0c, 0x83
        struct.pack_into('<II', data, 454, 1, 1)
        struct.pack_into('<II', data, 470, 2, base//512-2)
        data[base-1] = 91
        path.write_bytes(data)
        return bytes(data)

    def test_only_unused_padding_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'copy.img'
            original = self.fixture(path)
            receipt = trim_copy(path, 3072)
            self.assertEqual(path.read_bytes(), original[:3072])
            self.assertEqual(receipt, dict(source_bytes=3072, emulator_bytes=4096, removed_padding_bytes=1024))

    def test_power_of_two_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'copy.img'
            original = self.fixture(path, base=4096)
            self.assertEqual(trim_copy(path, 4096)['removed_padding_bytes'], 0)
            self.assertEqual(path.read_bytes(), original)

    def test_used_padding_and_out_of_bounds_partitions_are_never_trimmed(self):
        for kind in ('data', 'root', 'third', 'extended', 'gpt'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'copy.img'
                data = bytearray(self.fixture(path))
                if kind == 'data':
                    data[-1] = 1
                elif kind == 'root':
                    struct.pack_into('<II', data, 470, 2, 5)
                elif kind == 'third':
                    data[482] = 0x83
                    struct.pack_into('<II', data, 486, 6, 1)
                else:
                    data[466] = 0x0f if kind == 'extended' else 0xee
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    trim_copy(path, 3072)
                self.assertEqual(path.read_bytes(), bytes(data))

    def test_invalid_capacity_and_wrong_export_size_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'copy.img'
            original = self.fixture(path)
            for size in (True, 0, -512, 3073, 8192):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    trim_copy(path, size)
            self.assertEqual(path.read_bytes(), original)

    def test_links_and_metadata_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'copy.img'
            original = self.fixture(path)
            linked = Path(directory)/'link.img'
            linked.symlink_to(path)
            with self.assertRaises(OSError):
                trim_copy(linked, 3072)
            linked.unlink()
            os.link(path, linked)
            with self.assertRaises(ValueError):
                trim_copy(path, 3072)
            linked.unlink()
            pread = os.pread
            def changed(fd, size, offset):
                result = pread(fd, size, offset)
                info = path.stat()
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns+1000000))
                return result
            with patch('forge_export_capacity.os.pread', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'changed during'):
                    trim_copy(path, 3072)
            self.assertEqual(path.read_bytes(), original)

    def test_export_preserves_capacity_and_retains_overlay_on_refusal(self):
        for occupied in (False, True):
            with self.subTest(occupied=occupied), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                (workspace/'machine.json').write_text(json.dumps(dict(schema=1, base_bytes=3072)))
                disk = workspace/'disk.qcow2'
                disk.write_bytes(b'overlay-not-modified')
                target = workspace/'export.img'
                args = emulator.parser().parse_args(['--workspace', directory, 'export', str(target)])
                def convert(argv, **kwargs):
                    partial = Path(argv[-1])
                    self.fixture(partial)
                    if occupied:
                        with partial.open('r+b') as stream:
                            stream.seek(4095)
                            stream.write(b'!')
                with patch.object(emulator.subprocess, 'run', side_effect=convert), \
                        patch('forge_filesystem.check_export_root') as check:
                    if occupied:
                        with self.assertRaisesRegex(ValueError, 'padding'):
                            emulator.export(args)
                        self.assertFalse(target.exists())
                        check.assert_not_called()
                    else:
                        emulator.export(args)
                        self.assertEqual(target.stat().st_size, 3072)
                        check.assert_called_once()
                self.assertFalse((workspace/'export.img.partial').exists())
                self.assertEqual(disk.read_bytes(), b'overlay-not-modified')
