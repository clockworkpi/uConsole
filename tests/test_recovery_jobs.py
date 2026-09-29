from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from forge_client import ClientSession
from forge_controller import Controller
from forge_ram_transport import RecoveryProbe
from forge_recovery_bootplan import digest
from forge_recovery_jobs import RecoveryJobs, execute
from forge_recovery_session import prepare
from uconsole_mcp import BY_NAME, validate


class RecoveryJobsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('key', 'known_hosts'):
            (self.root/name).write_text('disposable fixture ' + name)
            (self.root/name).chmod(0o600)
        self.probe = RecoveryProbe('fixture', self.root/'key', self.root/'known_hosts',
                                   'a'*32, '6.12.62-v8+', '100000007b961d25')
        self.boot = '11111111-2222-3333-4444-555555555555'
        self.session = self.root/'session'
        record = prepare(self.session, self.probe, self.boot, 'b'*64)
        self.item = dict(workspace='main', machine_id='c'*32, session=str(self.session),
                         session_sha256=record['binding_sha256'], key=str(self.root/'key'),
                         known_hosts=str(self.root/'known_hosts'), operation='retry-lease', arguments={})
        self.definition = dict(schema=1, jobs=dict(proof=self.item))
        self.policy = self.root/'policy.json'
        self.workspaces = dict(main=self.root/'workspace', other=self.root/'other')
        self.save()

    def save(self):
        self.policy.write_text(json.dumps(self.definition))
        self.pin = hashlib.sha256(self.policy.read_bytes()).hexdigest()

    def registry(self):
        return RecoveryJobs(self.policy, self.pin, self.workspaces)

    def controller(self, grants=('target-recovery',)):
        result = Controller(self.workspaces, grants, recovery_policy=self.policy, recovery_policy_sha256=self.pin)
        self.addCleanup(result.close)
        return result

    def test_snapshot_and_listing_are_local_private_and_workspace_bound(self):
        with patch.object(RecoveryProbe, '_observe', side_effect=AssertionError('unexpected SSH')):
            registry = self.registry()
        before = registry.describe('main')
        self.assertNotIn(str(self.root), json.dumps(before))
        self.assertNotIn('fixture', json.dumps(before))
        self.assertEqual(registry.describe('other'), [])
        with self.assertRaises(ValueError): registry.get('proof', 'other')
        registry.get('proof', 'main').arguments['injected'] = True
        self.policy.write_text('{}')
        self.assertEqual(registry.get('proof', 'main').arguments, {})
        self.assertEqual(registry.describe('main'), before)
        with self.assertRaises(ValueError): self.registry()

    def test_bad_policy_and_credential_pins_fail_closed(self):
        for field, value in (('session_sha256', '0'*64), ('workspace', 'unknown'),
                             ('machine_id', 'invalid'), ('operation', 'shell'),
                             ('arguments', {'host': 'other'}), ('session', '../session')):
            original = self.item[field]
            self.item[field] = value
            self.save()
            with self.subTest(field=field), self.assertRaises(ValueError): self.registry()
            self.item[field] = original
        self.save()
        (self.root/'key').write_text('changed key')
        with self.assertRaisesRegex(ValueError, 'credentials'): self.registry()

    def test_nonphysical_session_refused(self):
        probe = replace(self.probe, mode='emulated')
        record = prepare(self.root/'emulated', probe, self.boot, 'b'*64)
        self.item.update(session=str(self.root/'emulated'), session_sha256=record['binding_sha256'])
        self.save()
        with self.assertRaisesRegex(ValueError, 'physical'): self.registry()

    def test_duplicate_json_and_wrong_schema_refused(self):
        for payload in ('{"schema":1,"schema":1,"jobs":{}}', '{"schema":true,"jobs":{}}'):
            self.policy.write_text(payload)
            self.pin = hashlib.sha256(payload.encode()).hexdigest()
            with self.assertRaises(ValueError): self.registry()

    def test_grant_requires_policy_and_is_not_inherited_by_client(self):
        with self.assertRaises(ValueError): Controller(self.workspaces, ['target-recovery'])
        with self.assertRaises(ValueError): Controller(self.workspaces, recovery_policy=self.policy)
        owner = self.controller()
        client = ClientSession(owner, ['main'])
        self.assertFalse(client.call('recovery_jobs', {'workspace': 'main'})['execution_granted'])
        with self.assertRaises(PermissionError):
            client.call('recovery_job', {'workspace': 'main', 'job': 'proof'})
        self.assertEqual(owner.jobs, {})

    def test_no_client_overrides_or_policy_approval(self):
        args = dict(workspace='main', job='proof')
        validate(BY_NAME['recovery_job']['inputSchema'], args)
        for key in ('host', 'journal', 'operation', 'arguments', 'plan_sha256', 'authorize'):
            with self.assertRaises(ValueError):
                validate(BY_NAME['recovery_job']['inputSchema'], dict(args, **{key: 'override'}))
        self.assertNotIn('approve_recovery_policy', BY_NAME)

    def test_async_serialization_and_running_cancellation_refusal(self):
        owner = self.controller()
        started, release = threading.Event(), threading.Event()
        def work(job):
            started.set()
            if not release.wait(5): raise TimeoutError('fixture did not release')
            return {'root_written': False}
        with patch('forge_recovery_jobs.execute', side_effect=work):
            try:
                client = ClientSession(owner, ['main'], ['target-recovery'])
                job = client.call('recovery_job', dict(workspace='main', job='proof'))
                self.assertTrue(started.wait(5))
                self.assertFalse(client.call('job_cancel', {'job_id': job['job_id']})['requested'])
                with self.assertRaisesRegex(ValueError, 'physical target'):
                    owner.submit('other', 'competing', lambda: None, target_identity='c'*32)
                with self.assertRaisesRegex(ValueError, 'Wait for controller'):
                    owner.approve_recovery_policy(self.policy, self.pin)
            finally:
                release.set()
            owner.jobs[job['job_id']][2].result(timeout=5)
        self.assertEqual(owner.job(job['job_id'])['context']['policy_sha256'], self.pin)

    def test_pending_lease_blocks_other_jobs_and_existing_backup_never_contacts_target(self):
        job = replace(self.registry().get('proof', 'main'), operation='backup-card',
                      arguments=dict(destination=str(self.root/'new'), cid='a'*32,
                                     disk_id='12345678', device='/dev/mmcblk0'))
        with patch('forge_recovery_jobs.Session') as session, patch('forge_recovery_backup.backup') as backup:
            session.return_value.__enter__.return_value.unresolved = True
            with self.assertRaisesRegex(RuntimeError, 'pending lease'): execute(job)
            backup.assert_not_called()
        with patch('forge_recovery_jobs.Session') as session:
            with self.assertRaises(FileExistsError):
                execute(replace(job, arguments=dict(job.arguments, destination=str(self.root))))
            session.assert_not_called()

    def test_dispatch_approval_binds_health_plan_boot_and_filesystem_review(self):
        for derived in (False, True):
            health = dict(filesystem_consistency_qualified=derived)
            plan = dict(binding=dict(boot_id=self.boot), rollback_manifest_sha256='d'*64)
            arguments = dict(journal=str(self.root/'journal'), plan_sha256=digest(plan),
                             source_directory=str(self.root/'source'), health_directory=str(self.root/'health'),
                             health_sha256=digest(health))
            if not derived: arguments['accept_filesystem_errors'] = True
            job = replace(self.registry().get('proof', 'main'),
                          operation='deploy-root' if derived else 'restore-root', arguments=arguments)
            def dispatch(*args, authorize):
                approval = authorize(plan, digest(plan), 'dispatch', health)
                self.assertEqual(approval['deploy' if derived else 'restore'], digest(plan))
                with self.assertRaises(ValueError): authorize(plan, '0'*64, 'dispatch', health)
                with self.assertRaises(ValueError): authorize(plan, digest(plan), 'dispatch', {})
                return approval
            with patch('forge_recovery_jobs.Session') as session, patch(
                    'forge_recovery_restore_dispatch.' + ('deploy' if derived else 'dispatch'), side_effect=dispatch):
                session.return_value.__enter__.return_value.unresolved = False
                execute(job)
            if not derived:
                job.arguments['accept_filesystem_errors'] = False
                with patch('forge_recovery_jobs.Session') as session, patch(
                        'forge_recovery_restore_dispatch.dispatch', side_effect=dispatch):
                    session.return_value.__enter__.return_value.unresolved = False
                    with self.assertRaises(PermissionError): execute(job)

    def test_retry_is_explicit_and_does_not_renew(self):
        job = self.registry().get('proof', 'main')
        with patch('forge_recovery_jobs.Session') as session:
            lease = session.return_value.__enter__.return_value
            lease.retry_pending.return_value = {'sequence': 17, 'private': 'omitted'}
            result = execute(job)
            lease.retry_pending.assert_called_once_with()
            lease.renew.assert_not_called()
        self.assertEqual(result, dict(status='acknowledged-pending-lease', sequence=17,
                                     root_written=False, normal_boot_release_authorized=False))

    def test_reconciliation_uses_bound_boot_and_omits_private_paths(self):
        job = replace(self.registry().get('proof', 'main'), operation='reconcile-root',
                      arguments=dict(journal=str(self.root/'journal'), plan_sha256='f'*64))
        with patch('forge_recovery_jobs.Session') as session, patch(
                'forge_recovery_restore_reconcile_host.reconcile', return_value={
                    'status': 'observed', 'evidence_directory': '/private/path', 'root_written': False}) as reconcile:
            lease = session.return_value.__enter__.return_value
            lease.unresolved = False
            self.assertEqual(execute(job), {'status': 'observed', 'root_written': False})
            reconcile.assert_called_once_with(job.probe, job.arguments['journal'], 'f'*64, lease,
                                              observed_boot_id=self.boot)

    def test_backup_is_whole_card_and_uses_durable_session(self):
        job = replace(self.registry().get('proof', 'main'), operation='backup-card',
                      arguments=dict(destination=str(self.root/'new'), cid='a'*32,
                                     disk_id='12345678', device='/dev/mmcblk0'))
        with patch('forge_recovery_jobs.Session') as session, patch('forge_recovery_backup.backup') as backup:
            lease = session.return_value.__enter__.return_value
            lease.unresolved = False
            execute(job)
            backup.assert_called_once_with(job.probe, str(self.root/'new'), 'a'*32, '12345678',
                                           boot_id=self.boot, device='/dev/mmcblk0', lease=lease, whole_card=True)

    def test_hash_job_is_independent_readonly_observation_not_backup(self):
        self.item.update(operation='hash-card', arguments=dict(destination=str(self.root/'new'),
                         cid='a'*32, disk_id='12345678', device='/dev/mmcblk0'))
        self.save()
        job = self.registry().get('proof', 'main')
        with patch('forge_recovery_jobs.Session') as session, patch('forge_recovery_hash.capture',
                return_value=dict(status='verified-offline-storage-digests', is_backup=False,
                                  target_written=False)) as capture:
            lease = session.return_value.__enter__.return_value
            lease.unresolved = False
            self.assertFalse(execute(job)['is_backup'])
            capture.assert_called_once_with(job.probe, str(self.root/'new'), 'a'*32, '12345678',
                                            boot_id=self.boot, device='/dev/mmcblk0', lease=lease)
        (self.root/'new').mkdir()
        with patch('forge_recovery_jobs.Session') as session:
            with self.assertRaises(FileExistsError): execute(job)
            session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
