import json
import gzip
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forge_backup_source import check_filesystems, inputs, prepare
from forge_recovery_restore_source import digest, validate
import test_recovery_archive_hash
import test_recovery_filesystems


class BackupSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = test_recovery_archive_hash.RecoveryArchiveHashTests()
        self.backup = self.fixture.fixture(self.root)
        self.reviewed = inputs(self.backup, digest(self.fixture.accepted))
        self.output = self.root/'prepared'

    def test_real_archive_to_verified_chunk_source_without_card_copy(self):
        result = prepare(self.output, self.reviewed)
        self.assertEqual(result['status'], 'prepared-backup-root-source')
        manifest = json.loads((self.output/'source/manifest.json').read_text())
        validate(manifest, result['manifest_sha256'])
        self.assertEqual(manifest['backup_plan_sha256'], self.reviewed['plan_sha256'])
        self.assertEqual(manifest['root'], result['root'])
        self.assertEqual(result['chunk_count'], len(manifest['chunks']))
        for key in ('target_contacted', 'target_written', 'lease_acquired',
                    'filesystem_consistency_qualified', 'restore_authorized',
                    'normal_boot_release_authorized'):
            self.assertIs(result[key], False)
        self.assertEqual(list(self.output.rglob('*.img')), [])
        for path in self.output.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        with self.assertRaises(FileExistsError):
            prepare(self.output, self.reviewed)

    def test_wrong_pin_and_changed_review_rejected_without_output(self):
        with self.assertRaises(ValueError): inputs(self.backup, '0'*64)
        self.fixture.plan['unexpected_change'] = True
        self.fixture.save(self.backup/'plan.json', self.fixture.plan)
        with self.assertRaises(ValueError): prepare(self.output, self.reviewed)
        self.assertFalse(self.output.exists())

    def test_public_or_replaced_archive_rejected(self):
        (self.backup/'card.img.gz').chmod(0o644)
        with self.assertRaises(PermissionError): prepare(self.output, self.reviewed)
        self.assertFalse(self.output.exists())

    def test_output_overlap_rejected(self):
        for path in (self.backup, self.backup/'nested', self.root):
            with self.assertRaises(ValueError): prepare(path, self.reviewed)

    def test_corrupt_archive_retains_failure_and_no_outer_acceptance(self):
        archive = self.backup/'card.img.gz'
        payload = bytearray(archive.read_bytes())
        payload[-8] ^= 1
        archive.write_bytes(payload)
        self.reviewed = inputs(self.backup, digest(self.fixture.accepted))
        with self.assertRaises(OSError): prepare(self.output, self.reviewed)
        self.assertTrue((self.output/'failure.json').exists())
        self.assertTrue((self.output/'ranges/acceptance.json').exists())
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_mutation_between_scans_is_not_accepted(self):
        from forge_backup_source import capture
        def changed(*args, **kwargs):
            result = capture(*args, **kwargs)
            self.fixture.plan['changed'] = True
            self.fixture.save(self.backup/'plan.json', self.fixture.plan)
            return result
        with patch('forge_backup_source.capture', side_effect=changed):
            with self.assertRaises(ValueError): prepare(self.output, self.reviewed)
        self.assertFalse((self.output/'acceptance.json').exists())
        self.assertTrue((self.output/'failure.json').exists())


@unittest.skipUnless(test_recovery_filesystems.HOST_CHECKERS, 'Filesystem checkers required')
class BackupFilesystemPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, *, real=False):
        fixture = test_recovery_filesystems.RecoveryFilesystemTests()
        image, backup, plan, _, size = fixture.fixture(self.root, real=real)
        plan['source'] = dict(device=plan['device'], length_bytes=size)
        archive = backup/'card.img.gz'
        with (image/'image.img').open('rb') as source, gzip.open(archive, 'wb') as output:
            shutil.copyfileobj(source, output)
        archive.chmod(0o600)
        receipt = json.loads((backup/'acceptance.json').read_text())
        receipt['compressed_bytes'] = archive.stat().st_size
        (backup/'plan.json').write_text(json.dumps(plan))
        (backup/'acceptance.json').write_text(json.dumps(receipt))
        return backup, inputs(backup, digest(receipt))

    def test_low_space_and_missing_tools_refuse_before_output(self):
        _, reviewed = self.fixture()
        output = self.root/'checked'
        with patch('forge_backup_source.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(OSError, 'free bytes'): check_filesystems(output, reviewed)
        with patch('forge_backup_source.filesystem_tool', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'make deps'): check_filesystems(output, reviewed)
        self.assertFalse(output.exists())

    def test_nonzero_checks_are_retained_not_health_or_authority(self):
        backup, reviewed = self.fixture()
        before = (backup/'card.img.gz').read_bytes()
        result = check_filesystems(self.root/'checked', reviewed)
        self.assertEqual(result['status'], 'checked-backup-filesystems')
        self.assertFalse(result['filesystem_consistency_qualified'])
        self.assertTrue(any(result['check_returncodes'].values()))
        self.assertEqual((backup/'card.img.gz').read_bytes(), before)
        for key in ('repair_performed', 'target_contacted', 'target_written', 'lease_acquired',
                    'restore_authorized', 'normal_boot_release_authorized'):
            self.assertIs(result[key], False)
        health = json.loads((self.root/'checked/health/acceptance.json').read_text())
        self.assertEqual(digest(health), result['health_sha256'])
        with self.assertRaises(FileExistsError): check_filesystems(self.root/'checked', reviewed)

    def test_failed_checker_retains_partial_copies_and_no_outer_acceptance(self):
        _, reviewed = self.fixture()
        output = self.root/'checked'
        with patch('forge_recovery_filesystems.run_check', side_effect=RuntimeError('checker failed')):
            with self.assertRaises(RuntimeError): check_filesystems(output, reviewed)
        self.assertTrue((output/'failure.json').exists())
        self.assertTrue((output/'image/image.img').exists())
        self.assertTrue((output/'health/root.img').exists())
        self.assertFalse((output/'acceptance.json').exists())

    @unittest.skipUnless(test_recovery_filesystems.filesystem_tool('mkfs.fat') and
                         test_recovery_filesystems.filesystem_tool('mke2fs'), 'Filesystem makers required')
    def test_real_fat_ext4_checks_match_restore_health_contract(self):
        _, reviewed = self.fixture(real=True)
        result = check_filesystems(self.root/'checked', reviewed)
        self.assertEqual(result['check_returncodes'], dict(boot=0, root=0))
        self.assertTrue(result['filesystem_consistency_qualified'])
        from forge_recovery_restore_dispatch import read_health
        health = read_health(self.root/'checked/health', result['health_sha256'], dict(card=reviewed['card']))
        self.assertTrue(health['filesystem_consistency_qualified'])
