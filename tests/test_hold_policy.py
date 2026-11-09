import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from forge_hold_policy import inputs, prepare
from forge_recovery_bootplan import digest
from forge_recovery_jobs import RecoveryJobs
from forge_recovery_session import Session
from forge_recovery_source_contract import BACKUP, DERIVATIVE
from forge_ram_transport import RecoveryProbe
from forge_target_journal import write_record
import test_backup_policy
import test_recovery_restore_source


class HoldPolicyTests(unittest.TestCase):
    def setUp(self):
        self.enrollment = test_backup_policy.BackupPolicyTests()
        self.enrollment.setUp()
        self.addCleanup(self.enrollment.doCleanups)
        self.source = self.enrollment.source
        self.root = self.enrollment.root
        self.staging = self.enrollment.enrollment.stage
        self.staging_pin = self.enrollment.accepted['staging_sha256']
        self.original = test_recovery_restore_source.RestoreSourceTests()
        self.original.setUp()
        self.addCleanup(self.original.doCleanups)
        self.receipt, self.manifest = self.original.prepared()
        self.manifest_dir = self.original.root/'manifest'
        self.manifest_pin = digest(self.manifest)
        self.hashes = self.root/'hashes'
        self.hashes.mkdir(mode=0o700)
        root = self.manifest['root']
        length, start, size = self.manifest['card']['bytes'], root['offset'], root['bytes']
        self.plan = dict(nonce=self.source.probe.nonce, kernel=self.source.probe.kernel, serial=self.source.probe.serial,
            mode='physical', boot_id=self.source.boot_id, lease_owner=self.source.owner,
            cid='a'*32, disk_id='21965b0c', device='/dev/mmcblk0',
            extent=dict(disk_bytes=length, offset_bytes=start, length_bytes=size))
        self.observation = dict(status='verified-offline-storage-digests', mode='physical', is_backup=False,
            root_write_authorized=False, normal_boot_release_authorized=False, target_written=False,
            digests=dict(boot_id=self.source.boot_id, card=self.manifest['card'], root=copy.deepcopy(root),
                prefix=dict(offset=0, bytes=start, sha256=hashlib.sha256(self.original.data[:start]).hexdigest()),
                suffix=dict(offset=start+size, bytes=length-start-size,
                            sha256=hashlib.sha256(self.original.data[start+size:]).hexdigest())))
        self.save(self.hashes/'plan.json', self.plan)
        self.save(self.hashes/'acceptance.json', self.observation)
        self.output = self.root/'hold-policy'

    def save(self, path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def reviewed(self, kind=BACKUP):
        return inputs(self.source, self.staging, self.staging_pin, self.hashes, digest(self.observation),
                      self.manifest_dir, self.manifest_pin, kind)

    def test_offline_consumable_install_only_plan_and_policy(self):
        reviewed = self.reviewed()
        with patch.object(RecoveryProbe, '_observe') as remote, patch.object(Session, 'renew') as renew:
            result = prepare(self.output, self.source, reviewed, 'fixture')
        remote.assert_not_called()
        renew.assert_not_called()
        self.assertEqual(result['status'], 'prepared-hold-policy-not-approved')
        registry = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.root/'workspace'})
        self.assertEqual(set(registry.jobs), {'hold'})
        job = registry.get('hold', 'fixture')
        self.assertEqual(job.operation, 'install-hold')
        self.assertEqual(job.boot_id, self.source.boot_id)
        self.assertEqual(job.arguments['root_sha256'], self.manifest['root']['sha256'])
        self.assertEqual(job.arguments['plan_sha256'], result['plan_sha256'])
        self.assertFalse((self.output/'hold/commit-attempt').exists())
        for flag in ('target_written', 'lease_acquired', 'policy_approved', 'hold_installed',
                     'root_write_authorized', 'normal_boot_release_authorized'):
            self.assertIs(result[flag], False)
        self.assertNotIn(self.source.owner, json.dumps(result))
        for path in self.output.rglob('*'): self.assertEqual(path.stat().st_mode & 0o077, 0)
        with self.assertRaises(FileExistsError): prepare(self.output, self.source, reviewed, 'fixture')

    def test_different_observed_root_cannot_be_adopted(self):
        self.observation['digests']['root']['sha256'] = 'f'*64
        self.save(self.hashes/'acceptance.json', self.observation)
        with self.assertRaisesRegex(ValueError, 'independently approved root'): self.reviewed()

    def test_wrong_boot_or_identity_is_not_a_draft(self):
        for key, replacement in (('boot_id', '99999999-1234-1234-1234-123456789abc'),
                                 ('nonce', 'f'*32), ('serial', 'f'*16), ('lease_owner', 'f'*64)):
            with self.subTest(key=key):
                changed = dict(self.plan, **{key: replacement})
                self.save(self.hashes/'plan.json', changed)
                with self.assertRaises(ValueError): self.reviewed()
        self.assertFalse(self.output.exists())

    def test_changed_inputs_after_review_refused(self):
        reviewed = self.reviewed()
        self.plan['cid'] = 'b'*32
        self.save(self.hashes/'plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'changed since'): prepare(self.output, self.source, reviewed, 'fixture')
        self.assertFalse(self.output.exists())

    def test_missing_original_archive_prevents_hold_draft(self):
        reviewed = self.reviewed()
        (self.original.source/'card.img.gz').rename(self.original.source/'retained-elsewhere.gz')
        with self.assertRaises(FileNotFoundError): prepare(self.output, self.source, reviewed, 'fixture')
        self.assertFalse(self.output.exists())

    def test_explicit_kind_and_completed_manifest_required(self):
        with self.assertRaises(ValueError): self.reviewed(DERIVATIVE)
        self.save(self.manifest_dir/'acceptance.json', dict(self.receipt, status='incomplete'))
        with self.assertRaisesRegex(ValueError, 'incomplete'): self.reviewed()

    def test_busy_and_uncertain_sessions_do_not_create_output(self):
        reviewed = self.reviewed()
        with Session(self.source.directory/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner) as session:
            with self.assertRaises(BlockingIOError): prepare(self.output, self.source, reviewed, 'fixture')
            write_record(session.fd, Session.names(1)[0], session.request(1, 300))
        with self.assertRaisesRegex(ValueError, 'uncertain lease'): prepare(self.output, self.source, reviewed, 'fixture')
        self.assertFalse(self.output.exists())

    def test_overlap_is_rejected_before_output(self):
        reviewed = self.reviewed()
        for path in (self.hashes/'nested', self.source.directory/'nested', self.staging/'nested', self.root):
            with self.assertRaises(ValueError): prepare(path, self.source, reviewed, 'fixture')

    def test_explicit_derivative_can_guard_the_current_enhanced_root(self):
        from forge_recovery_derivative import prepare as derive
        image = self.original.root/'export.img'
        data = bytearray(self.original.data)
        data[self.manifest['root']['offset']] ^= 1
        image.write_bytes(data)
        image.chmod(0o600)
        derivative = self.original.root/'derivative'
        result = derive(self.original.source, self.manifest, self.manifest_pin, image, derivative)
        self.manifest_dir, self.manifest_pin = derivative, result['manifest_sha256']
        self.observation['digests']['root'] = result['root']
        self.observation['digests']['card'] = result['card']
        self.save(self.hashes/'acceptance.json', self.observation)
        prepared = prepare(self.output, self.source, self.reviewed(DERIVATIVE), 'fixture')
        self.assertEqual(prepared['source_kind'], DERIVATIVE)
        self.assertEqual(prepared['root_sha256'], result['root']['sha256'])
        self.assertFalse(prepared['root_write_authorized'])
