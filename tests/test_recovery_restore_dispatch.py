import copy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forge_recovery_bootplan import digest
import forge_recovery_restore_dispatch as dispatch
from forge_recovery_restore_plan import prepare
import test_recovery_restore_plan


class RestoreDispatchTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.directory = fixture.source.root/'restore-plan'
        self.pin = prepare(self.directory,*fixture.arguments())['plan_sha256']
        self.plan = fixture.compile()
        binding = self.plan['binding']
        self.probe = SimpleNamespace(**{key:binding[key] for key in ('nonce','kernel','serial','mode')},
            inspect_storage=Mock(return_value=dict(extent=binding['extent'])),inspect=Mock())
        self.lease = SimpleNamespace(probe=self.probe,boot_id=binding['boot_id'],owner=binding['lease_owner'],
                                     renew=Mock(return_value={}))
        self.health_dir = fixture.source.root/'health'
        self.health_dir.mkdir(mode=0o700)
        self.health = dict(status='checked',filesystem_consistency_qualified=False,restore_authorized=False,
            target_written=False,repair_performed=False,check_returncodes=dict(boot=0,root=4),image=fixture.manifest['card'])
        self.save_health()
        self.patches = {}
        for name,value in (('capture_selection',Mock()),('argv',Mock(return_value=['fixture-worker'])),
                           ('start',Mock()),('exchange',Mock(side_effect=self.exchange)),
                           ('run_process',Mock(side_effect=lambda argv,op,errors:op(None,lambda:None,lambda:None)))):
            patcher = patch.object(dispatch,name,value)
            self.patches[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def save_health(self):
        self.fixture.source.save(self.health_dir/'acceptance.json',self.health)
        self.health_pin = digest(self.health)

    def authorize(self,plan,pin,phase,health):
        self.assertEqual(health,self.health)
        return dict(restore=pin,boot_id=plan['binding']['boot_id'],phase=phase,
            source_health_sha256=self.health_pin,
            accept_source_filesystem_errors=not health['filesystem_consistency_qualified'])

    def exchange(self,channel,plan,pin,manifest,attempt,produce,*,authorize,renew,record,**_):
        for phase in ('acquire','inspect','write'):
            approval = authorize(plan,pin,phase)
            record('owner-approval',approval)
            renew()
        chunks = []
        receipt = produce(lambda chunk,data:chunks.append(data))
        self.assertEqual(b''.join(chunks),self.fixture.source.root_bytes)
        record('source-verified',receipt)
        return dict(status='root-restore-verified',plan_sha256=pin,
            source_manifest_sha256=plan['source_manifest_sha256'],boot_id=plan['binding']['boot_id'],
            root=plan['root_after'],prefix=plan['prefix_guard'],suffix=plan['suffix_guard'],
            bytes_written=plan['root_after']['bytes'],protected_ranges_verified=True,boot_unmounted=True,
            normal_boot_release_authorized=False,physical_restore_qualified=False)

    def run_dispatch(self, **kwargs):
        return dispatch.dispatch(self.probe,self.directory,self.pin,self.lease,self.fixture.source.source,
            self.health_dir,self.health_pin,authorize=kwargs.get('authorize',self.authorize))

    def test_durable_dispatch_retains_explicit_nonclean_health_approval(self):
        result = self.run_dispatch()
        self.assertEqual(result['status'],'acknowledged')
        directory = self.directory/'restore-attempt'
        retained = json.loads((directory/'acceptance.json').read_text())
        self.assertEqual(result,retained)
        events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
        self.assertEqual([value['sequence'] for value in events],list(range(len(events))))
        approvals = [value['value'] for value in events if value['kind']=='source-health-approval']
        self.assertEqual([value['phase'] for value in approvals],['acquire','inspect','write'])
        self.assertTrue(all(value['accept_source_filesystem_errors'] for value in approvals))
        self.assertEqual((directory/'events.jsonl').stat().st_mode & 0o777,0o600)
        self.assertFalse(result['normal_boot_release_authorized'])

    def test_existing_attempt_is_not_retried_or_probed(self):
        self.run_dispatch()
        self.probe.inspect_storage.reset_mock()
        self.patches['capture_selection'].reset_mock()
        with self.assertRaises(FileExistsError): self.run_dispatch()
        self.probe.inspect_storage.assert_not_called()
        self.patches['capture_selection'].assert_not_called()
        self.assertEqual(self.patches['run_process'].call_count,1)

    def test_lost_transport_retains_uncertainty_and_blocks_redispatch(self):
        self.patches['run_process'].side_effect = EOFError('connection lost')
        with self.assertRaises(EOFError): self.run_dispatch()
        value = json.loads((self.directory/'restore-attempt/acceptance.json').read_text())
        self.assertEqual(value['status'],'uncertain')
        self.assertEqual(value['root_written'],'unknown')
        with self.assertRaises(FileExistsError): self.run_dispatch()

    def test_nonclean_source_requires_explicit_owner_acknowledgement(self):
        def authorize(*args):
            value = self.authorize(*args)
            value['accept_source_filesystem_errors'] = False
            return value
        with self.assertRaisesRegex(ValueError,'source-health approval'):
            self.run_dispatch(authorize=authorize)
        self.assertEqual(self.lease.renew.call_count,0)

    def test_different_image_health_or_changed_pin_stops_before_target_contact(self):
        self.health = copy.deepcopy(self.health)
        self.health['image']['sha256'] = '0'*64
        self.save_health()
        with self.assertRaisesRegex(ValueError,'health evidence'): self.run_dispatch()
        self.patches['capture_selection'].assert_not_called()
        self.assertFalse((self.directory/'restore-attempt').exists())

    def test_changed_source_archive_stops_before_target_contact(self):
        path = self.fixture.source.source/'card.img.gz'
        path.write_bytes(path.read_bytes()+b'!')
        with self.assertRaises((ValueError,PermissionError)): self.run_dispatch()
        self.patches['capture_selection'].assert_not_called()

    def test_wrong_boot_or_layout_never_launches_worker(self):
        self.lease.boot_id = '0'*36
        with self.assertRaises(ValueError): self.run_dispatch()
        self.lease.boot_id = self.plan['binding']['boot_id']
        self.probe.inspect_storage.return_value = dict(extent={})
        with self.assertRaises(ValueError): self.run_dispatch()
        self.patches['run_process'].assert_not_called()

    def test_post_completion_boot_change_stays_uncertain(self):
        self.probe.inspect.side_effect = ValueError('Boot changed')
        with self.assertRaisesRegex(ValueError,'Boot changed'): self.run_dispatch()
        value = json.loads((self.directory/'restore-attempt/acceptance.json').read_text())
        self.assertEqual(value['status'],'uncertain')

    def test_fsync_failure_invalidates_event_writer(self):
        path = self.fixture.source.root/'events'
        path.mkdir(mode=0o700)
        fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        self.addCleanup(os.close,fd)
        writer = dispatch.Events(fd)
        self.addCleanup(writer.close)
        with patch.object(dispatch.os,'fsync',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): writer.record('intent',{})
        with self.assertRaises(RuntimeError): writer.record('retry',{})

    def test_oversized_event_is_not_written(self):
        path = self.fixture.source.root/'events'
        path.mkdir(mode=0o700)
        fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        self.addCleanup(os.close,fd)
        writer = dispatch.Events(fd)
        self.addCleanup(writer.close)
        with self.assertRaises(ValueError): writer.record('oversized','x'*9000)
        self.assertEqual((path/'events.jsonl').read_bytes(),b'')


if __name__=='__main__': unittest.main()
