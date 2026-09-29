import copy
import hashlib
import json
from pathlib import Path
import unittest

from forge_recovery_bootplan import digest
from forge_recovery_restore_plan import compile_plan,prepare,load
from forge_target_journal import validate_plan
import test_recovery_bootcommit
import test_recovery_restore_source
import test_ram_identity


class RestorePlanTests(unittest.TestCase):
    def setUp(self):
        source=test_recovery_restore_source.RestoreSourceTests()
        source.setUp()
        self.addCleanup(source.doCleanups)
        self.source=source
        old=test_recovery_bootcommit.BootCommitTests()
        old.setUp()
        self.hold=old.plan
        binding=self.hold['binding']
        binding['lease_owner']='d'*64
        start,size,total=source.expected['offset'],source.expected['bytes'],len(source.data)
        extent=dict(kind='root-partition-extent',cid=binding['cid'],disk_id=binding['disk_id'],
            mbr_sha256='1'*64,device=binding['device']+'p2',offset_bytes=start,length_bytes=size,
            protected_prefix_bytes=start,protected_suffix_start_bytes=start+size,disk_bytes=total,
            restore_authorized=False,consistent_backup_qualified=False,whole_card_write_authorized=False)
        binding['extent']=extent
        self.current=copy.deepcopy(binding)
        self.current['boot_id']='abcdef01-1234-1234-1234-123456789abc'
        self.prefix=dict(offset=0,bytes=start,sha256='e'*64)
        self.suffix=dict(offset=start+size,bytes=512,sha256=hashlib.sha256(b's'*512).hexdigest())
        self.hold.update(root_guard=copy.deepcopy(source.expected),prefix_guard=self.prefix,suffix_guard=self.suffix)
        self.hold_pin=digest(self.hold)
        self.backup=dict(source.plan,**{key:copy.deepcopy(binding[key]) for key in
            ('mode','kernel','serial','cid','disk_id','device','extent')})
        source.save(source.source/'plan.json',self.backup)
        prepared,self.manifest=source.prepared()
        self.source_pin=prepared['manifest_sha256']
        self.hashes=dict(status='verified-offline-storage-digests',mode='physical',is_backup=False,
            root_write_authorized=False,normal_boot_release_authorized=False,target_written=False,
            digests=dict(boot_id=self.current['boot_id'],card=dict(bytes=total,sha256='f'*64),
                prefix=copy.deepcopy(self.prefix),suffix=copy.deepcopy(self.suffix),
                root=dict(source.expected,sha256='b'*64)))
        self.inspection=dict(status='after',files=[dict(path=path,state='after' if path.endswith('/config.txt') else 'unchanged')
            for path in self.hold['guarded_paths']],image='matched',stage='absent',plan_sha256=self.hold_pin,
            boot_id=self.current['boot_id'],prefix=copy.deepcopy(self.prefix),read_only=True,
            boot_unmounted=True,root_written=False,deployment_authorized=False)
        ram=test_ram_identity.RamIdentityTests()
        ram.setUp()
        record=ram.record
        record['boot_id']=self.current['boot_id']
        record['cmdline']=record['cmdline'].replace(ram.nonce,binding['nonce'])
        record['cmdline']+=' uconsole.recovery_lease=1 uconsole.recovery_owner='+binding['lease_owner']
        self.boot=dict(identity=record,bootloader=dict(tryboot=0,partition=1))

    def arguments(self):
        return (self.hold,self.hold_pin,self.manifest,self.source_pin,self.backup,
                self.current,self.hashes,self.inspection,self.boot)

    def compile(self): return compile_plan(*self.arguments())

    def test_saved_source_not_current_root_is_the_restoration_goal(self):
        plan=self.compile()
        self.assertEqual(plan['root_after'],self.source.expected)
        self.assertEqual(plan['root_before'],self.hashes['digests']['root'])
        self.assertNotEqual(plan['root_before'],plan['root_after'])
        self.assertEqual(plan['writable_devices'],['/dev/mmcblk0p2'])
        self.assertEqual(plan['held_files'],self.hold['after'])
        for key in ('root_write_authorized','boot_write_authorized','whole_card_write_authorized',
                    'normal_boot_release_authorized','filesystem_consistency_qualified'):
            self.assertFalse(plan[key])
        self.assertIn('fenced-prior-root-writers',plan['required_gates'])
        self.assertIn('separate-native-boot-compatibility-and-release-approval',plan['required_gates'])
        with self.assertRaises(ValueError): validate_plan(plan)
        self.manifest['root']['sha256']='0'*64
        self.assertEqual(plan['root_after'],self.source.expected)

    def test_physical_normal_selection_in_a_new_boot_is_required(self):
        initial=copy.deepcopy(self.boot)
        for flag in (1,True,'0'):
            self.boot=copy.deepcopy(initial)
            self.boot['bootloader']['tryboot']=flag
            with self.assertRaises(ValueError): self.compile()
        self.boot=initial
        self.current['boot_id']=self.hold['binding']['boot_id']
        with self.assertRaises(ValueError): self.compile()

    def test_emulator_mounted_root_wrong_owner_or_serial_is_rejected(self):
        original=copy.deepcopy(self.boot)
        for field,value in (('serial','0'*16),('cmdline',original['identity']['cmdline']+' uconsole.emulator=1'),
                            ('cmdline',original['identity']['cmdline'].replace('d'*64,'c'*64)),
                            ('mountinfo',original['identity']['mountinfo']+'3 1 179:2 / /mnt rw - ext4 /dev/mmcblk0p2 rw\n')):
            self.boot=copy.deepcopy(original)
            self.boot['identity'][field]=value
            with self.assertRaises(ValueError): self.compile()

    def test_binding_change_or_missing_lease_rejected(self):
        original=copy.deepcopy(self.current)
        for field,value in (('cid','c'*32),('device','/dev/mmcblk1'),('lease_owner','e'*64),('mode','emulated')):
            self.current=copy.deepcopy(original)
            self.current[field]=value
            with self.assertRaises(ValueError): self.compile()
        self.current=original
        del self.current['lease_owner']
        with self.assertRaises(ValueError): self.compile()

    def test_other_card_backup_rejected_even_when_source_is_repinned(self):
        self.backup['cid']='f'*32
        self.manifest['backup_plan_sha256']=digest(self.backup)
        self.source_pin=digest(self.manifest)
        with self.assertRaisesRegex(ValueError,'different target or layout'): self.compile()

    def test_source_plan_tamper_or_nonmatching_root_extent_rejected(self):
        self.backup['extra']='tampered'
        with self.assertRaisesRegex(ValueError,'frozen manifest'): self.compile()
        del self.backup['extra']
        self.manifest['root']['offset']+=512
        self.source_pin=digest(self.manifest)
        with self.assertRaisesRegex(ValueError,'exact original root'): self.compile()

    def test_hold_inspection_conflict_or_changed_prefix_rejected(self):
        self.inspection['prefix']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'protected boot bytes'): self.compile()
        self.inspection['prefix']=copy.deepcopy(self.prefix)
        self.inspection.update(status='conflict',image='conflict')
        with self.assertRaisesRegex(ValueError,'dependencies'): self.compile()

    def test_failed_hash_receipt_or_different_boot_is_not_adopted(self):
        self.hashes['status']='incomplete'
        with self.assertRaises(ValueError): self.compile()
        self.hashes['status']='verified-offline-storage-digests'
        self.hashes['digests']['boot_id']=self.hold['binding']['boot_id']
        with self.assertRaises(ValueError): self.compile()

    def test_private_journal_recompiles_evidence_and_rejects_tampering(self):
        directory=self.source.root/'restore-plan'
        prepared=prepare(directory,*self.arguments())
        self.assertEqual(prepared['status'],'prepared-not-dispatched')
        self.assertEqual(directory.stat().st_mode&0o777,0o700)
        self.assertEqual((directory/'plan.json').stat().st_mode&0o777,0o600)
        self.assertEqual(load(directory,prepared['plan_sha256']),self.compile())
        with self.assertRaises(FileExistsError): prepare(directory,*self.arguments())
        inputs=json.loads((directory/'inputs.json').read_text())
        inputs['hash_observation']['digests']['root']['sha256']='0'*64
        self.source.save(directory/'inputs.json',inputs)
        with self.assertRaisesRegex(ValueError,'retained evidence'): load(directory,prepared['plan_sha256'])


if __name__=='__main__': unittest.main()
