import copy
import unittest

from forge_recovery_bootplan import digest
from forge_recovery_restore_ledger import request
from forge_recovery_restore_reconcile import classify
import test_recovery_restore_plan


class RestoreReconcileTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.plan = fixture.compile()
        self.pin = digest(self.plan)
        self.attempt = 'e'*32
        self.boot = self.plan['binding']['boot_id']
        self.hash_plan = copy.deepcopy(self.plan['binding'])
        self.hashes = copy.deepcopy(fixture.hashes)
        self.inspection = copy.deepcopy(fixture.inspection)
        del self.inspection['deployment_authorized']
        self.inspection.update(plan_sha256=self.pin,hold_plan_sha256=fixture.hold_pin,
                               attempt=self.attempt,normal_boot_release_authorized=False)
        self.fenced = dict(status='restore-fenced',plan_sha256=self.pin,attempt=self.attempt,boot_id=self.boot,
            outcome={},stale_read_only_boot_unmounted=False,stale_empty_mountpoint_removed=False,
            root_written=False,boot_files_written=False,normal_boot_release_authorized=False)
        self.outcome('incomplete')

    def outcome(self, state):
        self.fenced['outcome'] = dict(status=state,request=request(self.plan,self.pin,self.attempt),
            boot_id=self.boot,requires_new_recovery_boot=state=='incomplete',
            root_write_authorized=False,normal_boot_release_authorized=False)
        if state=='completed':
            self.fenced['outcome']['result'] = dict(status='root-restore-verified',plan_sha256=self.pin,
                source_manifest_sha256=self.plan['source_manifest_sha256'],boot_id=self.boot,
                root=self.plan['root_after'],prefix=self.plan['prefix_guard'],suffix=self.plan['suffix_guard'],
                bytes_written=self.plan['root_after']['bytes'],protected_ranges_verified=True,boot_unmounted=True,
                normal_boot_release_authorized=False,physical_restore_qualified=False)

    def classify(self):
        return classify(self.plan,self.pin,self.attempt,self.fixture.hold,self.fixture.hold_pin,
                        self.fenced,self.inspection,self.hash_plan,self.hashes)

    def test_incomplete_before_state_does_not_authorize_retry(self):
        result = self.classify()
        self.assertEqual(result['status'],'reconciled-before')
        self.assertTrue(result['requires_new_recovery_boot'])
        self.assertFalse(result['original_attempt_completion_verified'])
        self.assertFalse(result['root_write_authorized'])

    def test_source_matched_after_lost_receipt_is_not_fabricated_completion(self):
        self.hashes['digests']['root'] = self.plan['root_after']
        result = self.classify()
        self.assertEqual(result['status'],'reconciled-source-matched')
        self.assertFalse(result['original_attempt_completion_verified'])
        self.assertTrue(result['requires_new_recovery_boot'])
        self.assertFalse(result['normal_boot_release_authorized'])

    def test_partial_or_diverged_root_is_retained_not_rolled_back(self):
        self.hashes['digests']['root']['sha256'] = '0'*64
        result = self.classify()
        self.assertEqual(result['status'],'reconciled-partial-or-diverged')
        self.assertTrue(result['observation_only'])
        self.assertFalse(result['physical_restore_qualified'])

    def test_completed_receipt_requires_fresh_matching_bytes(self):
        self.outcome('completed')
        result = self.classify()
        self.assertEqual(result['status'],'reconciled-conflict')
        self.assertIn('completed-receipt-root-differs',result['conflicts'])
        self.hashes['digests']['root'] = self.plan['root_after']
        self.assertTrue(self.classify()['original_attempt_completion_verified'])

    def test_fenced_not_started_conflicts_with_changed_root(self):
        self.outcome('fenced-not-started')
        self.assertEqual(self.classify()['status'],'reconciled-before')
        self.hashes['digests']['root'] = self.plan['root_after']
        self.assertIn('unstarted-attempt-root-differs',self.classify()['conflicts'])

    def test_new_boot_cannot_borrow_or_recreate_old_completion_receipt(self):
        boot = '99999999-1234-1234-1234-123456789abc'
        self.hash_plan['boot_id'] = self.fenced['boot_id'] = self.inspection['boot_id'] = boot
        self.hashes['digests']['boot_id'] = boot
        self.fenced['outcome'] = dict(status='previous-boot-ended',previous_boot_id=self.boot,
                                     request=request(self.plan,self.pin,self.attempt))
        self.hashes['digests']['root'] = self.plan['root_after']
        result = self.classify()
        self.assertEqual(result['status'],'reconciled-source-matched')
        self.assertFalse(result['requires_new_recovery_boot'])
        self.assertFalse(result['original_attempt_completion_verified'])
        self.outcome('completed')
        with self.assertRaises(ValueError): self.classify()

    def test_protected_bytes_or_hold_conflict_prevents_source_match_acceptance(self):
        self.hashes['digests']['root'] = self.plan['root_after']
        self.hashes['digests']['prefix']['sha256'] = '0'*64
        self.hashes['digests']['suffix']['sha256'] = '1'*64
        self.inspection['status'] = 'conflict'
        self.inspection['image'] = 'conflict'
        result = self.classify()
        self.assertEqual(result['status'],'reconciled-conflict')
        self.assertEqual(set(result['conflicts']),{'inspection-prefix-changed','protected-prefix-changed',
                                                 'protected-suffix-changed','persistent-hold-not-intact'})

    def test_foreign_attempt_or_layout_rejected(self):
        self.fenced['attempt'] = 'a'*32
        with self.assertRaises(ValueError): self.classify()
        self.fenced['attempt'] = self.attempt
        self.hash_plan['extent']['mbr_sha256'] = '0'*64
        with self.assertRaises(ValueError): self.classify()

    def test_bad_authority_flags_or_incomplete_hash_observation_rejected(self):
        self.fenced['normal_boot_release_authorized'] = True
        with self.assertRaises(ValueError): self.classify()
        self.fenced['normal_boot_release_authorized'] = False
        self.hashes['status'] = 'incomplete'
        with self.assertRaises(ValueError): self.classify()


if __name__=='__main__': unittest.main()
