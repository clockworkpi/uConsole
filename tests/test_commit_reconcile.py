from contextlib import nullcontext
import copy
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

import forge_recovery_commit_inspect as inspect
from forge_recovery_commit_reconcile import check_fence, check_inspection, expected_receipt, Reply, reconcile
from forge_recovery_commit_ledger import request
from forge_recovery_bootcommit import files_commit, validate, plan_digest
from target_test_support import linux_target
import test_recovery_bootcommit
import test_ram_identity
import test_commit_transport
from forge_target_journal import private_directory,write_record


class CommitReconcileTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_recovery_bootcommit.BootCommitTests()
        self.fixture.setUp()
        self.plan,self.pin=self.fixture.plan,self.fixture.pin

    @linux_target
    def test_read_only_classification_before_and_after(self):
        with tempfile.TemporaryDirectory() as directory:
            point=Path(directory)
            plan=self.fixture.materialize(point)
            before={path.name:path.read_bytes() for path in point.iterdir()}
            self.assertEqual(inspect.inspect_files(plan,point)['status'],'before')
            self.assertEqual({path.name:path.read_bytes() for path in point.iterdir()},before)
            files_commit(plan,point)
            after={path.name:path.read_bytes() for path in point.iterdir()}
            self.assertEqual(inspect.inspect_files(plan,point)['status'],'after')
            self.assertEqual({path.name:path.read_bytes() for path in point.iterdir()},after)

    @linux_target
    def test_staging_conflicts_and_unrelated_changes_are_preserved(self):
        for kind in ('stage','dependency','image'):
            with tempfile.TemporaryDirectory() as directory:
                point=Path(directory)
                plan=self.fixture.materialize(point)
                path=point/('.uconsole-forge-'+plan['stage_token'] if kind=='stage' else
                            'cmdline.txt' if kind=='dependency' else Path(plan['image_dependency']['path']).name)
                path.write_bytes(b'unknown contents must remain')
                path.chmod(0o700)
                before={path.name:path.read_bytes() for path in point.iterdir()}
                result=inspect.inspect_files(plan,point)
                self.assertEqual(result['status'],'conflict')
                self.assertEqual({path.name:path.read_bytes() for path in point.iterdir()},before)

    def test_identity_allows_only_exact_separately_checked_stale_mount(self):
        fixture=test_ram_identity.RamIdentityTests()
        fixture.setUp()
        record=copy.deepcopy(fixture.record)
        record['cmdline']=record['cmdline'].replace(fixture.nonce,self.plan['binding']['nonce'])
        point='/run/forge-boot-commit-'+self.plan['stage_token']
        row=f'3 1 179:1 / {point} rw,nosuid,nodev,noexec - vfat /dev/mmcblk0p1 rw\n'
        record['mountinfo']+=row
        with patch.object(inspect,'observe',return_value=record),patch.object(inspect,'check_mount') as mount:
            self.assertTrue(inspect.identity(self.plan,stale_mount=True))
            mount.assert_called_once_with(point,'/dev/mmcblk0p1')
            with self.assertRaises(ValueError): inspect.identity(self.plan)
            record['mountinfo']+='4 1 179:2 / /other rw - ext4 /dev/mmcblk0p2 rw\n'
            with self.assertRaises(ValueError): inspect.identity(self.plan,stale_mount=True)

    def test_live_ledger_prevents_any_cleanup(self):
        with patch.object(inspect,'layout'),patch.object(inspect.ledger,'provision'), \
                patch.object(inspect.ledger,'fence',side_effect=BlockingIOError('still running')), \
                patch.object(inspect,'ensure_lock_directory') as locks,patch.object(inspect.subprocess,'run') as command:
            with self.assertRaises(BlockingIOError): inspect.cleanup(self.plan,self.pin,'b'*32)
            locks.assert_not_called()
            command.assert_not_called()

    def test_fenced_dead_worker_mount_is_unmounted_without_file_repair(self):
        outcome=dict(status='incomplete')
        with patch.object(inspect,'layout',return_value=self.plan['binding']['extent']), \
                patch.object(inspect,'identity',return_value=True),patch.object(inspect.ledger,'provision'), \
                patch.object(inspect.ledger,'fence',return_value=outcome),patch.object(inspect,'ensure_lock_directory'), \
                patch.object(inspect,'target_lock',return_value=nullcontext()), \
                patch.object(inspect,'claim_root',return_value=nullcontext()),patch.object(inspect,'check_mount'), \
                patch.object(inspect.subprocess,'run') as command,patch.object(os.path,'ismount',return_value=False), \
                patch.object(Path,'rmdir') as rmdir:
            result=inspect.cleanup(self.plan,self.pin,'b'*32)
            command.assert_called_once_with(['umount','/run/forge-boot-commit-'+self.plan['stage_token']],check=True,timeout=10)
            rmdir.assert_called_once()
            self.assertTrue(result['stale_boot_unmounted'])
            self.assertTrue(result['pending_boot_writes_may_have_flushed'])
            self.assertFalse(result['root_written'])

    def test_strict_fence_receipt_matches_old_attempt_and_complete_effect(self):
        attempt='b'*32
        for status in ('fenced-not-started','incomplete','completed'):
            outcome=dict(status=status,request=request(self.plan,self.pin,attempt))
            if status=='completed': outcome['result']=expected_receipt(self.plan,self.pin)
            result=dict(status='fenced',plan_sha256=self.pin,boot_id=self.plan['binding']['boot_id'],attempt=attempt,
                outcome=outcome,stale_boot_unmounted=False,pending_boot_writes_may_have_flushed=False,root_written=False)
            self.assertEqual(check_fence(result,self.plan,self.pin,attempt),result)
            changed=copy.deepcopy(result)
            changed['outcome']['request']['nonce']='c'*32
            with self.assertRaises(ValueError): check_fence(changed,self.plan,self.pin,attempt)

    def test_inspection_requires_full_file_set_and_consistent_classification(self):
        result=dict(status='after',files=[dict(path=path,state='after' if path.endswith('/config.txt') else 'unchanged')
            for path in self.plan['guarded_paths']],image='matched',stage='absent',plan_sha256=self.pin,
            boot_id=self.plan['binding']['boot_id'],prefix=dict(offset=0,bytes=4096,sha256='d'*64),
            read_only=True,boot_unmounted=True,root_written=False,deployment_authorized=False)
        self.assertEqual(check_inspection(result,self.plan,self.pin),result)
        for change in ({'files':result['files'][:-1]},{'stage':'matches-desired'},{'read_only':1},{'status':'before'}):
            with self.assertRaises(ValueError): check_inspection(dict(result,**change),self.plan,self.pin)
        reply=Reply('q',lambda value:check_inspection(value,self.plan,self.pin))
        self.assertFalse(reply.may_renew)
        reply.consume(dict(type='complete',attempt='q',result=result))
        with self.assertRaises(ValueError): reply.consume(dict(type='complete',attempt='q',result=result))

    def host_fixture(self,directory,protocol=2):
        plan,pin,probe,lease=test_commit_transport.CommitTransportTests().journal_fixture(directory)
        attempt='b'*32
        child=directory/'commit-attempt'
        child.mkdir(mode=0o700)
        fd=private_directory(child)
        try:
            record=dict(plan_sha256=pin,attempt=attempt,binding=plan['binding'],lease_owner=lease.owner)
            if protocol is not None: record['worker_protocol']=protocol
            write_record(fd,'dispatch.json',record)
            write_record(fd,'acceptance.json',dict(status='uncertain'))
        finally: os.close(fd)
        return plan,pin,probe,lease,attempt

    def test_host_reconciliation_preserves_uncertain_attempt_and_requires_matching_hashes(self):
        for changed in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                directory=Path(directory)
                plan,pin,probe,lease,attempt=self.host_fixture(directory)
                original=(directory/'commit-attempt/acceptance.json').read_bytes()
                def transfer(argv,request,protocol,**options):
                    if request['action']=='fence':
                        result=dict(status='fenced',plan_sha256=pin,boot_id=plan['binding']['boot_id'],attempt=attempt,
                            outcome=dict(status='incomplete',request=request_ledger),stale_boot_unmounted=True,
                            pending_boot_writes_may_have_flushed=True,root_written=False)
                    else:
                        result=dict(status='after',files=[dict(path=path,state='after' if path.endswith('/config.txt')
                            else 'unchanged') for path in plan['guarded_paths']],image='matched',stage='absent',
                            plan_sha256=pin,boot_id=plan['binding']['boot_id'],prefix=plan['prefix_guard'],
                            read_only=True,boot_unmounted=True,root_written=False,deployment_authorized=False)
                    protocol.consume(dict(type='complete',attempt=request['query'],result=result))
                    return protocol.result
                request_ledger=request(plan,pin,attempt)
                hashes=dict(root=plan['root_guard'],suffix=plan['suffix_guard'],prefix=plan['prefix_guard'])
                if changed: hashes['root']=dict(hashes['root'],sha256='f'*64)
                with patch('forge_recovery_commit_reconcile.exchange',side_effect=transfer), \
                        patch('forge_recovery_commit_reconcile.capture',return_value=dict(digests=hashes)):
                    if changed:
                        with self.assertRaisesRegex(ValueError,'Independent reconciliation hashes'):
                            reconcile(probe,directory,pin,lease)
                    else:
                        result=reconcile(probe,directory,pin,lease)
                        self.assertEqual(result['status'],'reconciled-after')
                        self.assertFalse(result['normal_boot_release_authorized'])
                self.assertEqual((directory/'commit-attempt/acceptance.json').read_bytes(),original)
                evidence=list(directory.glob('reconcile-*/acceptance.json'))
                self.assertEqual(len(evidence),1)
                self.assertEqual(json.loads(evidence[0].read_text())['status'],'incomplete' if changed else 'reconciled-after')

    def test_legacy_unfenced_worker_journal_is_not_claimed_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            plan,pin,probe,lease,attempt=self.host_fixture(directory,protocol=None)
            with patch('forge_recovery_commit_reconcile.exchange') as exchange:
                with self.assertRaisesRegex(ValueError,'Original commit attempt binding'):
                    reconcile(probe,directory,pin,lease)
                exchange.assert_not_called()

    def test_new_boot_view_preserves_original_plan_and_cannot_execute_under_old_pin(self):
        before=copy.deepcopy(self.plan)
        new='99999999-1234-1234-1234-123456789abc'
        view=inspect.observation_plan(self.plan,self.pin,new)
        self.assertEqual(self.plan,before)
        self.assertEqual(plan_digest(self.plan),self.pin)
        expected=copy.deepcopy(before)
        expected['binding']['boot_id']=new
        self.assertEqual(view,expected)
        with self.assertRaises(ValueError): validate(view,self.pin)
        with self.assertRaises(ValueError): inspect.observation_plan(self.plan,self.pin,'bad')

    def test_reboot_proof_does_not_create_a_fake_new_boot_ledger_receipt(self):
        new='99999999-1234-1234-1234-123456789abc'
        with patch.object(inspect,'layout',return_value=self.plan['binding']['extent']) as layout, \
                patch.object(inspect,'identity',return_value=False),patch.object(inspect.ledger,'provision') as provision, \
                patch.object(inspect.ledger,'fence') as fence,patch.object(inspect,'ensure_lock_directory'), \
                patch.object(inspect,'target_lock',return_value=nullcontext()), \
                patch.object(inspect,'claim_root',return_value=nullcontext()):
            result=inspect.cleanup(self.plan,self.pin,'b'*32,observed_boot_id=new)
            provision.assert_not_called()
            fence.assert_not_called()
            self.assertEqual(layout.call_args_list[0].kwargs,dict(stale_mount=False))
            self.assertEqual(layout.call_args_list[0].args[0]['binding']['boot_id'],new)
            self.assertEqual(result['outcome']['status'],'previous-boot-ended')
            self.assertFalse(result['stale_boot_unmounted'])
            check_fence(result,self.plan,self.pin,'b'*32,observed_boot_id=new)
            with self.assertRaises(ValueError): check_fence(result,self.plan,self.pin,'b'*32)
            result['stale_boot_unmounted']=result['pending_boot_writes_may_have_flushed']=True
            with self.assertRaises(ValueError): check_fence(result,self.plan,self.pin,'b'*32,observed_boot_id=new)

    def test_new_lease_cannot_silently_rebind_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            plan,pin,probe,lease,attempt=self.host_fixture(directory)
            lease.boot_id='99999999-1234-1234-1234-123456789abc'
            with patch('forge_recovery_commit_reconcile.exchange') as exchange:
                with self.assertRaisesRegex(ValueError,'probe/lease differs'):
                    reconcile(probe,directory,pin,lease)
                exchange.assert_not_called()

    def test_inspector_hangup_unwinds_cleanup_and_ignores_repeated_hangups(self):
        cleaned=[]
        with patch.object(signal,'signal') as installed:
            def interrupted(*args,**kwargs):
                try:
                    handler=installed.call_args_list[0].args[1]
                    handler(signal.SIGHUP,None)
                finally:
                    cleaned.append('unmounted')
            with patch.object(inspect,'inspect',side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError,'inspection disconnected'):
                    inspect.run(dict(action='inspect',plan=self.plan,pin=self.pin,owner='b'*64,lease={},query='c'*32))
            self.assertEqual(cleaned,['unmounted'])
            self.assertEqual(installed.call_args_list[-2].args,(signal.SIGHUP,signal.SIG_IGN))
            self.assertEqual(installed.call_args_list[-1].args,(signal.SIGTERM,signal.SIG_IGN))


if __name__=='__main__': unittest.main()
