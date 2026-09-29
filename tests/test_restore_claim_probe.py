import unittest
from pathlib import Path
import tempfile
from forge_restore_claim_probe import script
from validate_recovery_watchdog import run, storage_fixture, verify_storage


class RestoreClaimProbeTests(unittest.TestCase):
    def test_unchanged_final_card_does_not_mean_probe_was_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.img'
            fixture=storage_fixture(path)
            result=verify_storage(path,fixture,root_write_roundtrip=True)
            self.assertTrue(result['unchanged'])
            self.assertTrue(result['root_unchanged'])
            self.assertFalse(result['probe_read_only'])

    def test_physical_device_and_large_or_ambiguous_fixture_rejected(self):
        binding=dict(mode='emulated',device='/dev/mmcblk1',extent=dict(disk_bytes=64*1024*1024))
        for changed in (dict(mode='physical'),dict(device='/dev/mmcblk0'),
                        dict(extent=dict(disk_bytes=True)),dict(extent=dict(disk_bytes=256*1024*1024))):
            with self.assertRaises(ValueError): script(dict(binding,**changed),{})

    def test_validator_requires_explicit_small_storage_and_lease(self):
        for options in ({},{'storage':True},{'require_lease':True}):
            with self.assertRaisesRegex(ValueError,'Root write claim'):
                run(None,None,None,None,None,None,None,root_write_claim=True,**options)
        with self.assertRaisesRegex(ValueError,'Root write claim'):
            run(None,None,None,None,None,None,None,root_write_claim=1)


if __name__=='__main__': unittest.main()
