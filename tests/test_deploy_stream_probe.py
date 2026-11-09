import hashlib
import copy
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock

from forge_deploy_stream_probe import check_deployed, make_derivative, partial_root, run
from forge_recovery_restore_bootstrap import BOOTSTRAP, framed
from forge_restore_stream_probe import packet
from validate_recovery_watchdog import run as validate
import test_recovery_restore_source
import test_recovery_derivative


class DeployStreamProbeTests(unittest.TestCase):
    def test_interruption_requires_distinct_stream_trial(self):
        for options in ({}, {'root_deploy_stream': True, 'root_deploy_lost_completion': True}):
            with self.assertRaisesRegex(ValueError, 'Deployment interruption'):
                validate(None, None, None, None, None, None, None, root_deploy_interruption=True, **options)
        with self.assertRaisesRegex(ValueError, 'Deployment interruption'):
            validate(None, None, None, None, None, None, None, root_deploy_interruption=1)

    def test_partial_root_uses_first_derived_chunk_and_remaining_original_chunks(self):
        fixture = test_recovery_derivative.RecoveryDerivativeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.change(512, b'first-change')
        fixture.change(512+4*1024*1024, b'second-change')
        accepted = fixture.run_prepare()
        original = fixture.fixture
        result = partial_root(original.source, fixture.original, fixture.baseline['manifest_sha256'],
                              fixture.output, accepted['manifest_sha256'])
        expected = b'first-change'+original.root_bytes[len(b'first-change'):]
        self.assertEqual(result['sha256'], hashlib.sha256(expected).hexdigest())
        self.assertNotEqual(result['sha256'], accepted['root']['sha256'])
        self.assertNotEqual(result['sha256'], fixture.original['root']['sha256'])

    def test_deployed_observation_requires_new_card_and_root_but_same_protected_ranges(self):
        original = dict(status='verified-offline-storage-digests', digests=dict(
            boot_id='boot', root={'sha256': 'old-root'}, card={'sha256': 'old-card'},
            prefix={'sha256': 'prefix'}, suffix={'sha256': 'suffix'}))
        saved = copy.deepcopy(original)
        manifest = dict(root={'sha256': 'new-root'}, card={'sha256': 'new-card'})
        observed = copy.deepcopy(original)
        observed['digests'].update(manifest)
        check_deployed(original, observed, manifest)
        self.assertEqual(original, saved)
        for name in ('card', 'root', 'prefix', 'suffix', 'boot_id'):
            changed = copy.deepcopy(observed)
            changed['digests'][name] = original['digests'][name] if name in manifest else 'changed'
            with self.assertRaises(ValueError):
                check_deployed(original, changed, manifest)

    def test_requires_distinct_full_config_transport(self):
        for options in ({}, {'boot_commit_transport': True, 'root_restore_stream': True},
                        {'boot_commit_transport': True, 'boot_commit_reboot': True},
                        {'boot_commit_transport': True, 'boot_commit_interruption': True}):
            with self.assertRaisesRegex(ValueError, 'Root deployment stream'):
                validate(None, None, None, None, None, None, None, root_deploy_stream=True, **options)
        with self.assertRaisesRegex(ValueError, 'Root deployment stream'):
            validate(None, None, None, None, None, None, None, root_deploy_stream=1)
        for value in (True, 1, 'yes'):
            with self.assertRaisesRegex(ValueError, 'Deployment completion loss'):
                validate(None, None, None, None, None, None, None, root_deploy_lost_completion=value)

    def test_physical_binding_rejected_before_any_files_or_transport(self):
        probe = Mock()
        with self.assertRaises(ValueError):
            run(probe, None, dict(mode='physical'), None, None, None, None)
        self.assertEqual(probe.mock_calls, [])

    def test_derivative_worker_bootstrap_still_rejects_physical_target(self):
        request = dict(plan=dict(binding=dict(mode='emulated', serial=None, device='/dev/mmcblk1',
                            extent=dict(disk_bytes=128*1024*1024))))
        data, _ = packet(request)
        value = json.loads(data)
        value['request'].update(fixture_action='restore', fixture_lease=None)
        value['request']['plan'].update(kind='guarded-derived-root-deploy-plan', operation='deploy-derived-root')
        value['request']['plan']['binding']['mode'] = 'physical'
        data = json.dumps(value).encode()
        pin = hashlib.sha256(data).hexdigest()
        bootstrap = BOOTSTRAP.replace('from forge_recovery_restore_worker import run',
                                     'from forge_restore_stream_fixture import run')
        result = subprocess.run([sys.executable, '-I', '-S', '-c', bootstrap, pin],
                                input=framed(data, pin), capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Restore fixture requires a pinned small disposable emulator card', result.stderr)

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('mkfs.ext4') and shutil.which('e2fsck'),
                         'Requires real Linux ext4 tools')
    def test_disposable_derivative_has_real_clean_ext4_and_retained_backup_lineage(self):
        fixture = test_recovery_restore_source.RestoreSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        accepted, original = fixture.prepared()
        before = (fixture.source/'card.img.gz').read_bytes()
        pulse = Mock()
        derived, pin, health = make_derivative(fixture.source, original, accepted['manifest_sha256'],
                                                fixture.root, pulse)
        self.assertTrue(health['root_filesystem_consistency_qualified'])
        self.assertEqual(health['root_check_returncode'], 0)
        self.assertEqual(derived['original_manifest'], original)
        self.assertEqual(health['derivative_manifest_sha256'], pin)
        self.assertTrue(derived['changed_chunks'])
        self.assertFalse(health['target_written'])
        self.assertEqual((fixture.source/'card.img.gz').read_bytes(), before)
        self.assertGreater(pulse.call_count, 5)


if __name__ == '__main__':
    unittest.main()
