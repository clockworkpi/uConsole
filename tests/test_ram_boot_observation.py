import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from forge_ram_boot_observation import checked,capture
import test_ram_identity


class RamBootObservationTests(unittest.TestCase):
    def setUp(self):
        fixture=test_ram_identity.RamIdentityTests()
        fixture.setUp()
        self.record=dict(identity=fixture.record,bootloader=dict(tryboot=0,partition=1))
        self.arguments=(fixture.nonce,fixture.record['kernel'],fixture.serial)
        self.boot_id=fixture.record['boot_id']

    def test_normal_selection_and_tryboot_are_distinct_ram_observations(self):
        for flag,selection in ((0,'normal'),(1,'tryboot')):
            self.record['bootloader']['tryboot']=flag
            result=checked(self.record,*self.arguments,boot_id=self.boot_id,expected_tryboot=flag)
            self.assertEqual(result['selection'],selection)
            self.assertFalse(result['normal_boot_release_authorized'])
            self.assertFalse(result['root_write_authorized'])
            with self.assertRaises(ValueError):
                checked(self.record,*self.arguments,boot_id=self.boot_id,expected_tryboot=1-flag)

    def test_emulation_disk_mount_wrong_boot_and_loose_cell_types_rejected(self):
        bad=copy.deepcopy(self.record)
        bad['identity']['cmdline']+=' uconsole.emulator=1'
        cases=[bad]
        bad=copy.deepcopy(self.record)
        bad['identity']['mountinfo']+='3 1 179:2 / /mnt rw - ext4 /dev/mmcblk0p2 rw\n'
        cases.append(bad)
        for value in (True,'0',2):
            bad=copy.deepcopy(self.record)
            bad['bootloader']['tryboot']=value
            cases.append(bad)
        for record in cases:
            with self.assertRaises(ValueError):
                checked(record,*self.arguments,boot_id=self.boot_id,expected_tryboot=0)
        with self.assertRaises(ValueError):
            checked(self.record,*self.arguments,boot_id='99999999-1234-1234-1234-123456789abc',expected_tryboot=0)

    def test_capture_rejects_emulator_or_ambiguous_request_before_ssh(self):
        for mode,flag,partition in (('emulated',0,1),('physical',True,1),('physical',0,2)):
            probe=SimpleNamespace(mode=mode,_observe=Mock())
            with self.assertRaises(ValueError):
                capture(probe,boot_id=self.boot_id,expected_tryboot=flag,expected_partition=partition)
            probe._observe.assert_not_called()


if __name__=='__main__': unittest.main()
