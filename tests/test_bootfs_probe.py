import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from forge_bootfs_probe import script
from validate_recovery_watchdog import run, storage_fixture
import test_ram_identity


class BootFilesystemProbeTests(unittest.TestCase):
    def rejected_guest(self, record, *, cid='a'*32, write_roundtrip=False):
        with patch.object(sys, 'path', list(sys.path)), \
                patch('forge_ram_identity.READER', 'print('+repr(json.dumps(record))+')'), \
                patch('pathlib.Path.read_text', return_value=cid), \
                patch('pathlib.Path.mkdir') as mkdir, \
                patch('subprocess.run') as execute:
            with self.assertRaises(ValueError):
                exec(script('a'*32, '12345678-1234-1234-1234-123456789abc', '6.12.62-v8+', 'a'*32,
                            write_roundtrip=write_roundtrip), {})
            mkdir.assert_not_called()
            execute.assert_not_called()

    def test_physical_session_wrong_boot_and_wrong_card_reject_before_effects(self):
        fixture = test_ram_identity.RamIdentityTests()
        fixture.setUp()
        self.rejected_guest(fixture.record)
        self.rejected_guest(fixture.record, write_roundtrip=True)
        fixture.record['cmdline'] += ' uconsole.emulator=1'
        self.rejected_guest(fixture.record, cid='b'*32)
        fixture.record['boot_id'] = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        self.rejected_guest(fixture.record)

    def test_bad_binding_is_rejected_before_generating_worker(self):
        valid = ['a'*32, '12345678-1234-1234-1234-123456789abc', '6.12.62-v8+', 'a'*32]
        for index, value in ((0, 'bad'), (1, 'bad'), (2, ''), (3, 'bad')):
            arguments = valid.copy()
            arguments[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                script(*arguments)

    def test_boot_probe_requires_explicit_synthetic_storage(self):
        with self.assertRaises(ValueError):
            run(None, None, None, None, None, None, None, boot_filesystem=True)
        with self.assertRaises(ValueError):
            run(None, None, None, None, None, None, None, boot_write_roundtrip=True)
        for invalid in (1, 'yes', None):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                storage_fixture(None, boot_filesystem=invalid)
            with self.subTest(write=invalid), self.assertRaises(ValueError):
                script(None, None, None, None, write_roundtrip=invalid)

    @unittest.skipUnless(shutil.which('mkfs.fat'), 'FAT32 creation tool required')
    def test_fixture_has_real_fat32_and_bounded_distinct_root_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sd.img'
            fixture = storage_fixture(path, boot_filesystem=True)
            self.assertEqual(fixture['bytes'], 128*1024*1024)
            with path.open('rb') as stream:
                header = stream.read(512)
                self.assertEqual(struct.unpack_from('<II', header, 454), (8192, 131072))
                self.assertEqual(struct.unpack_from('<II', header, 470), (139264, 122880))
                stream.seek(8192*512)
                boot = stream.read(512)
                self.assertEqual(boot[82:90], b'FAT32   ')
                stream.seek(fixture['root_offset'])
                marker = b'FORGE DISPOSABLE ROOT EXTENT\n'
                self.assertEqual(stream.read(len(marker)), marker)
            with self.assertRaises(FileExistsError):
                storage_fixture(path, boot_filesystem=True)
