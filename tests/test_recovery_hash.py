import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from forge_recovery_hash import capture, check_receipt
from forge_recovery_backup import receive


class RecoveryHashTests(unittest.TestCase):
    def fixture(self):
        data = b'P'*64 + b'R'*256 + b'S'*32
        extent = dict(disk_bytes=len(data), offset_bytes=64, length_bytes=256)
        receipt = dict(boot_id='12345678-1234-1234-1234-123456789abc',
                       card=dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        for name, offset, length in (('prefix', 0, 64), ('root', 64, 256), ('suffix', 320, 32)):
            receipt[name] = dict(offset=offset, bytes=length,
                                 sha256=hashlib.sha256(data[offset:offset+length]).hexdigest())
        return extent, receipt

    def producer(self, receipt, *, code=0):
        source = ('import sys; print("progress"); print('+repr('FORGE_STORAGE_HASH_RESULT '+json.dumps(receipt))+
                  ',file=sys.stderr); sys.exit('+str(code)+')')
        return [sys.executable, '-c', source]

    def test_receipt_binds_all_ranges_boot_and_digest_shapes(self):
        extent, receipt = self.fixture()
        self.assertEqual(check_receipt(receipt, extent, receipt['boot_id']), receipt)
        for section, field, value in (('prefix', 'offset', 1), ('root', 'bytes', 255),
                                      ('suffix', 'sha256', 'bad'), ('card', 'bytes', True)):
            altered = copy.deepcopy(receipt)
            altered[section][field] = value
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                check_receipt(altered, extent, receipt['boot_id'])
        with self.assertRaises(ValueError):
            check_receipt(receipt, extent, 'different-boot')

    def test_streamed_hash_record_is_not_a_backup_or_write_authority(self):
        extent, receipt = self.fixture()
        probe = SimpleNamespace(nonce='a'*32, kernel='fixture', serial=None, mode='emulated',
            inspect_storage=lambda *a, **kw: dict(extent=extent),
            _argv=lambda command: self.producer(receipt))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'hash'
            result = capture(probe, output, 'cid', 'disk', boot_id=receipt['boot_id'])
            self.assertEqual(result['status'], 'verified-offline-storage-digests')
            self.assertEqual(result['digests'], receipt)
            for field in ('is_backup', 'root_write_authorized', 'normal_boot_release_authorized', 'target_written'):
                self.assertFalse(result[field])
            self.assertEqual((output/'progress.jsonl').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                capture(probe, output, 'cid', 'disk', boot_id=receipt['boot_id'])

    def test_failed_worker_retains_incomplete_evidence(self):
        extent, receipt = self.fixture()
        probe = SimpleNamespace(nonce='a'*32, kernel='fixture', serial=None, mode='emulated',
            inspect_storage=lambda *a, **kw: dict(extent=extent),
            _argv=lambda command: self.producer(receipt, code=1))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'hash'
            with self.assertRaises(RuntimeError):
                capture(probe, output, 'cid', 'disk', boot_id=receipt['boot_id'])
            self.assertEqual(json.loads((output/'acceptance.json').read_text())['status'], 'incomplete')
            self.assertTrue((output/'progress.jsonl').exists())

    def test_wrong_lease_binding_rejected_before_inspection(self):
        probe = Mock()
        with self.assertRaises(ValueError):
            capture(probe, None, None, None, boot_id='boot', lease=Mock(probe=object(), boot_id='boot'))
        probe.inspect_storage.assert_not_called()

    def test_custom_receipt_namespace_is_not_confused_with_backup(self):
        _, receipt = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(receive(self.producer(receipt), path/'hash.log', 1024,
                                    receipt_prefix='FORGE_STORAGE_HASH_RESULT '), receipt)
            with self.assertRaises(ValueError):
                receive(self.producer(receipt), path/'wrong.log', 1024)
            for invalid in ('', 'lowercase ', 'FORGE\nRESULT ', None):
                with self.subTest(prefix=invalid), self.assertRaises(ValueError):
                    receive(None, None, 1024, receipt_prefix=invalid)
