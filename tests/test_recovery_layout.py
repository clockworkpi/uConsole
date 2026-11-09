import base64
import copy
import struct
import unittest

from forge_recovery_layout import root_extent, reader


class RecoveryLayoutTests(unittest.TestCase):
    def setUp(self):
        self.cid, self.disk_id = 'a'*32, '21965b0c'
        self.header = bytearray(512)
        struct.pack_into('<I', self.header, 440, int(self.disk_id,16))
        self.header[510:] = b'\x55\xaa'
        self.header[450], self.header[466] = 0x0c, 0x83
        struct.pack_into('<II', self.header, 454, 8192, 1048576)
        struct.pack_into('<II', self.header, 470, 1056768, 2000000)
        self.observed = dict(device='/dev/mmcblk0',cid=self.cid,sector_size=512,sectors=4000000,
                             bytes=4000000*512,partitions=[dict(number=1,start=8192,sectors=1048576),
                                                        dict(number=2,start=1056768,sectors=2000000)])

    def check(self):
        return root_extent(dict(self.observed,mbr=base64.b64encode(self.header).decode()),self.cid,self.disk_id)

    def test_only_root_extent_excludes_boot_table_and_trailing_sectors(self):
        result = self.check()
        self.assertEqual(result['offset_bytes'],1056768*512)
        self.assertEqual(result['length_bytes'],2000000*512)
        self.assertEqual(result['protected_prefix_bytes'],result['offset_bytes'])
        self.assertEqual(result['protected_suffix_start_bytes'],3056768*512)
        for key in ('restore_authorized','consistent_backup_qualified','whole_card_write_authorized'):
            self.assertFalse(result[key])

    def test_explicit_mmc_device_is_bound_and_cannot_inject_paths(self):
        observed = dict(self.observed,device='/dev/mmcblk1',mbr=base64.b64encode(self.header).decode())
        with self.assertRaises(ValueError):
            root_extent(observed,self.cid,self.disk_id)
        result = root_extent(observed,self.cid,self.disk_id,device='/dev/mmcblk1')
        self.assertEqual(result['device'],'/dev/mmcblk1p2')
        self.assertIn("os.open('/dev/mmcblk1'",reader('/dev/mmcblk1'))
        for device in ('/dev/mmcblk0p2','/dev/sda','/dev/mmcblk1; reboot','../mmcblk0'):
            with self.subTest(device=device),self.assertRaises(ValueError): reader(device)

    def test_wrong_card_geometry_or_kernel_inventory_rejected(self):
        original = copy.deepcopy(self.observed)
        for key,value in (('cid','b'*32),('sector_size',4096),('bytes',1),('partitions',[])):
            self.observed = dict(original,**{key:value})
            with self.subTest(key=key),self.assertRaises(ValueError): self.check()

    def test_overlap_out_of_bounds_and_extra_partition_rejected(self):
        original = bytes(self.header)
        for start,count in ((8192,2000000),(3999999,2),(1056768,0)):
            self.header = bytearray(original)
            struct.pack_into('<II',self.header,470,start,count)
            with self.subTest(start=start,count=count),self.assertRaises(ValueError): self.check()
        self.header = bytearray(original)
        self.header[482] = 0x83
        with self.assertRaises(ValueError): self.check()

    def test_gpt_extended_bad_signature_and_disk_id_rejected(self):
        for index,value in ((450,0xee),(466,0x05),(510,0),(440,0)):
            original = self.header[index]
            self.header[index] = value
            with self.subTest(index=index),self.assertRaises(ValueError): self.check()
            self.header[index] = original
