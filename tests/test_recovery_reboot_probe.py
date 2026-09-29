from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from forge_recovery_reboot_probe import restart, ready, reject_old_worker
from validate_recovery_watchdog import run
from forge_commit_fault_probe import interrupt_inspector


class RecoveryRebootProbeTests(unittest.TestCase):
    def test_inspector_fault_requires_explicit_non_interrupted_transport(self):
        for options in ({}, {'boot_commit_transport':True,'boot_commit_interruption':True}):
            with self.assertRaisesRegex(ValueError,'Inspector interruption'):
                run(None,None,None,None,None,None,None,boot_inspector_interruption=True,**options)
        with self.assertRaisesRegex(ValueError,'Inspector interruption'):
            run(None,None,None,None,None,None,None,boot_inspector_interruption=1)

    def test_inspector_fault_rejects_physical_target_before_access(self):
        probe=SimpleNamespace(mode='physical',inspect=Mock())
        with patch('forge_commit_fault_probe.reconciliation.reconcile') as reconcile:
            with self.assertRaisesRegex(ValueError,'emulator fixture'):
                interrupt_inspector(probe,None,None,None)
            reconcile.assert_not_called()
            probe.inspect.assert_not_called()

    def test_reboot_is_opt_in_disposable_transport_and_cannot_mix_interruption(self):
        for options in ({},{'boot_commit_transport':True,'boot_commit_interruption':True}):
            with self.assertRaisesRegex(ValueError,'Reboot qualification'):
                run(None,None,None,None,None,None,None,boot_commit_reboot=True,**options)

    def test_physical_reboot_or_stale_process_rejected_before_ssh(self):
        for mode,code in (('physical',None),('emulated',0)):
            probe=SimpleNamespace(mode=mode,inspect=Mock())
            with patch('forge_recovery_reboot_probe.subprocess.run') as execute:
                with self.assertRaises(ValueError):
                    restart(SimpleNamespace(poll=lambda:code),[],[],probe,'old',Path('/fixture'),Path('/console'))
                execute.assert_not_called()
                probe.inspect.assert_not_called()
        with self.assertRaises(ValueError):
            reject_old_worker(SimpleNamespace(mode='physical'),None,None,None,None)

    def test_readiness_requires_a_changed_boot_uuid(self):
        process=SimpleNamespace(poll=lambda:None)
        with self.assertRaisesRegex(ValueError,'retained the old'):
            ready(process,SimpleNamespace(inspect=lambda:dict(verification=dict(boot_id='old'))),'old')
        result=dict(verification=dict(boot_id='new'))
        self.assertEqual(ready(process,SimpleNamespace(inspect=lambda:result),'old'),result)

    def test_transient_ssh_failure_waits_on_same_live_process(self):
        result=dict(verification=dict(boot_id='new'))
        process=SimpleNamespace(poll=lambda:None,wait=Mock(side_effect=subprocess.TimeoutExpired('fixture',0.25)))
        probe=SimpleNamespace(inspect=Mock(side_effect=[RuntimeError('not ready'),result]))
        self.assertEqual(ready(process,probe,'old'),result)
        process.wait.assert_called_once()
        self.assertEqual(probe.inspect.call_count,2)


if __name__=='__main__': unittest.main()
