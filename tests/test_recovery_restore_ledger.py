import copy
import multiprocessing
from pathlib import Path
from unittest.mock import Mock,patch
import unittest

from forge_recovery_bootplan import digest
from forge_recovery_restore_ledger import run,fence,provision
import test_recovery_restore_plan


def delayed_worker(directory,plan,pin,attempt,ready,proceed,results):
    ready.set()
    if not proceed.wait(10): raise RuntimeError('Worker was not released')
    with patch('forge_recovery_restore_ledger.Path.read_text',return_value=plan['binding']['boot_id']), \
         patch('forge_recovery_restore_ledger.ROOT',Path(directory).parent):
        try:
            run(directory,plan,pin,attempt,lambda:dict(unexpected=True))
        except RuntimeError as exc: results.put(str(exc))
        else: results.put('unexpected success')


class RestoreLedgerTests(unittest.TestCase):
    def setUp(self):
        fixture=test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.plan=fixture.compile()
        self.pin=digest(self.plan)
        self.boot=self.plan['binding']['boot_id']
        self.attempt='a'*32
        self.directory=fixture.source.root/self.boot
        self.directory.mkdir(mode=0o700)
        root_patch=patch('forge_recovery_restore_ledger.ROOT',self.directory.parent)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.boot_reader=patch('forge_recovery_restore_ledger.Path.read_text',return_value=self.boot)
        self.boot_reader.start()
        self.addCleanup(self.boot_reader.stop)
        self.result=dict(status='root-restore-verified',plan_sha256=self.pin,
            source_manifest_sha256=self.plan['source_manifest_sha256'],boot_id=self.boot,
            root=self.plan['root_after'],prefix=self.plan['prefix_guard'],suffix=self.plan['suffix_guard'],
            bytes_written=self.plan['root_after']['bytes'],protected_ranges_verified=True,boot_unmounted=True,
            normal_boot_release_authorized=False,physical_restore_qualified=False)

    def execute(self,effect,plan=None,attempt=None):
        plan=self.plan if plan is None else plan
        return run(self.directory,plan,digest(plan),attempt or self.attempt,effect)

    def test_success_receipt_replays_without_repeating_writes(self):
        effect=Mock(return_value=self.result)
        self.assertEqual(self.execute(effect),self.result)
        self.assertEqual(self.execute(effect),self.result)
        effect.assert_called_once()
        observed=fence(self.directory,self.plan,self.pin,self.attempt)
        self.assertEqual(observed['status'],'completed')
        self.assertFalse(observed['requires_new_recovery_boot'])
        self.assertFalse(observed['normal_boot_release_authorized'])

    def test_incomplete_attempt_blocks_different_plan_and_requires_new_boot(self):
        with self.assertRaises(RuntimeError): self.execute(Mock(side_effect=RuntimeError('partial write')))
        other=copy.deepcopy(self.plan)
        other['root_before']['sha256']='f'*64
        effect=Mock()
        for plan,attempt in ((self.plan,self.attempt),(self.plan,'b'*32),(other,'c'*32)):
            with self.assertRaises(RuntimeError): self.execute(effect,plan,attempt)
        effect.assert_not_called()
        observed=fence(self.directory,self.plan,self.pin,self.attempt)
        self.assertEqual(observed['status'],'incomplete')
        self.assertTrue(observed['requires_new_recovery_boot'])
        self.assertFalse((self.directory/('receipt-'+self.attempt+'.json')).exists())
        self.assertFalse((self.directory/('fence-'+self.attempt+'.json')).exists())

    def test_invalid_root_or_release_receipt_leaves_incomplete_intent(self):
        for index,change in enumerate((dict(normal_boot_release_authorized=True),dict(bytes_written=True),
                                       dict(protected_ranges_verified=False),dict(root=dict(self.plan['root_after'],sha256='0'*64)))):
            # Each case gets a separate fixture namespace. Never delete an
            # incomplete intent to let a second write through the same ledger.
            directory=self.directory/str(index)/self.boot
            directory.mkdir(mode=0o700,parents=True)
            with patch('forge_recovery_restore_ledger.ROOT',directory.parent),self.assertRaises(ValueError):
                run(directory,self.plan,self.pin,self.attempt,lambda:dict(self.result,**change))
            self.assertFalse((directory/('receipt-'+self.attempt+'.json')).exists())
            self.assertTrue((directory/('intent-'+self.attempt+'.json')).exists())

    def test_running_effect_cannot_be_fenced(self):
        def effect():
            with self.assertRaises(BlockingIOError): fence(self.directory,self.plan,self.pin,self.attempt)
            return self.result
        self.execute(effect)

    def test_effect_cannot_rebind_the_pinned_completion_root(self):
        def effect():
            self.plan['root_after']['sha256']='0'*64
            return self.result
        with self.assertRaisesRegex(ValueError,'completion receipt differs'): self.execute(effect)
        self.assertFalse((self.directory/('receipt-'+self.attempt+'.json')).exists())

    def test_stale_boot_rejected_before_any_journal_access_or_effect(self):
        with patch('forge_recovery_restore_ledger.Path.read_text',return_value='01234567-1234-1234-1234-0123456789ab'), \
             patch('forge_recovery_restore_ledger.ledger_run') as transaction, \
             patch('forge_recovery_restore_ledger.os.open') as opened:
            with self.assertRaises(ValueError): self.execute(Mock())
            with self.assertRaises(ValueError): provision(self.boot)
            transaction.assert_not_called()
            opened.assert_not_called()

    def test_wrong_pin_and_authorizing_plan_are_rejected(self):
        with self.assertRaises(ValueError): run(self.directory,self.plan,'0'*64,self.attempt,Mock())
        plan=copy.deepcopy(self.plan)
        plan['root_write_authorized']=True
        with self.assertRaises(ValueError): self.execute(Mock(),plan)

    def test_another_directory_cannot_bypass_boot_wide_fencing(self):
        other=self.directory/'other'/self.boot
        other.mkdir(mode=0o700,parents=True)
        with patch('forge_recovery_restore_ledger.ledger_run') as backend:
            with self.assertRaises(ValueError): run(other,self.plan,self.pin,self.attempt,Mock())
            backend.assert_not_called()

    def test_separate_delayed_process_is_fenced_before_effect(self):
        context=multiprocessing.get_context('spawn')
        ready,proceed=context.Event(),context.Event()
        results=context.Queue()
        worker=context.Process(target=delayed_worker,args=(self.directory,self.plan,self.pin,self.attempt,ready,proceed,results))
        worker.start()
        try:
            self.assertTrue(ready.wait(10))
            observed=fence(self.directory,self.plan,self.pin,self.attempt)
            self.assertEqual(observed['status'],'fenced-not-started')
            proceed.set()
            self.assertIn('fenced before starting',results.get(timeout=10))
            worker.join(10)
            self.assertEqual(worker.exitcode,0)
            self.assertFalse((self.directory/('intent-'+self.attempt+'.json')).exists())
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
            results.close()
            results.join_thread()


if __name__=='__main__': unittest.main()
