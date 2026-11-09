import copy
import unittest

from forge_ram_identity import verify


class RamIdentityTests(unittest.TestCase):
    def setUp(self):
        self.nonce = 'a'*32
        self.serial = '100000007b961d25'
        self.record = dict(boot_id='12345678-1234-1234-1234-123456789abc', uid=0,
                           kernel='6.12.62-v8+', serial=self.serial,
                           cmdline='root=/dev/ram0 rdinit=/init uconsole.recovery=1 uconsole.forge_trial='+self.nonce,
                           mountinfo='1 0 0:1 / / rw - rootfs rootfs rw\n2 1 0:2 / /proc rw - proc proc rw\n')

    def check(self, record, **kwargs):
        return verify(record, self.nonce, '6.12.62-v8+', self.serial, **kwargs)

    def test_ram_session_identity_does_not_authorize_mutation_or_fallback(self):
        result = self.check(self.record)
        self.assertEqual(result['status'], 'verified-ram-session')
        self.assertFalse(result['mutation_authorized'])
        self.assertFalse(result['fallback_qualified'])

    def test_rejects_other_target_boot_arguments_or_privilege(self):
        for field, value in (('serial', '0'*16), ('kernel', 'other'), ('uid', True), ('uid', 1000),
                             ('boot_id', 'bad'), ('cmdline', self.record['cmdline']+' root=/dev/mmcblk0p2'),
                             ('cmdline', self.record['cmdline']+' uconsole.emulator=1')):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.check(dict(self.record, **{field: value}))

    def test_rejects_block_mount_overlay_and_duplicate_root(self):
        for mount in ('3 1 179:2 / /mnt rw - ext4 /dev/mmcblk0p2 rw\n',
                      '3 1 0:3 / /mnt rw - overlay overlay rw\n',
                      '3 1 0:3 / / rw - tmpfs tmpfs rw\n', 'malformed\n'):
            with self.subTest(mount=mount), self.assertRaises(ValueError):
                self.check(dict(self.record, mountinfo=self.record['mountinfo']+mount))

    def test_emulator_requires_explicit_mode_and_marker(self):
        record = copy.deepcopy(self.record)
        with self.assertRaises(ValueError):
            self.check(record, mode='emulated')
        record['cmdline'] += ' uconsole.emulator=1'
        self.assertEqual(self.check(record, mode='emulated')['mode'], 'emulated')
        with self.assertRaises(ValueError):
            verify(self.record, self.nonce, '6.12.62-v8+', None)
