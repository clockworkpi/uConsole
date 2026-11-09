import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from forge_derivative_prepare import inputs, prepare
from forge_recovery_derivative import load
from forge_recovery_restore_source import digest
from forge_recovery_filesystems import filesystem_tool
import test_recovery_derivative


class DerivativePreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_derivative.RecoveryDerivativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.fixture.root
        self.backup = self.fixture.fixture.source
        self.source = self.root/'manifest'
        self.pin = digest(self.fixture.original)
        self.output = self.root/'owner-prepared'

    def reviewed(self):
        return inputs(self.backup, self.source, self.pin, self.fixture.image)

    def test_real_root_change_keeps_exact_rollback_lineage(self):
        self.fixture.change(512, b'!')
        reviewed = self.reviewed()
        result = prepare(self.output, reviewed)
        self.assertEqual(result['status'], 'prepared-export-derivative')
        manifest, _ = load(self.output/'derivative', result['manifest_sha256'])
        self.assertEqual(manifest['original_manifest_sha256'], self.pin)
        self.assertEqual(manifest['changed_chunks'], [0])
        self.assertEqual(result['changed_chunks'], 1)
        for field in ('target_contacted', 'target_written', 'target_write_authorized',
                      'normal_boot_release_authorized', 'filesystem_consistency_qualified',
                      'native_boot_qualified', 'lease_acquired'):
            self.assertIs(result[field], False)
        for path in self.output.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        with self.assertRaises(FileExistsError): prepare(self.output, reviewed)

    def test_changed_export_since_review_refused_before_output(self):
        reviewed = self.reviewed()
        self.fixture.change(512)
        with self.assertRaisesRegex(ValueError, 'changed since'): prepare(self.output, reviewed)
        self.assertFalse(self.output.exists())

    def test_wrong_source_pin_and_incomplete_receipt_refused(self):
        with self.assertRaises(ValueError): inputs(self.backup, self.source, '0'*64, self.fixture.image)
        accepted = self.source/'acceptance.json'
        value = json.loads(accepted.read_text())
        value['status'] = 'incomplete'
        accepted.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'completed'): self.reviewed()

    def test_changed_backup_is_not_an_original_source(self):
        plan = self.backup/'plan.json'
        value = json.loads(plan.read_text())
        value['changed'] = True
        plan.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'Backup differs'): self.reviewed()

    def test_public_export_and_symlink_refused(self):
        self.fixture.image.chmod(0o644)
        with self.assertRaises(PermissionError): self.reviewed()
        self.fixture.image.chmod(0o600)
        link = self.root/'linked.img'
        link.symlink_to(self.fixture.image)
        with self.assertRaises(OSError): inputs(self.backup, self.source, self.pin, link)

    def test_overlapping_output_refused(self):
        reviewed = self.reviewed()
        for output in (self.source/'nested', self.backup/'nested', self.root):
            with self.assertRaises(ValueError): prepare(output, reviewed)

    def test_protected_byte_change_leaves_failure_not_outer_acceptance(self):
        self.fixture.change(0)
        with self.assertRaisesRegex(ValueError, 'protected'):
            prepare(self.output, self.reviewed())
        self.assertFalse((self.output/'acceptance.json').exists())
        self.assertTrue((self.output/'failure.json').exists())
        self.assertEqual(json.loads((self.output/'derivative/acceptance.json').read_text())['status'], 'incomplete')

    @unittest.skipUnless(filesystem_tool('e2fsck'), 'Ext4 checker required')
    def test_controller_retains_actual_checker_errors_and_health_pin(self):
        result = prepare(self.output, self.reviewed())
        from forge_controller import Controller
        from forge_recovery_derivative_health import read_health
        controller = Controller({'test': self.root/'workspace'})
        self.addCleanup(controller.close)
        job = controller.check_export_root('test', self.output/'derivative', result['manifest_sha256'], self.root/'health')
        checked = controller.jobs[job['job_id']][2].result(timeout=10)
        self.assertFalse(checked['root_filesystem_consistency_qualified'])
        self.assertFalse(checked['target_contacted'])
        self.assertFalse(checked['lease_acquired'])
        self.assertEqual(controller.grants, frozenset())
        manifest, _ = load(self.output/'derivative', result['manifest_sha256'])
        health = read_health(self.root/'health', checked['health_sha256'], manifest, result['manifest_sha256'])
        self.assertNotEqual(health['root_check_returncode'], 0)
        with self.assertRaises(ValueError): controller.call('recovery_check_export_root', {'workspace': 'test'})

    @unittest.skipUnless(filesystem_tool('e2fsck'), 'Ext4 checker required')
    def test_controller_low_space_has_no_copy_or_grant(self):
        result = prepare(self.output, self.reviewed())
        from forge_controller import Controller
        controller = Controller({'test': self.root/'workspace'})
        self.addCleanup(controller.close)
        with patch('forge_recovery_derivative_health.shutil.disk_usage', return_value=Mock(free=0)):
            job = controller.check_export_root('test', self.output/'derivative', result['manifest_sha256'], self.root/'health')
            with self.assertRaises(OSError): controller.jobs[job['job_id']][2].result(timeout=5)
        self.assertFalse((self.root/'health').exists())
        self.assertEqual(controller.grants, frozenset())

    @unittest.skipUnless(filesystem_tool('e2fsck') and filesystem_tool('mkfs.ext4'), 'Ext4 utilities required')
    def test_controller_real_ext4_success_preserves_export(self):
        root = self.root/'ext4.img'
        with root.open('xb') as stream: stream.truncate(self.fixture.original['root']['bytes'])
        subprocess.run([filesystem_tool('mkfs.ext4'), '-q', '-F', str(root)], check=True, capture_output=True)
        self.fixture.change(512, root.read_bytes())
        before = self.fixture.image.read_bytes()
        result = prepare(self.output, self.reviewed())
        from forge_controller import Controller
        controller = Controller({'test': self.root/'workspace'})
        self.addCleanup(controller.close)
        job = controller.check_export_root('test', self.output/'derivative', result['manifest_sha256'], self.root/'health')
        checked = controller.jobs[job['job_id']][2].result(timeout=10)
        self.assertTrue(checked['root_filesystem_consistency_qualified'])
        self.assertEqual(checked['root_check_returncode'], 0)
        self.assertFalse(checked['boot_filesystem_checked'])
        self.assertFalse(checked['target_write_authorized'])
        self.assertEqual(self.fixture.image.read_bytes(), before)
