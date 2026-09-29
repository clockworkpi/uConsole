import copy
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from forge_recovery_derivative_health import check_health, inspect_root, read_health
from forge_recovery_derivative import load, prepare
from forge_recovery_restore_source import digest
from forge_recovery_filesystems import filesystem_tool
import test_recovery_derivative


class DerivativeHealthTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_derivative.RecoveryDerivativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.change(512, b'enhanced')
        result = self.fixture.run_prepare()
        self.pin = result['manifest_sha256']
        self.destination = self.fixture.fixture.root/'health'
        self.which = patch('forge_recovery_derivative_health.shutil.which', return_value='/usr/sbin/e2fsck')
        self.which.start()
        self.addCleanup(self.which.stop)
        self.proc = patch('forge_recovery_derivative_health.Path.is_dir', return_value=True)
        self.proc.start()
        self.addCleanup(self.proc.stop)

    def run_check(self, **kwargs):
        return inspect_root(self.fixture.output, self.pin, self.destination, **kwargs)

    def acceptance(self):
        return json.loads((self.destination/'acceptance.json').read_text())

    def test_exact_private_copy_readonly_checker_and_no_authority(self):
        pulses = []
        def checker(argv, fd, **kwargs):
            self.assertEqual(argv, ['/usr/sbin/e2fsck', '-f', '-n'])
            with self.assertRaises(OSError):
                os.write(fd, b'no writes')
            self.assertEqual(os.read(fd, 8), b'enhanced')
            return dict(argv=argv, returncode=0, output='clean')
        with patch('forge_recovery_derivative_health.run_check', side_effect=checker):
            result = self.run_check(heartbeat=lambda: pulses.append(True))
        self.assertEqual(result, self.acceptance())
        self.assertTrue(result['root_filesystem_consistency_qualified'])
        for field in ('boot_filesystem_checked', 'repair_performed', 'target_written',
                      'target_write_authorized', 'normal_boot_release_authorized'):
            self.assertIs(result[field], False)
        self.assertEqual((self.destination/'root.img').read_bytes(), self.fixture.image.read_bytes()[512:-512])
        self.assertEqual((self.destination/'root.img').stat().st_mode & 0o777, 0o600)
        self.assertGreater(len(pulses), 2)
        with self.assertRaises(FileExistsError):
            self.run_check()

    def test_nonclean_is_retained_not_repaired_or_qualified(self):
        with patch('forge_recovery_derivative_health.run_check', return_value=dict(returncode=4, output='errors')):
            result = self.run_check()
        self.assertEqual(result['status'], 'checked-derivative-root')
        self.assertFalse(result['root_filesystem_consistency_qualified'])
        self.assertEqual(result['root_check_returncode'], 4)

    def test_changed_source_never_invokes_checker(self):
        self.fixture.change(512, b'bad')
        with patch('forge_recovery_derivative_health.run_check') as checker:
            with self.assertRaises(ValueError):
                self.run_check()
        checker.assert_not_called()
        self.assertEqual(self.acceptance()['status'], 'incomplete')

    def test_lease_failure_never_invokes_checker(self):
        with patch('forge_recovery_derivative_health.run_check') as checker:
            with self.assertRaisesRegex(RuntimeError, 'lease failed'):
                self.run_check(heartbeat=Mock(side_effect=RuntimeError('lease failed')))
        checker.assert_not_called()
        self.assertEqual(self.acceptance()['status'], 'incomplete')

    def test_checker_timeout_retains_incomplete_evidence(self):
        with patch('forge_recovery_derivative_health.run_check', side_effect=TimeoutError('deadline')):
            with self.assertRaises(TimeoutError):
                self.run_check()
        self.assertEqual(self.acceptance()['status'], 'incomplete')

    def test_root_copy_mutation_is_not_qualified(self):
        def checker(*args, **kwargs):
            path = self.destination/'root.img'
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns+1000000))
            return dict(returncode=0)
        with patch('forge_recovery_derivative_health.run_check', side_effect=checker):
            with self.assertRaisesRegex(ValueError, 'changed during checking'):
                self.run_check()
        self.assertEqual(self.acceptance()['status'], 'incomplete')

    def test_abnormal_checker_exit_is_incomplete(self):
        with patch('forge_recovery_derivative_health.run_check', return_value=dict(returncode=-9)):
            with self.assertRaisesRegex(ValueError, 'exit normally'):
                self.run_check()
        self.assertEqual(self.acceptance()['status'], 'incomplete')

    def test_low_space_refuses_before_destination_creation(self):
        with patch('forge_recovery_derivative_health.shutil.disk_usage', return_value=Mock(free=0)):
            with self.assertRaises(OSError):
                self.run_check()
        self.assertFalse(self.destination.exists())

    def test_copy_replacement_before_checker_is_rejected(self):
        from forge_recovery_derivative_health import write_record
        def record(fd, name, value):
            write_record(fd, name, value)
            if name == 'source-stream.json':
                path = self.destination/'root.img'
                path.rename(self.destination/'retained-root.img')
                path.write_bytes(b'x'*self.fixture.original['root']['bytes'])
                path.chmod(0o600)
        with patch('forge_recovery_derivative_health.write_record', side_effect=record):
            with patch('forge_recovery_derivative_health.run_check') as checker:
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    self.run_check()
        checker.assert_not_called()
        self.assertEqual(self.acceptance()['status'], 'incomplete')

    def health_fixture(self):
        with patch('forge_recovery_derivative_health.run_check',
                   return_value=dict(argv=['/usr/sbin/e2fsck', '-f', '-n'], returncode=0, output='clean')):
            result = self.run_check()
        manifest, _ = load(self.fixture.output, self.pin)
        return result, manifest

    def test_health_reader_allows_disposable_copy_cleanup(self):
        value, manifest = self.health_fixture()
        (self.destination/'root.img').unlink()
        self.assertEqual(read_health(self.destination, digest(value), manifest, self.pin), value)

    def test_health_pin_identity_and_strict_boolean_types(self):
        value, manifest = self.health_fixture()
        with self.assertRaisesRegex(ValueError, 'pinned evidence'):
            check_health(value, '0'*64, manifest, self.pin)
        for field, replacement in (('derivative_manifest_sha256', '0'*64),
                ('target_write_authorized', 0), ('root_filesystem_consistency_qualified', 1),
                ('root_check_returncode', False), ('root_check_returncode', 4),
                ('status', 'incomplete'), ('boot_filesystem_checked', True),
                ('image', dict(manifest['card'], sha256='0'*64))):
            with self.subTest(field=field, replacement=replacement):
                changed = dict(value, **{field: replacement})
                with self.assertRaises(ValueError):
                    check_health(changed, digest(changed), manifest, self.pin)

    def test_health_reader_rejects_changed_supporting_records(self):
        value, manifest = self.health_fixture()
        for name, field, replacement in (
                ('source-stream.json', 'target_restore_verified', True),
                ('plan.json', 'command', ['/usr/sbin/e2fsck', '-f', '-y']),
                ('root-check.json', 'returncode', False)):
            path = self.destination/name
            original = json.loads(path.read_text())
            changed = copy.deepcopy(original)
            changed[field] = replacement
            path.write_text(json.dumps(changed))
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_health(self.destination, digest(value), manifest, self.pin)
            path.write_text(json.dumps(original))

    @unittest.skipUnless((sys.platform.startswith('linux') or sys.platform == 'darwin') and
                         filesystem_tool('mkfs.ext4') and filesystem_tool('e2fsck'), 'Ext4 utilities required')
    def test_real_readonly_ext4_check_of_qualified_derivative(self):
        self.which.stop()
        self.proc.stop()
        root = self.fixture.fixture.root/'ext4.img'
        with root.open('xb') as output:
            output.truncate(self.fixture.original['root']['bytes'])
        subprocess.run([filesystem_tool('mkfs.ext4'), '-q', '-F', str(root)], check=True, capture_output=True)
        self.fixture.change(512, root.read_bytes())
        self.fixture.output = self.fixture.fixture.root/'ext4-derivative'
        result = prepare(self.fixture.fixture.source, self.fixture.original,
                         digest(self.fixture.original), self.fixture.image, self.fixture.output)
        self.pin = result['manifest_sha256']
        before = self.fixture.image.read_bytes()
        checked = self.run_check()
        self.assertTrue(checked['root_filesystem_consistency_qualified'])
        self.assertEqual(checked['root_check_returncode'], 0)
        self.assertEqual(before, self.fixture.image.read_bytes())
        self.assertIn('Pass 5', json.loads((self.destination/'root-check.json').read_text())['output'])
