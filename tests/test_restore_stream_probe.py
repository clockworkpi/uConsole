import hashlib
import json
import subprocess
import sys
import unittest
import copy

from forge_recovery_restore_bootstrap import BOOTSTRAP, framed
from forge_restore_stream_probe import packet, checked, check_completed_fence, check_incomplete_fence, expected_partial
import test_recovery_restore_reconcile
from validate_recovery_watchdog import run


class RestoreStreamProbeTests(unittest.TestCase):
    def test_physical_or_wrong_device_or_large_fixture_rejected(self):
        binding=dict(mode='emulated',serial=None,device='/dev/mmcblk1',extent=dict(disk_bytes=128*1024*1024))
        checked(binding)
        for value in (dict(mode='physical'),dict(device='/dev/mmcblk0'),dict(serial='0'*16),
                      dict(extent=dict(disk_bytes=True)),dict(extent=dict(disk_bytes=256*1024*1024))):
            with self.assertRaises(ValueError): checked(dict(binding,**value))

    def test_requires_full_disposable_hold_transport(self):
        for options in ({},{'boot_commit_transport':True,'boot_commit_interruption':True}):
            with self.assertRaisesRegex(ValueError,'Root restore stream'):
                run(None,None,None,None,None,None,None,root_restore_stream=True,**options)
        with self.assertRaisesRegex(ValueError,'Root restore stream'):
            run(None,None,None,None,None,None,None,root_restore_stream=1)

    def test_isolated_fixture_imports_but_rejects_physical_packet_before_target_access(self):
        value=dict(plan=dict(binding=dict(mode='emulated',serial=None,device='/dev/mmcblk1',
                                         extent=dict(disk_bytes=128*1024*1024))))
        data,_=packet(value)
        decoded=json.loads(data)
        decoded['request'].update(fixture_action='restore',fixture_lease=None)
        decoded['request']['plan']['binding']['mode']='physical'
        data=json.dumps(decoded).encode()
        pin=hashlib.sha256(data).hexdigest()
        bootstrap=BOOTSTRAP.replace('from forge_recovery_restore_worker import run',
                                    'from forge_restore_stream_fixture import run')
        result=subprocess.run([sys.executable,'-I','-S','-c',bootstrap,pin],
                              input=framed(data,pin),capture_output=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'Restore fixture requires a pinned small disposable emulator card',result.stderr)
        self.assertEqual(json.loads(result.stdout)['packet_sha256'],pin)

    def test_lost_completion_requires_stream_and_boolean_switch(self):
        for value in (True,1,'yes'):
            with self.assertRaisesRegex(ValueError,'Restore completion loss'):
                run(None,None,None,None,None,None,None,root_restore_lost_completion=value)

    def test_interruption_requires_distinct_stream_reboot_trial(self):
        for options in ({},{'root_restore_stream':True,'root_restore_lost_completion':True},
                        {'root_restore_stream':True,'boot_commit_reboot':True},
                        {'root_restore_stream':True,'boot_inspector_interruption':True}):
            with self.assertRaisesRegex(ValueError,'Restore interruption'):
                run(None,None,None,None,None,None,None,root_restore_interruption=True,**options)
        with self.assertRaisesRegex(ValueError,'Restore interruption'):
            run(None,None,None,None,None,None,None,root_restore_interruption=1)

    def test_incomplete_fence_requires_new_boot_and_exact_original_attempt(self):
        fixture=test_recovery_restore_reconcile.RestoreReconcileTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        value=dict(type='fixture-fence',plan_sha256=fixture.pin,attempt=fixture.attempt,
                   boot_id=fixture.boot,outcome=fixture.fenced['outcome'])
        check_incomplete_fence(value,fixture.plan,fixture.pin,fixture.attempt)
        for change in (dict(requires_new_recovery_boot=False),dict(root_write_authorized=True),
                       dict(status='completed'),dict(boot_id='bad')):
            altered=copy.deepcopy(value)
            altered['outcome'].update(change)
            with self.assertRaises(ValueError):
                check_incomplete_fence(altered,fixture.plan,fixture.pin,fixture.attempt)

    def test_partial_root_prediction_uses_verified_backup_and_only_second_sector_change(self):
        fixture=test_recovery_restore_reconcile.RestoreReconcileTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source=fixture.fixture.source
        manifest=fixture.fixture.manifest
        value=expected_partial(source.source,manifest,fixture.fixture.source_pin)
        offset=manifest['chunks'][1]['offset']
        original=source.root_bytes
        changed=original[:offset]+bytes(byte^255 for byte in original[offset:offset+512])+original[offset+512:]
        self.assertEqual(value['sha256'],hashlib.sha256(changed).hexdigest())
        self.assertNotEqual(value['sha256'],manifest['root']['sha256'])

    def test_historical_receipt_requires_exact_original_fence_binding(self):
        fixture=test_recovery_restore_reconcile.RestoreReconcileTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.outcome('completed')
        value=dict(type='fixture-fence',plan_sha256=fixture.pin,attempt=fixture.attempt,
                   boot_id=fixture.boot,outcome=fixture.fenced['outcome'])
        self.assertEqual(check_completed_fence(value,fixture.plan,fixture.pin,fixture.attempt),
                         value['outcome']['result'])
        for field,replacement in (('attempt','0'*32),('boot_id','bad'),('type','complete')):
            with self.assertRaises(ValueError):
                check_completed_fence(dict(value,**{field:replacement}),fixture.plan,fixture.pin,fixture.attempt)
        altered=copy.deepcopy(value)
        altered['outcome']['root_write_authorized']=0
        with self.assertRaises(ValueError):
            check_completed_fence(altered,fixture.plan,fixture.pin,fixture.attempt)
        altered=copy.deepcopy(value)
        altered['outcome']['result']['root']['sha256']='0'*64
        with self.assertRaises(ValueError):
            check_completed_fence(altered,fixture.plan,fixture.pin,fixture.attempt)


if __name__=='__main__': unittest.main()
