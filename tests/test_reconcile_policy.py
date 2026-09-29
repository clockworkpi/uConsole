import fcntl
import json
import os
import unittest
from unittest.mock import patch

from forge_reconcile_policy import inputs, prepare
from forge_recovery_jobs import RecoveryJobs
from forge_recovery_bootplan import digest
from forge_recovery_session import Session, prepare as prepare_session
from forge_ram_transport import RecoveryProbe
from forge_target_journal import private_directory, write_record
import test_held_reboot
import test_recovery_restore_reconcile_host


class ReconcilePolicyTests(unittest.TestCase):
    def setUp(self):
        self.f = test_held_reboot.HeldRebootTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.source, self.journal, self.pin = self.f.source, self.f.directory, self.f.pin
        self.output = self.f.f.root/'reconcile-policy'
        self.kind = 'hold'

    def reviewed(self): return inputs(self.source, self.journal, self.pin, self.kind)

    def test_offline_hold_draft_is_consumable_and_does_not_touch_original(self):
        original = {str(p): p.read_bytes() for p in self.journal.rglob('*') if p.is_file()}
        with patch.object(RecoveryProbe, '_observe') as remote, patch.object(Session, 'renew') as renew:
            result = prepare(self.output, self.source, self.reviewed(), 'fixture')
        remote.assert_not_called()
        renew.assert_not_called()
        job = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.output}).get('reconcile', 'fixture')
        self.assertEqual(job.operation, 'reconcile-hold')
        self.assertEqual(job.arguments['transition'], 'install-hold')
        self.assertEqual(job.arguments['plan_sha256'], self.pin)
        self.assertEqual(job.boot_id, self.source.boot_id)
        self.assertEqual(original, {str(p): p.read_bytes() for p in self.journal.rglob('*') if p.is_file()})
        for name in ('target_contacted', 'lease_acquired', 'policy_approved', 'root_write_authorized',
                     'normal_boot_release_authorized', 'retry_authorized'):
            self.assertIs(result[name], False)
        for p in self.output.rglob('*'): self.assertEqual(p.stat().st_mode & 0o077, 0)
        self.assertNotIn(self.source.owner, json.dumps(result))

    def test_uncertain_hold_is_observable_not_retried(self):
        self.f.f.save(self.journal/'commit-attempt/acceptance.json', dict(status='uncertain'))
        result = prepare(self.output, self.source, self.reviewed(), 'fixture')
        self.assertFalse(result['retry_authorized'])

    def test_missing_or_changed_dispatch_is_not_drafted(self):
        path = self.journal/'commit-attempt/dispatch.json'
        dispatch = json.loads(path.read_text())
        self.f.f.save(path, dict(dispatch, attempt='bad'))
        with self.assertRaises(ValueError): self.reviewed()
        path.unlink()
        with self.assertRaises(FileNotFoundError): self.reviewed()

    def test_busy_journal_or_session_cannot_be_adopted(self):
        fd = private_directory(self.journal)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): self.reviewed()
        finally: os.close(fd)
        reviewed = self.reviewed()
        with Session(self.source.directory/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner) as lease:
            with self.assertRaises(BlockingIOError): prepare(self.output, self.source, reviewed, 'fixture')
            write_record(lease.fd, Session.names(1)[0], lease.request(1, 300))
        with self.assertRaisesRegex(ValueError, 'uncertain lease'): prepare(self.output, self.source, reviewed, 'fixture')
        self.assertFalse(self.output.exists())

    def test_changed_review_or_wrong_kind_is_refused(self):
        reviewed = self.reviewed()
        reviewed['original_boot_id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'changed since'): prepare(self.output, self.source, reviewed, 'fixture')
        with self.assertRaises(ValueError): inputs(self.source, self.journal, self.pin, 'deploy-root')
        with self.assertRaises((ValueError, FileNotFoundError)): inputs(self.source, self.journal, self.pin, 'root')

    def test_overlap_does_not_create_output(self):
        for output in (self.journal/'nested', self.source.directory/'nested', self.f.f.root):
            with self.assertRaises(ValueError): prepare(output, self.source, self.reviewed(), 'fixture')

    def root_fixture(self):
        fixture = test_recovery_restore_reconcile_host.RestoreReconcileHostTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.journal, self.pin, self.kind = fixture.directory, fixture.pin, 'root'
        b = fixture.plan['binding']
        prior = self.source
        probe = RecoveryProbe('fixture', prior.probe.key, prior.probe.known_hosts, b['nonce'], b['kernel'], b['serial'])
        directory = self.f.f.root/'new-root-enrollment'
        directory.mkdir(mode=0o700)
        boot = '99999999-2222-3333-4444-555555555555'
        session = prepare_session(directory/'session', probe, boot, b['lease_owner'])
        accepted = json.loads((prior.directory/'acceptance.json').read_text())
        accepted.update(host=probe.host, port=probe.port, kernel=probe.kernel, serial=probe.serial,
                        boot_id=boot, binding_sha256=session['binding_sha256'],
                        machine_id=fixture.plan['held_files']['machine_id'])
        self.f.f.save(directory/'acceptance.json', accepted)
        from forge_backup_policy import inputs as enrolled
        self.source = enrolled(directory, digest(accepted), probe.key, probe.known_hosts)
        return fixture

    def test_root_new_boot_preserves_uncertainty_and_old_packet_identity(self):
        fixture = self.root_fixture()
        result = prepare(self.output, self.source, self.reviewed(), 'fixture')
        job = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.output}).get('reconcile', 'fixture')
        self.assertEqual(job.operation, 'reconcile-root')
        self.assertNotEqual(result['original_boot_id'], result['observed_boot_id'])
        self.assertEqual(job.boot_id, self.source.boot_id)
        self.assertEqual(fixture.snapshot(), fixture.original_bytes)
        self.assertFalse(result['root_write_authorized'])

    def test_root_health_binding_mismatch_prevents_draft(self):
        fixture = self.root_fixture()
        fixture.save(fixture.attempt_dir/'source-health.json', dict(fixture.health, filesystem_consistency_qualified=True))
        with self.assertRaisesRegex(ValueError, 'health evidence'): self.reviewed()

    def test_wrong_target_owner_cannot_draft_root_observation(self):
        self.root_fixture()
        from dataclasses import replace
        wrong = replace(self.source, owner='f'*64)
        with self.assertRaises(ValueError): inputs(wrong, self.journal, self.pin, 'root')
