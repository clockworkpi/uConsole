import copy
import json
import unittest
from unittest.mock import patch

from forge_backup_policy import inputs as enrolled
from forge_recovery_bootplan import digest
from forge_recovery_jobs import RecoveryJobs
from forge_recovery_restore_ledger import request
from forge_recovery_restore_plan import prepare as prepare_restore
from forge_recovery_restore_reconcile import classify
from forge_recovery_session import Session, binding
from forge_ram_transport import RecoveryProbe
from forge_release_policy import inputs, prepare
import test_root_policy
import test_recovery_bootplan


class ReleasePolicyTests(unittest.TestCase):
    def setUp(self):
        self.setup_case(False)

    def setup_case(self, derived):
        self.base = test_root_policy.RootPolicyTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        if derived: self.base.setup_case(True)
        self.f, self.root, self.source = self.base.f, self.base.root, self.base.source
        fixture = test_recovery_bootplan.RecoveryBootPlanTests()
        fixture.setUp()
        self.review = fixture.review
        self.review['lease'] = dict(owner=self.source.owner)
        self.review_pin = digest(self.review)
        self.f.hold['hold_review_sha256'] = self.review_pin
        self.f.hold_pin = digest(self.f.hold)
        self.f.inspection['plan_sha256'] = self.f.hold_pin
        self.review_directory = self.root/'review'
        self.review_directory.mkdir(mode=0o700)
        self.save(self.review_directory/'hold-review.json', self.review)
        self.journal = self.root/'root-attempt-plan'
        if derived:
            from forge_recovery_deploy_plan import prepare as prepare_deploy
            prepared = prepare_deploy(self.journal, *self.base.deployment.arguments())
        else:
            prepared = prepare_restore(self.journal, *self.f.arguments())
        self.pin = prepared['plan_sha256']
        self.plan = json.loads((self.journal/'plan.json').read_text())
        attempt = self.journal/'restore-attempt'
        attempt.mkdir(mode=0o700)
        self.save(attempt/'dispatch.json', dict(plan_sha256=self.pin, attempt='e'*32, binding=self.plan['binding'],
            packet_sha256='a'*64, source_manifest_sha256=self.plan['source_manifest_sha256'],
            source_health_sha256=self.base.health_pin, worker_protocol=1))
        self.save(attempt/'source-health.json', self.base.health_record)
        self.save(attempt/'acceptance.json', dict(status='uncertain', root_written='unknown'))
        self.reconciliation = self.journal/('reconcile-'+'f'*32)
        self.reconciliation.mkdir(mode=0o700)
        (self.reconciliation/'hashes').mkdir(mode=0o700)
        self.hashes, self.hash_plan = copy.deepcopy(self.f.hashes), copy.deepcopy(self.f.current)
        self.hashes['digests']['root'] = copy.deepcopy(self.plan['root_after'])
        self.inspection = copy.deepcopy(self.f.inspection)
        del self.inspection['deployment_authorized']
        self.inspection.update(plan_sha256=self.pin, hold_plan_sha256=self.f.hold_pin, attempt='e'*32,
                               normal_boot_release_authorized=False)
        self.fence = dict(status='restore-fenced', plan_sha256=self.pin, attempt='e'*32, boot_id=self.source.boot_id,
            outcome={}, stale_read_only_boot_unmounted=False, stale_empty_mountpoint_removed=False,
            root_written=False, boot_files_written=False, normal_boot_release_authorized=False)
        self.outcome('completed')
        self.retain()
        self.output = self.root/'release-policy'

    def save(self, path, value): self.base.save(path, value)

    def outcome(self, status):
        self.fence['outcome'] = dict(status=status, request=request(self.plan, self.pin, 'e'*32),
            boot_id=self.source.boot_id, requires_new_recovery_boot=status == 'incomplete',
            root_write_authorized=False, normal_boot_release_authorized=False)
        if status == 'completed':
            self.fence['outcome']['result'] = dict(status='root-restore-verified', plan_sha256=self.pin,
                source_manifest_sha256=self.plan['source_manifest_sha256'], boot_id=self.source.boot_id,
                root=self.plan['root_after'], prefix=self.plan['prefix_guard'], suffix=self.plan['suffix_guard'],
                bytes_written=self.plan['root_after']['bytes'], protected_ranges_verified=True, boot_unmounted=True,
                normal_boot_release_authorized=False, physical_restore_qualified=False)

    def retain(self):
        classified = classify(self.plan, self.pin, 'e'*32, self.f.hold, self.f.hold_pin,
                              self.fence, self.inspection, self.hash_plan, self.hashes)
        self.accepted = dict(classified, query='f'*32, root_written=False, evidence_directory=str(self.reconciliation))
        for name, value in (('acceptance.json', self.accepted), ('fence.json', self.fence),
                            ('inspect.json', self.inspection), ('hashes/plan.json', self.hash_plan),
                            ('hashes/acceptance.json', self.hashes)):
            self.save(self.reconciliation/name, value)

    def reviewed(self):
        return inputs(self.source, self.journal, self.pin, self.reconciliation, digest(self.accepted),
                      self.review_directory, self.review_pin, str(self.f.source.source))

    def test_offline_release_draft_preserves_uncertain_history_and_requires_health_choice(self):
        reviewed = self.reviewed()
        original = (self.journal/'restore-attempt/acceptance.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'explicit owner acceptance'):
            prepare(self.output, self.source, reviewed, 'fixture')
        with patch.object(RecoveryProbe, '_observe') as remote, patch.object(Session, 'renew') as renew:
            result = prepare(self.output, self.source, reviewed, 'fixture', accept_filesystem_errors=True)
        remote.assert_not_called()
        renew.assert_not_called()
        job = RecoveryJobs(self.output/'policy.json', result['policy_sha256'], {'fixture': self.root}).get('release', 'fixture')
        self.assertEqual(job.operation, 'release-hold')
        self.assertEqual(job.arguments['root_sha256'], self.plan['root_after']['sha256'])
        plan = json.loads((self.output/'release/plan.json').read_text())
        self.assertEqual(plan['before'], self.f.hold['after'])
        self.assertEqual(plan['after'], self.f.hold['before'])
        self.assertEqual((self.journal/'restore-attempt/acceptance.json').read_bytes(), original)
        self.assertFalse((self.output/'release/commit-attempt').exists())
        for key in ('target_contacted', 'lease_acquired', 'policy_approved', 'reboot_performed',
                    'root_write_authorized', 'normal_boot_release_authorized'):
            self.assertIs(result[key], False)

    def test_source_matched_incomplete_same_boot_cannot_release(self):
        self.outcome('incomplete')
        self.retain()
        self.assertEqual(self.accepted['status'], 'reconciled-source-matched')
        with self.assertRaisesRegex(ValueError, 'safely fenced'): self.reviewed()

    def test_clean_enhanced_root_can_draft_release_without_error_waiver(self):
        self.setup_case(True)
        result = prepare(self.output, self.source, self.reviewed(), 'fixture')
        self.assertFalse(result['source_filesystem_errors'])
        self.assertFalse(result['accept_filesystem_errors'])
        self.assertEqual(result['root'], self.base.deployment.derivative['root'])
        self.assertFalse(result['normal_boot_release_authorized'])

    def test_new_boot_independent_recovery_does_not_fabricate_old_completion(self):
        old = self.source.boot_id
        new = '99999999-2222-3333-4444-555555555555'
        saved = binding(self.source.probe, new, self.source.owner)
        self.save(self.source.directory/'session/binding.json', saved)
        accepted = json.loads((self.source.directory/'acceptance.json').read_text())
        accepted.update(boot_id=new, binding_sha256=digest(saved))
        self.save(self.source.directory/'acceptance.json', accepted)
        self.source = enrolled(self.source.directory, digest(accepted), self.source.probe.key, self.source.probe.known_hosts)
        self.hash_plan['boot_id'] = self.hashes['digests']['boot_id'] = new
        self.fence['boot_id'] = self.inspection['boot_id'] = new
        self.fence['outcome'] = dict(status='previous-boot-ended', previous_boot_id=old,
                                    request=request(self.plan, self.pin, 'e'*32))
        self.retain()
        result = prepare(self.output, self.source, self.reviewed(), 'fixture', accept_filesystem_errors=True)
        self.assertFalse(result['original_attempt_completion_verified'])

    def test_conflicting_protected_bytes_prevent_release(self):
        self.hashes['digests']['suffix']['sha256'] = '0'*64
        self.retain()
        with self.assertRaisesRegex(ValueError, 'safely fenced'): self.reviewed()

    def test_tampered_classification_is_not_trusted(self):
        self.accepted['original_attempt_completion_verified'] = False
        self.save(self.reconciliation/'acceptance.json', self.accepted)
        with self.assertRaisesRegex(ValueError, 'reclassified'): self.reviewed()

    def test_numeric_false_does_not_replace_boolean_evidence(self):
        self.accepted['root_written'] = 0
        self.save(self.reconciliation/'acceptance.json', self.accepted)
        with self.assertRaisesRegex(ValueError, 'reclassified'): self.reviewed()

    def test_health_change_during_review_cannot_hide_original_errors(self):
        import forge_release_policy as policy
        original = policy.record
        def changed(directory, name):
            value = original(directory, name)
            if name == 'source-health.json': value['filesystem_consistency_qualified'] = True
            return value
        with patch.object(policy, 'record', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'health changed'): self.reviewed()

    def test_missing_backup_and_changed_review_prevent_draft(self):
        reviewed = self.reviewed()
        reviewed['root']['sha256'] = '0'*64
        with self.assertRaises(ValueError): prepare(self.output, self.source, reviewed, 'fixture', accept_filesystem_errors=True)
        (self.f.source.source/'card.img.gz').rename(self.f.source.source/'preserved.gz')
        with self.assertRaises(FileNotFoundError): self.reviewed()

    def test_busy_session_prevents_draft(self):
        reviewed = self.reviewed()
        with Session(self.source.directory/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner):
            with self.assertRaises(BlockingIOError):
                prepare(self.output, self.source, reviewed, 'fixture', accept_filesystem_errors=True)
