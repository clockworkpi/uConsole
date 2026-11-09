#!/usr/bin/env python3
"""FreeBSD ELF-to-Image boot ABI layout and refusal controls."""
import struct
import unittest
from prepare_pi5_boot import kernel_image
BASE=0xffff000000000000

def fixture():
    b=bytearray(0x108);b[:16]=b'\x7fELF\x02\x01\x01\x09'+bytes(8)
    struct.pack_into('<HHIQQQIHHHHHH',b,16,2,183,1,BASE+0x800,64,0,0,64,56,2,0,0,0)
    struct.pack_into('<IIQQQQQQ',b,64,1,4,0,BASE,BASE,64,64,0x10000)
    struct.pack_into('<IIQQQQQQ',b,120,1,5,0x100,BASE+0x800,BASE+0x800,8,0x1000,0x10000)
    b[0x100:]=bytes.fromhex('1f2003d5c0035fd6') # independent AArch64 NOP; RET
    return b

class Conversion(unittest.TestCase):
    def test_image_header_and_exact_code(self):
        image=kernel_image(fixture())
        self.assertEqual(struct.unpack_from('<I',image)[0],0x14000200)
        self.assertEqual(struct.unpack_from('<Q',image,8)[0],0x200000)
        self.assertEqual(image[0x38:0x3c],b'ARM\x64')
        self.assertEqual(image[0x800:0x808],bytes.fromhex('1f2003d5c0035fd6'))
        self.assertEqual(image[0x808:0x1800],bytes(0xff8))
    def test_short_header(self):
        with self.assertRaises(ValueError):kernel_image(b'\x7fELF')
    def test_non_arm_kernel(self):
        b=fixture();struct.pack_into('<H',b,18,62)
        with self.assertRaises(ValueError):kernel_image(b)
    def test_program_table_bounds(self):
        b=fixture();struct.pack_into('<H',b,56,255)
        with self.assertRaises(ValueError):kernel_image(b)
    def test_load_segment_outside_file(self):
        b=fixture();struct.pack_into('<Q',b,120+32,0x1000)
        with self.assertRaises(ValueError):kernel_image(b)
    def test_overlapping_segments(self):
        b=fixture();struct.pack_into('<QQ',b,120+16,BASE+32,BASE+32)
        with self.assertRaises(ValueError):kernel_image(b)
    def test_wrong_virtual_base(self):
        b=fixture();struct.pack_into('<QQ',b,64+16,0,0)
        with self.assertRaises(ValueError):kernel_image(b)
    def test_entry_in_header(self):
        b=fixture();struct.pack_into('<I',b,64+4,5)
        with self.assertRaises(ValueError):kernel_image(b)

if __name__=='__main__':unittest.main()
