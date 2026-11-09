import copy
from dataclasses import replace
import os
import unittest
from unittest.mock import patch

from forge_recovery_bootplan import digest
from forge_recovery_hold_jobs import load
from forge_recovery_jobs import execute
from forge_target_journal import private_directory, write_record
import test_recovery_bootplan
import test_recovery_jobs


class RecoveryHoldJobTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_jobs.RecoveryJobsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        base = test_recovery_bootplan.RecoveryBootPlanTests()
        base.setUp()
        self.base = base
        self.plan = base.compile()
        self.plan['stage_token'] = 'e'*32
        self.plan['binding']['lease_owner'] = 'b'*64
        self.directory = self.fixture.root/'hold-plan'
        self.directory.mkdir(mode=0o700)
        original = self.fixture.registry().get('proof', 'main')
        self.job = replace(original, operation='install-hold',
                           probe=replace(original.probe, nonce=base.binding['nonce']),
                           boot_id=base.binding['boot_id'], machine_id=self.plan['before']['machine_id'],
                           arguments={})
        self.save()

    def save(self):
        fd = private_directory(self.directory)
        try:
            if (self.directory/'plan.json').exists():
                # Fixture-only replacement; production journals are immutable.
                (self.directory/'plan.json').unlink()
            pin = write_record(fd, 'plan.json', self.plan)
        finally:
            os.close(fd)
        arguments = dict(journal=str(self.directory), plan_sha256=pin,
                         root_sha256=self.plan['root_guard']['sha256'], root_bytes=self.plan['root_guard']['bytes'])
        self.job = replace(self.job, arguments=arguments)

    def test_owner_policy_requires_explicit_root_size_digest_and_transition(self):
        for operation in ('install-hold', 'release-hold', 'reconcile-hold'):
            arguments = dict(self.job.arguments)
            if operation == 'reconcile-hold': arguments['transition'] = 'install-hold'
            self.fixture.item.update(operation=operation, arguments=arguments)
            self.fixture.save()
            self.assertEqual(self.fixture.registry().get('proof', 'main').operation, operation)
            for field, value in (('root_bytes', True), ('root_bytes', 513), ('root_sha256', 'unknown')):
                previous = arguments[field]
                arguments[field] = value
                self.fixture.save()
                with self.assertRaises(ValueError): self.fixture.registry()
                arguments[field] = previous
            if operation == 'reconcile-hold':
                arguments['transition'] = 'automatic'
                self.fixture.save()
                with self.assertRaises(ValueError): self.fixture.registry()

    def test_fixed_commit_approval_and_no_reboot_or_root_write_route(self):
        for operation in ('install-hold', 'release-hold'):
            self.plan['operation'] = operation
            self.job = replace(self.job, operation=operation)
            self.save()
            def dispatch(probe, directory, pin, lease, *, authorize):
                self.assertIs(probe, self.job.probe)
                self.assertEqual(directory, str(self.directory))
                self.assertEqual(authorize(copy.deepcopy(self.plan)), dict(commit=pin, boot_id=self.job.boot_id))
                changed = copy.deepcopy(self.plan)
                changed['root_guard']['sha256'] = '0'*64
                with self.assertRaises(ValueError): authorize(changed)
                return {'status': 'acknowledged', 'root_written': False}
            with patch('forge_recovery_jobs.Session') as session, patch(
                    'forge_recovery_commit_transport.dispatch', side_effect=dispatch) as worker:
                session.return_value.__enter__.return_value.unresolved = False
                self.assertEqual(execute(self.job), {'status': 'acknowledged', 'root_written': False})
                worker.assert_called_once()

    def test_wrong_local_identity_or_root_guard_refused_before_session_or_contact(self):
        cases = (dict(machine_id='0'*32), dict(boot_id='22222222-2222-3333-4444-555555555555'),
                 dict(owner='0'*64), dict(operation='release-hold'),
                 dict(arguments=dict(self.job.arguments, root_sha256='0'*64)),
                 dict(arguments=dict(self.job.arguments, root_bytes=512)),
                 dict(probe=replace(self.job.probe, serial='0'*16)))
        with patch('forge_recovery_jobs.Session') as session:
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    execute(replace(self.job, **changes))
            session.assert_not_called()

    def test_prior_attempt_refused_before_session_even_if_completion_was_lost(self):
        (self.directory/'commit-attempt').mkdir()
        with patch('forge_recovery_jobs.Session') as session:
            with self.assertRaisesRegex(FileExistsError, 'already attempted'): execute(self.job)
            session.assert_not_called()

    def test_changed_plan_bytes_refused_before_session(self):
        self.plan['root_guard']['sha256'] = '0'*64
        (self.directory/'plan.json').write_text('{}')
        with patch('forge_recovery_jobs.Session') as session:
            with self.assertRaises(ValueError): execute(self.job)
            session.assert_not_called()

    def test_reconciliation_explicit_new_boot_and_filtered_result(self):
        job = replace(self.job, operation='reconcile-hold', boot_id='22222222-2222-3333-4444-555555555555',
                      arguments=dict(self.job.arguments, transition='install-hold'))
        self.assertEqual(load(job), self.plan)
        with patch('forge_recovery_jobs.Session') as session, patch(
                'forge_recovery_commit_reconcile.reconcile', return_value={
                    'status': 'reconciled-after', 'root_written': False,
                    'evidence_directory': '/private', 'inspection': {'files': 'private content'},
                    'normal_boot_release_authorized': False}) as reconcile:
            lease = session.return_value.__enter__.return_value
            lease.unresolved = False
            result = execute(job)
            reconcile.assert_called_once_with(job.probe, str(self.directory), digest(self.plan), lease,
                                              observed_boot_id=job.boot_id)
        self.assertEqual(result, dict(status='reconciled-after', root_written=False, normal_boot_release_authorized=False))

    def preparation(self):
        b = self.base
        source = dict(schema=1, kind='backup-root-chunk-source', backup_plan_sha256='a'*64,
                      backup_acceptance_sha256='b'*64, archive_fingerprint=[0]*8,
                      card=dict(bytes=b.binding['extent']['disk_bytes'], sha256='d'*64), root=copy.deepcopy(b.root),
                      chunk_bytes=4*1024*1024, chunks=[dict(offset=0, bytes=b.root['bytes'], sha256=b.root['sha256'])],
                      target_write_authorized=False, normal_boot_release_authorized=False)
        review = copy.deepcopy(b.review)
        review['lease'] = dict(owner=self.job.owner)
        inputs = dict(schema=1, hold_review=review,
                      hash_plan=dict(b.binding, lease_owner=self.job.owner), hash_observation=copy.deepcopy(b.observation),
                      source_manifest=source, source_manifest_sha256=digest(source), source_kind=source['kind'])
        directory = self.fixture.root/'inputs'
        directory.mkdir(mode=0o700)
        fd = private_directory(directory)
        try: pin = write_record(fd, 'inputs.json', inputs)
        finally: os.close(fd)
        return replace(self.job, operation='prepare-hold', arguments=dict(input_directory=str(directory),
                       input_sha256=pin, destination=str(self.fixture.root/'prepared'), transition='release-hold')), inputs

    def test_plan_preparation_uses_manifest_guard_and_grants_no_execution(self):
        import json
        from pathlib import Path
        job, inputs = self.preparation()
        with patch('forge_recovery_jobs.Session') as session, \
                patch.object(type(job.probe), '_observe', side_effect=AssertionError('unexpected target contact')):
            session.return_value.__enter__.return_value.unresolved = False
            result = execute(job)
        self.assertEqual(result['status'], 'prepared-not-approved')
        self.assertFalse(result['deployment_authorized'])
        self.assertFalse(result['normal_boot_release_authorized'])
        self.assertNotIn('journal', result)
        plan = json.loads((Path(job.arguments['destination'])/'plan.json').read_text())
        self.assertEqual(digest(plan), result['plan_sha256'])
        self.assertEqual(plan['root_guard'], inputs['source_manifest']['root'])
        self.assertEqual(plan['operation'], 'release-hold')
        with patch('forge_recovery_jobs.Session') as session:
            with self.assertRaises(FileExistsError): execute(job)
            session.assert_not_called()

    def test_plan_preparation_never_adopts_current_root_as_expected_source(self):
        from forge_recovery_hold_jobs import load_preparation
        from pathlib import Path
        job, inputs = self.preparation()
        inputs['hash_observation']['digests']['root']['sha256'] = '0'*64
        directory = Path(job.arguments['input_directory'])
        (directory/'inputs.json').unlink()
        fd = private_directory(directory)
        try: pin = write_record(fd, 'inputs.json', inputs)
        finally: os.close(fd)
        changed = replace(job, arguments=dict(job.arguments, input_sha256=pin))
        with self.assertRaisesRegex(ValueError, 'independently approved root'):
            load_preparation(changed)
        self.assertFalse(Path(job.arguments['destination']).exists())

    def test_preparation_pin_and_physical_session_checked_before_session_open(self):
        job, _ = self.preparation()
        with patch('forge_recovery_jobs.Session') as session:
            for changed in (replace(job, arguments=dict(job.arguments, input_sha256='0'*64)),
                            replace(job, boot_id='22222222-2222-3333-4444-555555555555')):
                with self.assertRaises(ValueError): execute(changed)
            session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
