import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from forge_recovery_backup import backup,receive,verify_archive


class RecoveryBackupTests(unittest.TestCase):
    def test_preflight_space_rejection_precedes_directory_and_worker_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)/'backup'
            probe = Mock()
            probe.inspect_storage.return_value = {'extent': {
                'device':'/dev/mmcblk0p2', 'length_bytes':1024, 'disk_bytes':4096}}
            with patch('forge_recovery_backup.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
                with self.assertRaisesRegex(OSError, 'Insufficient host space'):
                    backup(probe, destination, 'cid', 'disk', boot_id='boot', whole_card=True)
            self.assertFalse(destination.exists())
            probe._argv.assert_not_called()

    def test_lease_loss_after_streaming_stops_worker_and_retains_received_bytes(self):
        real_popen = subprocess.Popen
        processes = []
        def start(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        calls = 0
        def pulse():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError('renewal lost during transfer')
        producer = [sys.executable, '-c',
                    'import sys,time; sys.stdout.buffer.write(b"partial-stream"); '
                    'sys.stdout.flush(); time.sleep(30)']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'partial.gz'
            with patch('forge_recovery_backup.subprocess.Popen', side_effect=start):
                with self.assertRaisesRegex(RuntimeError, 'renewal lost during transfer'):
                    receive(producer, path, 10000, timeout=241, heartbeat=pulse)
            self.assertEqual(path.read_bytes(), b'partial-stream')
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
            self.assertTrue(processes[0].stdout.closed)
            self.assertTrue(processes[0].stderr.closed)

    def test_extended_transfer_requires_renewal_and_failure_retains_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv, expected = self.producer()
            with self.assertRaises(ValueError):
                receive(argv, root/'denied.gz', 10000, timeout=241)
            pulse = Mock()
            self.assertEqual(receive(argv, root/'allowed.gz', 10000, timeout=241, heartbeat=pulse), expected)
            self.assertGreaterEqual(pulse.call_count, 2)
            fail = Mock(side_effect=RuntimeError('lease lost'))
            with self.assertRaisesRegex(RuntimeError, 'lease lost'):
                receive(argv, root/'partial.gz', 10000, timeout=241, heartbeat=fail)
            self.assertTrue((root/'partial.gz').exists())
            with self.assertRaises(RuntimeError):
                verify_archive(root/'allowed.gz', expected['bytes'], expected['sha256'], heartbeat=fail)

    def test_wrong_lease_binding_rejected_before_storage_inspection(self):
        probe = Mock()
        lease = Mock(probe=object(), boot_id='boot')
        with self.assertRaises(ValueError):
            backup(probe, None, 'cid', 'disk', boot_id='boot', lease=lease)
        probe.inspect_storage.assert_not_called()

    def test_invalid_limit_is_rejected_before_contacting_target(self):
        for limit in (0,-1,True,'4096'):
            with self.subTest(limit=limit),self.assertRaises(ValueError):
                backup(None,None,None,None,boot_id='boot',compressed_limit=limit)
        for selection in (1, 'yes', None, [], {}):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                backup(None, None, None, None, boot_id='boot', whole_card=selection)

    def producer(self, data=b'fixture'*1024, exit_code=0):
        receipt = dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),boot_id='boot')
        source = ('import sys; sys.stdout.buffer.write('+repr(gzip.compress(data))+'); '
                  'sys.stdout.flush(); print('+repr('FORGE_BACKUP_RESULT '+json.dumps(receipt))+
                  ',file=sys.stderr); sys.exit('+str(exit_code)+')')
        return [sys.executable,'-c',source],receipt

    def test_bounded_stream_and_independent_archive_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'root.gz'
            argv,expected = self.producer()
            self.assertEqual(receive(argv,path,10000),expected)
            self.assertEqual(verify_archive(path,expected['bytes'],expected['sha256'])['bytes'],expected['bytes'])
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(ValueError): verify_archive(path,1,expected['sha256'])
            with self.assertRaises(ValueError): verify_archive(path,expected['bytes'],'0'*64)
            with self.assertRaises(FileExistsError): receive(argv,path,10000)

    def test_failure_oversize_and_timeout_retain_incomplete_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv,_ = self.producer(exit_code=1)
            with self.assertRaises(RuntimeError): receive(argv,root/'failed.gz',10000)
            argv,_ = self.producer()
            with self.assertRaises(ValueError): receive(argv,root/'oversized.gz',1)
            with self.assertRaises(TimeoutError):
                receive([sys.executable,'-c','import time; time.sleep(5)'],root/'timeout.gz',10000,timeout=0.1)
            self.assertTrue(all((root/name).exists() for name in ('failed.gz','oversized.gz','timeout.gz')))

    def test_space_guard_stops_before_exhausting_host(self):
        with tempfile.TemporaryDirectory() as directory:
            argv,_ = self.producer()
            with patch('forge_recovery_backup.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
                with self.assertRaises(OSError): receive(argv,Path(directory)/'root.gz',10000)

    def test_manifest_only_verifies_complete_root_not_restore_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            argv,receipt = self.producer()
            probe = SimpleNamespace(nonce='a'*32,kernel='fixture',serial=None,mode='emulated',
                                    inspect_storage=lambda *args,**kwargs: {'extent':{
                                        'length_bytes':receipt['bytes'], 'device':'/dev/mmcblk0p2'}},
                                    _argv=lambda command:argv)
            output = Path(directory)/'backup'
            result = backup(probe,output,'cid','disk',boot_id='boot')
            self.assertEqual(result['status'],'verified-root-backup')
            self.assertFalse(result['restore_authorized'])
            self.assertFalse(result['whole_system_backup'])
            self.assertEqual(json.loads((output/'acceptance.json').read_text()),result)
            argv,_ = self.producer(exit_code=1)
            output = Path(directory)/'failed'
            with self.assertRaises(RuntimeError): backup(probe,output,'cid','disk',boot_id='boot')
            self.assertEqual(json.loads((output/'acceptance.json').read_text())['status'],'incomplete')
            argv,_ = self.producer()
            output = Path(directory)/'capped'
            with self.assertRaisesRegex(ValueError,'compressed size bound'):
                backup(probe,output,'cid','disk',boot_id='boot',compressed_limit=1)
            self.assertEqual(json.loads((output/'plan.json').read_text())['maximum_compressed_bytes'],1)
            self.assertEqual(json.loads((output/'acceptance.json').read_text())['status'],'incomplete')

    def test_whole_card_scope_includes_prefix_without_restore_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            argv, receipt = self.producer(b'partition-table-and-boot-and-root')
            probe = SimpleNamespace(nonce='a'*32, kernel='fixture', serial=None, mode='emulated',
                inspect_storage=lambda *args, **kwargs: {'extent': {
                    'length_bytes':4, 'device':'/dev/mmcblk0p2', 'disk_bytes':receipt['bytes']}},
                _argv=lambda command: argv)
            output = Path(directory)/'card'
            result = backup(probe, output, 'cid', 'disk', boot_id='boot', whole_card=True)
            self.assertEqual(result['status'], 'verified-card-byte-backup')
            self.assertEqual(result['card']['bytes'], receipt['bytes'])
            self.assertFalse(result['restore_authorized'])
            self.assertFalse(result['whole_system_backup'])
            plan = json.loads((output/'plan.json').read_text())
            self.assertEqual(plan['source'], {'device':'/dev/mmcblk0', 'length_bytes':receipt['bytes']})
            self.assertTrue((output/'card.img.gz').is_file())
            self.assertFalse((output/'root.img.gz').exists())
