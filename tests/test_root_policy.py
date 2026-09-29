import copy
import json
import unittest
from unittest.mock import patch

from forge_backup_policy import inputs as enrolled
from forge_ram_transport import RecoveryProbe
from forge_recovery_bootplan import digest
from forge_recovery_commit_ledger import request as ledger_request
from forge_recovery_jobs import RecoveryJobs
from forge_recovery_operation_contract import load
from forge_recovery_session import Session, prepare as session_prepare
from forge_root_policy import inputs, prepare
import test_backup_policy
import test_deploy_plan
import test_recovery_restore_plan


class RootPolicyTests(unittest.TestCase):
    def setUp(self): self.setup_case(False)

    def setup_case(self, derived):
        self.derived = derived
        if derived:
            self.deployment = test_deploy_plan.DeployPlanTests()
            self.deployment.setUp()
            self.addCleanup(self.deployment.doCleanups)
            f = self.deployment.fixture
        else:
            f = test_recovery_restore_plan.RestorePlanTests()
            f.setUp()
            self.addCleanup(f.doCleanups)
        self.f, self.root = f, f.source.root
        self.hold = self.root/'hold'
        self.hold.mkdir(mode=0o700)
        f.hold['root_guard'] = copy.deepcopy(f.hashes['digests']['root'])
        f.hold_pin = digest(f.hold)
        f.inspection['plan_sha256'] = f.hold_pin
        self.save(self.hold/'plan.json', f.hold)
        attempt = self.hold/'commit-attempt'
        attempt.mkdir(mode=0o700)
        self.save(attempt/'dispatch.json', dict(plan_sha256=f.hold_pin, attempt='a'*32,
            binding=f.hold['binding'], lease_owner=f.current['lease_owner'], worker_protocol=2))
        self.reconciliation = self.hold/('reconcile-'+'b'*32)
        self.reconciliation.mkdir(mode=0o700)
        (self.reconciliation/'hashes').mkdir(mode=0o700)
        fence = dict(status='fenced', plan_sha256=f.hold_pin, boot_id=f.current['boot_id'], attempt='a'*32,
            outcome=dict(status='previous-boot-ended', request=ledger_request(f.hold, f.hold_pin, 'a'*32),
                         previous_boot_id=f.hold['binding']['boot_id']), stale_boot_unmounted=False,
            pending_boot_writes_may_have_flushed=False, root_written=False)
        self.accepted = dict(status='reconciled-after', query='b'*32, attempt='a'*32,
            plan_sha256=f.hold_pin, deployment_authorized=False, root_written=False,
            normal_boot_release_authorized=False, fence=fence, inspection=f.inspection,
            digests=f.hashes['digests'], evidence_directory=str(self.reconciliation))
        self.save(self.reconciliation/'acceptance.json', self.accepted)
        self.save(self.reconciliation/'fence.json', fence)
        self.save(self.reconciliation/'inspect.json', f.inspection)
        self.save(self.reconciliation/'hashes/plan.json', f.current)
        self.save(self.reconciliation/'hashes/acceptance.json', f.hashes)
        self.manifest, self.manifest_pin = self.root/'manifest', f.source_pin
        self.health = self.root/'source-health'
        self.health.mkdir(mode=0o700)
        if derived:
            d = self.deployment
            self.manifest, self.manifest_pin = self.root/'derivative', d.pin
            health = d.health
            self.save(self.health/'source-stream.json', dict(status='verified-derivative-stream', manifest_sha256=d.pin,
                chunks=len(d.derivative['chunks']), root=d.derivative['root'], target_restore_verified=False,
                normal_boot_release_authorized=False))
            self.save(self.health/'plan.json', dict(derivative_manifest_sha256=d.pin, source=str(self.manifest),
                image=d.derivative['card'], root=d.derivative['root'], command=['/usr/sbin/e2fsck', '-f', '-n']))
            self.save(self.health/'root-check.json', dict(argv=['/usr/sbin/e2fsck', '-f', '-n'], returncode=0, output='clean fixture'))
        else:
            health = dict(status='checked', filesystem_consistency_qualified=False, restore_authorized=False,
                target_written=False, repair_performed=False, check_returncodes=dict(boot=0, root=4), image=f.manifest['card'])
        self.health_record, self.health_pin = health, digest(health)
        self.save(self.health/'acceptance.json', health)
        enrolled_fixture = test_backup_policy.BackupPolicyTests()
        enrolled_fixture.setUp()
        self.addCleanup(enrolled_fixture.doCleanups)
        previous = enrolled_fixture.source
        probe = RecoveryProbe('fixture', previous.probe.key, previous.probe.known_hosts,
                             f.current['nonce'], f.current['kernel'], f.current['serial'])
        self.enrollment = self.root/'enrollment'
        self.enrollment.mkdir(mode=0o700)
        session = session_prepare(self.enrollment/'session', probe, f.current['boot_id'], f.current['lease_owner'])
        accepted = dict(enrolled_fixture.accepted, host=probe.host, port=probe.port, kernel=probe.kernel,
            serial=probe.serial, boot_id=f.current['boot_id'], binding_sha256=session['binding_sha256'],
            machine_id=f.hold['before']['machine_id'], tryboot=0)
        self.save(self.enrollment/'acceptance.json', accepted)
        self.save(self.enrollment/'selection.json', dict(observation=f.boot))
        self.source = enrolled(self.enrollment, digest(accepted), probe.key, probe.known_hosts)
        self.output = self.root/'root-policy'

    def save(self, path, value): self.f.source.save(path, value)

    def reviewed(self):
        return inputs(self.source, self.hold, self.f.hold_pin, self.reconciliation, digest(self.accepted),
            self.manifest, self.manifest_pin, self.health, self.health_pin,
            'deploy-root' if self.derived else 'restore-root')

    def test_restore_requires_explicit_known_error_acceptance_and_preserves_backup(self):
        reviewed = self.reviewed()
        archive = self.f.source.source/'card.img.gz'
        original = archive.read_bytes()
        with self.assertRaisesRegex(ValueError, 'explicit owner acceptance'):
            prepare(self.output, self.source, reviewed, 'fixture')
        with patch.object(RecoveryProbe, '_observe') as remote, patch.object(Session, 'renew') as renew:
            result = prepare(self.output, self.source, reviewed, 'fixture', accept_filesystem_errors=True)
        remote.assert_not_called()
        renew.assert_not_called()
        self.assertEqual(archive.read_bytes(), original)
        self.assertFalse((self.output/'root-plan/restore-attempt').exists())
        plan = load(self.output/'root-plan', result['plan_sha256'])
        self.assertEqual(plan['root_after'], self.f.manifest['root'])
        self.assertNotEqual(plan['root_before'], plan['root_after'])
        job = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.root}).get('transfer', 'fixture')
        self.assertEqual(job.operation, 'restore-root')
        self.assertTrue(job.arguments['accept_filesystem_errors'])
        self.assertEqual(job.arguments['source_directory'], str(self.f.source.source))
        for field in ('target_contacted', 'lease_acquired', 'policy_approved', 'root_write_authorized', 'normal_boot_release_authorized'):
            self.assertIs(result[field], False)

    def test_healthy_derivative_has_separate_consumable_deploy_policy(self):
        self.setup_case(True)
        reviewed = self.reviewed()
        with self.assertRaises(ValueError): prepare(self.output, self.source, reviewed, 'fixture', accept_filesystem_errors=True)
        result = prepare(self.output, self.source, reviewed, 'fixture')
        plan = load(self.output/'root-plan', result['plan_sha256'])
        self.assertEqual(plan['rollback_manifest_sha256'], self.f.source_pin)
        self.assertEqual(plan['root_after'], self.deployment.derivative['root'])
        job = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.root}).get('transfer', 'fixture')
        self.assertEqual(job.operation, 'deploy-root')
        self.assertEqual(job.arguments['source_directory'], str(self.manifest))
        self.assertNotIn('accept_filesystem_errors', job.arguments)

    def test_unhealthy_derivative_is_never_a_deployment_draft(self):
        self.setup_case(True)
        self.health_record.update(root_check_returncode=4, root_filesystem_consistency_qualified=False)
        self.health_pin = digest(self.health_record)
        self.save(self.health/'acceptance.json', self.health_record)
        self.save(self.health/'root-check.json', dict(argv=['/usr/sbin/e2fsck', '-f', '-n'], returncode=4, output='errors'))
        with self.assertRaisesRegex(ValueError, 'clean derivative'): self.reviewed()

    def test_missing_original_archive_prevents_both_drafts(self):
        for derived in (False, True):
            if derived: self.setup_case(True)
            (self.f.source.source/'card.img.gz').rename(self.f.source.source/'preserved.gz')
            with self.assertRaises(FileNotFoundError): self.reviewed()

    def test_changed_export_is_not_a_deployment_draft(self):
        self.setup_case(True)
        image = self.root/'derived.img'
        image.write_bytes(image.read_bytes())
        with self.assertRaisesRegex(ValueError, 'Enhanced image differs'): self.reviewed()

    def test_reconciliation_conflict_and_wrong_boot_refuse_before_output(self):
        self.accepted['status'] = 'reconciled-conflict'
        self.save(self.reconciliation/'acceptance.json', self.accepted)
        with self.assertRaises(ValueError): self.reviewed()
        self.accepted['status'] = 'reconciled-after'
        self.accepted['inspection']['boot_id'] = self.f.hold['binding']['boot_id']
        self.save(self.reconciliation/'acceptance.json', self.accepted)
        with self.assertRaises(ValueError): self.reviewed()
        self.assertFalse(self.output.exists())

    def test_changed_inputs_after_review_and_busy_session_refused(self):
        reviewed = self.reviewed()
        with Session(self.enrollment/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner):
            with self.assertRaises(BlockingIOError):
                prepare(self.output, self.source, reviewed, 'fixture', accept_filesystem_errors=True)
        reviewed['root_after']['sha256'] = '0'*64
        with self.assertRaises(ValueError): prepare(self.output, self.source, reviewed, 'fixture', accept_filesystem_errors=True)

    def test_output_cannot_overlap_original_archive(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            prepare(self.f.source.source/'nested', self.source, self.reviewed(), 'fixture', accept_filesystem_errors=True)
