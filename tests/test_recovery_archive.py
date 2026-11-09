import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forge_recovery_archive import materialize


class RecoveryArchiveTests(unittest.TestCase):
    def test_heartbeat_covers_chunks_and_completion_and_failure_stays_incomplete(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = self.fixture(root, data=b'x' * (2*1048576+1))
                calls = []
                def heartbeat():
                    calls.append(True)
                    if fail and len(calls) == 2:
                        raise RuntimeError('Lease renewal failed')
                if fail:
                    with self.assertRaisesRegex(RuntimeError, 'Lease renewal'):
                        materialize(source, root/'out', heartbeat=heartbeat)
                    self.assertEqual(json.loads((root/'out/acceptance.json').read_text())['status'], 'incomplete')
                    self.assertLess((root/'out/image.img').stat().st_size, 2*1048576+1)
                else:
                    self.assertEqual(materialize(source, root/'out', heartbeat=heartbeat)['status'], 'verified-host-image')
                    self.assertEqual(len(calls), 4)

    def test_invalid_heartbeat_rejected_before_access(self):
        with self.assertRaisesRegex(ValueError, 'heartbeat'):
            materialize('/missing-backup', '/missing-output', heartbeat=True)

    def fixture(self, root, data=b'whole-card-fixture', *, card=True):
        source = root / 'backup'
        source.mkdir(mode=0o700)
        name = 'card' if card else 'root'
        payload = gzip.compress(data)
        records = {'plan.json': {'backup_kind': 'whole-card-bytes' if card else 'root-partition-bytes',
                                'source': {'length_bytes': len(data)}},
                   'acceptance.json': {'status': 'verified-card-byte-backup' if card else 'verified-root-backup',
                                       name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
                                       'compressed_bytes': len(payload)}}
        for filename, record in records.items():
            (source / filename).write_text(json.dumps(record))
            (source / filename).chmod(0o600)
        (source / (name + '.img.gz')).write_bytes(payload)
        (source / (name + '.img.gz')).chmod(0o600)
        return source

    def test_both_scopes_private_exclusive_and_no_restore_authority(self):
        for card in (True, False):
            with self.subTest(card=card), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = self.fixture(root, card=card)
                destination = root / 'extracted'
                record = materialize(source, destination)
                self.assertEqual(record['status'], 'verified-host-image')
                for field in ('target_written', 'restore_authorized', 'filesystem_consistency_qualified'):
                    self.assertFalse(record[field])
                self.assertEqual((destination / 'image.img').read_bytes(), b'whole-card-fixture')
                self.assertEqual((destination / 'image.img').stat().st_mode & 0o777, 0o600)
                self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
                with self.assertRaises(FileExistsError):
                    materialize(source, destination)

    def test_incomplete_and_wrong_scope_rejected_before_output(self):
        for change in ({'status': 'incomplete'}, {'card': {'bytes': True, 'sha256': '0'*64}},
                       {'status': 'verified-root-backup'}, {'compressed_bytes': True}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = self.fixture(root)
                path = source / 'acceptance.json'
                record = json.loads(path.read_text())
                record.update(change)
                path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    materialize(source, root / 'out')
                self.assertFalse((root / 'out').exists())

    def test_wrong_hash_retains_incomplete_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            path = source / 'acceptance.json'
            record = json.loads(path.read_text())
            record['card']['sha256'] = '0'*64
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                materialize(source, root / 'out')
            self.assertEqual(json.loads((root / 'out/acceptance.json').read_text())['status'], 'incomplete')
            self.assertTrue((root / 'out/image.img').exists())

    def test_oversize_gzip_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            payload = gzip.compress(b'oversized'*100000)
            (source / 'card.img.gz').write_bytes(payload)
            path = source / 'acceptance.json'
            record = json.loads(path.read_text())
            record['compressed_bytes'] = len(payload)
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'expands beyond'):
                materialize(source, root / 'out')
            self.assertLessEqual((root / 'out/image.img').stat().st_size, len(b'whole-card-fixture'))

    def test_archive_links_and_public_permissions_rejected(self):
        for mode in ('symlink', 'hardlink', 'public'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = self.fixture(root)
                path = source / 'card.img.gz'
                if mode == 'symlink':
                    path.rename(source / 'real.gz')
                    path.symlink_to('real.gz')
                elif mode == 'hardlink':
                    os.link(path, source / 'other.gz')
                else:
                    path.chmod(0o644)
                with self.assertRaises(OSError):
                    materialize(source, root / 'out')
                self.assertFalse((root / 'out').exists())

    def test_space_preflight_precedes_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            with patch('forge_recovery_archive.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
                with self.assertRaises(OSError):
                    materialize(source, root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_space_loss_retains_incomplete_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            with patch('forge_recovery_archive.shutil.disk_usage', side_effect=[
                    SimpleNamespace(free=10**10), SimpleNamespace(free=0)]):
                with self.assertRaises(OSError):
                    materialize(source, root / 'out')
            self.assertEqual((root / 'out/image.img').stat().st_size, 0)
            self.assertEqual(json.loads((root / 'out/acceptance.json').read_text())['status'], 'incomplete')

    def test_corrupt_archive_never_produces_success_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            path = source / 'card.img.gz'
            data = bytearray(path.read_bytes())
            data[-8] ^= 1  # Same size, incorrect gzip CRC.
            path.write_bytes(data)
            with self.assertRaises(gzip.BadGzipFile):
                materialize(source, root / 'out')
            self.assertEqual(json.loads((root / 'out/acceptance.json').read_text())['status'], 'incomplete')

    def test_source_mutation_is_rejected_even_with_identical_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            archive = source / 'card.img.gz'
            def space(path):
                if Path(path) == root / 'out':
                    # Mutate metadata after the initial descriptor observation.
                    observed = archive.stat()
                    os.utime(archive, ns=(observed.st_atime_ns, observed.st_mtime_ns + 1000000))
                return SimpleNamespace(free=10**10)
            with patch('forge_recovery_archive.shutil.disk_usage', side_effect=space):
                with self.assertRaisesRegex(ValueError, 'changed during extraction'):
                    materialize(source, root / 'out')
            self.assertEqual(json.loads((root / 'out/acceptance.json').read_text())['status'], 'incomplete')
