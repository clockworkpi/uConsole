import unittest
from validate_recovery_guest_ssh import check_output


class RecoveryGuestSshTests(unittest.TestCase):
    def test_requires_ram_root_and_exact_markers(self):
        text = ('0\nFORGE_MOUNTS\nrootfs / rootfs rw 0 0\nproc /proc proc rw 0 0\n'
                'FORGE_CMDLINE\nuconsole.recovery=1 uconsole.emulator=1\n')
        check_output(text)
        for bad in (text.replace('rootfs / rootfs', '/dev/mmcblk0p2 / ext4'),
                    text.replace('emulator=1', 'emulator=10'),
                    text.replace('0\nFORGE', '1000\nFORGE', 1)):
            with self.assertRaises(ValueError):
                check_output(bad)
