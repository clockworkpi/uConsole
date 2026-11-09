import base64
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from forge_backup_policy import inputs, prepare
from forge_recovery_bootplan import digest
from forge_recovery_jobs import RecoveryJobs
from forge_recovery_session import Session
from forge_target_journal import write_record
from forge_ram_transport import RecoveryProbe
import test_session_enrollment
import test_recovery_layout


class BackupPolicyTests(unittest.TestCase):
    def setUp(self):
        self.enrollment = test_session_enrollment.SessionEnrollmentTests()
        self.enrollment.setUp()
        self.addCleanup(self.enrollment.doCleanups)
        from forge_session_enrollment import prepare as enroll
        with self.enrollment.observe():
            self.accepted = enroll(self.enrollment.output, self.enrollment.value)
        self.root = self.enrollment.root
        self.output = self.root/'backup-policy'
        self.source = inputs(self.enrollment.output, digest(self.accepted), self.root/'key', self.root/'known_hosts')
        layout = test_recovery_layout.RecoveryLayoutTests()
        layout.setUp()
        self.layout = dict(layout.observed, mbr=base64.b64encode(layout.header).decode())
        self.records = [copy.deepcopy(value) for value in (self.enrollment.observation, self.layout,
                        self.enrollment.observation, self.layout, self.enrollment.observation)]

    def observe(self):
        return patch.object(RecoveryProbe, '_observe', side_effect=copy.deepcopy(self.records))

    def test_draft_is_consumable_backup_only_without_lease_or_archive(self):
        with self.observe() as remote, patch.object(Session, 'renew') as renew:
            result = prepare(self.output, self.source, 'fixture')
        self.assertEqual(remote.call_count, 5)
        renew.assert_not_called()
        self.assertEqual(result['status'], 'prepared-backup-policy-not-approved')
        for key in ('target_written', 'lease_acquired', 'policy_approved', 'backup_created', 'root_write_authorized'):
            self.assertIs(result[key], False)
        registry = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.root/'workspace'})
        self.assertEqual(set(registry.jobs), {'backup'})
        job = registry.get('backup', 'fixture')
        self.assertEqual(job.operation, 'backup-card')
        self.assertEqual(job.arguments['cid'], self.layout['cid'])
        self.assertEqual(job.arguments['disk_id'], '21965b0c')
        self.assertEqual(job.owner, self.source.owner)
        self.assertEqual(job.boot_id, self.source.boot_id)
        self.assertFalse((self.output/'card-backup').exists())
        self.assertEqual([p.name for p in (self.source.directory/'session').iterdir()], ['binding.json'])
        self.assertNotIn(str(self.output), json.dumps(result))
        self.assertNotIn(self.source.owner, json.dumps(result))
        for path in self.output.rglob('*'): self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_hash_draft_is_consumable_hash_only_without_capture_or_lease(self):
        with self.observe() as remote, patch.object(Session, 'renew') as renew:
            result = prepare(self.output, self.source, 'fixture', operation='hash-card')
        renew.assert_not_called()
        self.assertEqual(remote.call_count, 5)
        self.assertEqual(result['status'], 'prepared-hash-policy-not-approved')
        registry = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.root/'workspace'})
        self.assertEqual(set(registry.jobs), {'hash'})
        job = registry.get('hash', 'fixture')
        self.assertEqual(job.operation, 'hash-card')
        self.assertEqual(job.arguments['destination'], str(self.output.resolve()/'card-hashes'))
        self.assertEqual(job.boot_id, self.source.boot_id)
        self.assertEqual(job.arguments['cid'], self.layout['cid'])
        for key in ('target_written', 'lease_acquired', 'policy_approved', 'backup_created',
                    'hashes_captured', 'root_write_authorized'):
            self.assertIs(result[key], False)
        self.assertFalse((self.output/'card-hashes').exists())
        self.assertEqual([p.name for p in (self.source.directory/'session').iterdir()], ['binding.json'])

    def test_other_operations_cannot_gain_a_policy_from_inventory(self):
        for operation in ('deploy-root', 'restore-root', 'release-hold', 'retry-lease', '', None):
            with self.subTest(operation=operation), patch.object(RecoveryProbe, '_observe') as remote:
                with self.assertRaises(ValueError): prepare(self.output, self.source, 'fixture', operation=operation)
                remote.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_changed_card_preserves_failure_without_policy(self):
        self.records[3]['cid'] = 'b'*32
        with self.observe():
            with self.assertRaises(ValueError): prepare(self.output, self.source, 'fixture')
        self.assertTrue((self.output/'failure.json').exists())
        self.assertFalse((self.output/'policy.json').exists())

    def test_reboot_during_inventory_refuses_draft(self):
        self.records[-1]['boot_id'] = '99999999-1234-1234-1234-123456789abc'
        with self.observe():
            with self.assertRaises(ValueError): prepare(self.output, self.source, 'fixture')
        self.assertFalse((self.output/'policy.json').exists())

    def test_held_session_fails_before_probe_or_output(self):
        with Session(self.source.directory/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner), patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaises(BlockingIOError): prepare(self.output, self.source, 'fixture')
        remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_uncertain_lease_is_not_renewed_or_reset(self):
        with Session(self.source.directory/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner) as session:
            write_record(session.fd, Session.names(1)[0], session.request(1, 300))
        with patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaisesRegex(ValueError, 'uncertain lease'):
                prepare(self.output, self.source, 'fixture')
        remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_credentials_fail_before_probe_or_output(self):
        (self.root/'key').write_text('changed')
        with patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaises(ValueError): prepare(self.output, self.source, 'fixture')
        remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_overlapping_output_and_existing_output_refused(self):
        with patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaises(ValueError): prepare(self.source.directory/'session/draft', self.source, 'fixture')
            self.output.mkdir()
            with self.assertRaises(FileExistsError): prepare(self.output, self.source, 'fixture')
        remote.assert_not_called()

    def test_parent_alias_cannot_put_draft_inside_lease_journal(self):
        alias = self.root/'alias'
        alias.symlink_to(self.source.directory/'session', target_is_directory=True)
        with patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaises(ValueError): prepare(alias/'draft', self.source, 'fixture')
        remote.assert_not_called()
        self.assertFalse((self.source.directory/'session/draft').exists())
