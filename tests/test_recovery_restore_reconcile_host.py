import copy
import fcntl
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forge_recovery_bootplan import digest
from forge_recovery_restore_plan import prepare
from forge_recovery_restore_ledger import request
from forge_target_journal import private_directory
import forge_recovery_restore_reconcile_host as host
import test_recovery_restore_reconcile


class RestoreReconcileHostTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_restore_reconcile.RestoreReconcileTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.source = fixture.fixture.source
        self.directory = self.source.root/'restore-plan'
        self.pin = prepare(self.directory,*fixture.fixture.arguments())['plan_sha256']
        self.plan = fixture.plan
        self.attempt_dir = self.directory/'restore-attempt'
        self.attempt_dir.mkdir(mode=0o700)
        self.health = dict(status='checked',filesystem_consistency_qualified=False,
            restore_authorized=False,target_written=False,repair_performed=False,
            check_returncodes=dict(boot=0,root=4),image=fixture.fixture.manifest['card'])
        self.original = dict(plan_sha256=self.pin,attempt=fixture.attempt,binding=self.plan['binding'],
            packet_sha256='a'*64,source_manifest_sha256=self.plan['source_manifest_sha256'],
            source_health_sha256=digest(self.health),worker_protocol=1)
        self.save(self.attempt_dir/'dispatch.json',self.original)
        self.save(self.attempt_dir/'source-health.json',self.health)
        self.save(self.attempt_dir/'acceptance.json',dict(status='uncertain',root_written='unknown'))
        self.original_bytes = self.snapshot()
        binding = self.plan['binding']
        self.probe = SimpleNamespace(**{key:binding[key] for key in ('nonce','kernel','serial','mode')},
                                     inspect=Mock())
        self.events = []
        self.lease = SimpleNamespace(probe=self.probe,boot_id=binding['boot_id'],owner=binding['lease_owner'],
                                    renew=Mock(side_effect=self.renew))
        self.observer = self.patch('capture_observation',self.observe)
        self.hasher = self.patch('capture_hashes',self.hashes)

    def patch(self,name,effect):
        patcher = patch.object(host,name,side_effect=effect)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def save(self,path,value): self.source.save(path,value)

    def snapshot(self):
        return {path.name:path.read_bytes() for path in self.attempt_dir.iterdir()}

    def query_dirs(self): return sorted(self.directory.glob('reconcile-*'))

    def observe(self,probe,value,errors,**kwargs):
        self.assertIs(probe,self.probe)
        directory = self.directory/('reconcile-'+value['query'])
        self.assertEqual(json.loads((directory/(value['action']+'-request.json')).read_text()),value)
        self.assertEqual((directory/'request.json').stat().st_mode & 0o777,0o600)
        self.events.append(value['action'])
        if value['action']=='fence':
            self.assertIsNone(value['accepted'])
            return copy.deepcopy(self.fixture.fenced)
        self.assertEqual(value['accepted'],dict(fixture='accepted'))
        return copy.deepcopy(self.fixture.inspection)

    def renew(self):
        self.assertEqual(self.events[-1],'fence')
        self.events.append('renew')
        return dict(fixture='accepted')

    def hashes(self,probe,directory,cid,disk_id,**kwargs):
        self.assertIs(kwargs['lease'],self.lease)
        self.assertEqual(kwargs['boot_id'],self.lease.boot_id)
        self.events.append('hash')
        directory.mkdir(mode=0o700)
        self.save(directory/'plan.json',self.fixture.hash_plan)
        self.save(directory/'acceptance.json',self.fixture.hashes)
        return copy.deepcopy(self.fixture.hashes)

    def reconcile(self,**kwargs):
        return host.reconcile(self.probe,self.directory,self.pin,self.lease,**kwargs)

    def test_fresh_evidence_order_and_original_attempt_immutability(self):
        result = self.reconcile()
        self.assertEqual(self.events,['fence','renew','inspect','hash'])
        self.assertEqual(result['status'],'reconciled-before')
        self.assertFalse(result['root_write_authorized'])
        self.assertFalse(result['normal_boot_release_authorized'])
        self.assertTrue(result['requires_new_recovery_boot'])
        self.assertEqual(self.snapshot(),self.original_bytes)
        self.assertEqual(json.loads((self.query_dirs()[0]/'acceptance.json').read_text()),result)
        self.probe.inspect.assert_called_once_with(expected_boot_id=self.lease.boot_id)

    def test_repeat_observation_gets_new_journal_not_new_restore_attempt(self):
        first = self.reconcile()
        second = self.reconcile()
        self.assertNotEqual(first['query'],second['query'])
        self.assertEqual(len(self.query_dirs()),2)
        self.assertEqual(self.snapshot(),self.original_bytes)

    def test_wrong_original_or_health_pin_stops_before_contact(self):
        for field in ('source_manifest_sha256','source_health_sha256','packet_sha256'):
            altered = dict(self.original,**{field:'invalid'})
            self.save(self.attempt_dir/'dispatch.json',altered)
            with self.assertRaises(ValueError): self.reconcile()
        self.observer.assert_not_called()
        self.lease.renew.assert_not_called()
        self.assertEqual(self.query_dirs(),[])

    def test_old_packet_pin_is_historical_not_rebuilt_from_current_sources(self):
        self.assertEqual(self.reconcile()['status'],'reconciled-before')
        retained = json.loads((self.query_dirs()[0]/'request.json').read_text())
        self.assertEqual(retained['original_dispatch']['packet_sha256'],'a'*64)

    def test_fence_failure_never_renews_and_retains_incomplete_evidence(self):
        self.observer.side_effect = EOFError('disconnected')
        with self.assertRaisesRegex(EOFError,'disconnected'): self.reconcile()
        result = json.loads((self.query_dirs()[0]/'acceptance.json').read_text())
        self.assertEqual(result['status'],'incomplete')
        self.assertIn('EOFError',result['error'])
        self.lease.renew.assert_not_called()
        self.hasher.assert_not_called()
        self.assertEqual(self.snapshot(),self.original_bytes)

    def test_malformed_fence_is_retained_but_stops_before_renewal(self):
        self.fixture.fenced['attempt'] = '0'*32
        with self.assertRaises(ValueError): self.reconcile()
        self.assertTrue((self.query_dirs()[0]/'fence.json').is_file())
        self.lease.renew.assert_not_called()
        self.hasher.assert_not_called()

    def test_malformed_inspection_stops_before_hashing(self):
        self.fixture.inspection['boot_unmounted'] = False
        with self.assertRaises(ValueError): self.reconcile()
        self.assertTrue((self.query_dirs()[0]/'inspect.json').is_file())
        self.hasher.assert_not_called()

    def test_new_boot_requires_explicit_binding_and_never_borrows_completion(self):
        boot = '99999999-1234-1234-1234-123456789abc'
        self.lease.boot_id = boot
        with self.assertRaises(ValueError): self.reconcile()
        self.assertEqual(self.query_dirs(),[])
        self.fixture.hash_plan['boot_id'] = boot
        self.fixture.hashes['digests']['boot_id'] = boot
        self.fixture.inspection['boot_id'] = boot
        self.fixture.fenced['boot_id'] = boot
        self.fixture.fenced['outcome'] = dict(status='previous-boot-ended',
            previous_boot_id=self.plan['binding']['boot_id'],request=request(self.plan,self.pin,self.fixture.attempt))
        result = self.reconcile(observed_boot_id=boot)
        self.assertFalse(result['original_attempt_completion_verified'])
        self.assertFalse(result['requires_new_recovery_boot'])
        self.assertEqual(self.snapshot(),self.original_bytes)

    def test_hash_conflict_is_classified_without_release_authority(self):
        self.fixture.hashes['digests']['prefix']['sha256'] = '0'*64
        result = self.reconcile()
        self.assertEqual(result['status'],'reconciled-conflict')
        self.assertIn('protected-prefix-changed',result['conflicts'])
        self.assertFalse(result['normal_boot_release_authorized'])

    def test_different_retained_hash_or_changed_final_boot_leaves_incomplete(self):
        original = self.hashes
        def changed(*args,**kwargs):
            result = original(*args,**kwargs)
            result['digests']['root']['sha256'] = '0'*64
            return result
        self.hasher.side_effect = changed
        with self.assertRaisesRegex(ValueError,'Retained restore hashes'): self.reconcile()
        self.hasher.side_effect = self.hashes
        self.probe.inspect.side_effect = ValueError('Boot changed')
        with self.assertRaisesRegex(ValueError,'Boot changed'): self.reconcile()
        for directory in self.query_dirs():
            self.assertEqual(json.loads((directory/'acceptance.json').read_text())['status'],'incomplete')

    def test_competing_host_dispatch_lock_stops_before_contact(self):
        fd = private_directory(self.directory)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): self.reconcile()
        finally:
            os.close(fd)
        self.observer.assert_not_called()

    def test_journal_failure_does_not_hide_transport_error(self):
        real = host.write_record
        def write(fd,name,value):
            if name=='acceptance.json': raise OSError('disk full')
            return real(fd,name,value)
        self.observer.side_effect = EOFError('lost target')
        with patch.object(host,'write_record',side_effect=write):
            with self.assertRaisesRegex(EOFError,'lost target'): self.reconcile()
        self.assertEqual(self.snapshot(),self.original_bytes)


if __name__=='__main__': unittest.main()
