import base64
import copy
import unittest
from unittest.mock import Mock

from forge_recovery_restore_identity import checker, observe_local
import test_ram_identity
import test_recovery_layout


class RestoreIdentityTests(unittest.TestCase):
    def setUp(self):
        ram = test_ram_identity.RamIdentityTests()
        ram.setUp()
        layout = test_recovery_layout.RecoveryLayoutTests()
        layout.setUp()
        owner = 'd' * 64
        ram.record['cmdline'] += ' uconsole.recovery_lease=1 uconsole.recovery_owner=' + owner
        self.ram = dict(identity=ram.record, bootloader=dict(tryboot=0, partition=1))
        self.layout = dict(layout.observed, mbr=base64.b64encode(layout.header).decode())
        self.binding = dict(nonce=ram.nonce, kernel=ram.record['kernel'], serial=ram.serial,
                            mode='physical', boot_id=ram.record['boot_id'], cid=layout.cid,
                            disk_id=layout.disk_id, device='/dev/mmcblk0', extent=layout.check(),
                            lease_owner=owner)

    def test_every_call_brackets_layout_with_fresh_physical_ram_observations(self):
        observe = Mock(side_effect=[self.ram, self.layout, self.ram] * 2)
        check = checker(self.binding, observe=observe)
        for _ in range(2):
            self.assertEqual(check(), self.binding['extent'])
        self.assertEqual(observe.call_count, 6)
        sources = [call.args[0] for call in observe.call_args_list]
        self.assertEqual(sources[0], sources[2])
        self.assertNotEqual(sources[0], sources[1])
        self.assertIn('/proc/device-tree/chosen/bootloader', sources[0])

    def test_bad_ram_identity_prevents_even_layout_observation(self):
        cases = []
        for field, value in (('boot_id', 'aaaaaaaa-1234-1234-1234-123456789abc'),
                             ('serial', '0'*16), ('uid', 1000),
                             ('mountinfo', self.ram['identity']['mountinfo'] +
                              '3 1 179:2 / /mnt rw - ext4 /dev/mmcblk0p2 rw\n')):
            bad = copy.deepcopy(self.ram)
            bad['identity'][field] = value
            cases.append(bad)
        for suffix in (' uconsole.emulator=1', ' uconsole.recovery_lease=1',
                       ' uconsole.recovery_owner=' + 'e'*64):
            bad = copy.deepcopy(self.ram)
            bad['identity']['cmdline'] += suffix
            cases.append(bad)
        bad = copy.deepcopy(self.ram)
        bad['bootloader']['tryboot'] = 1
        cases.append(bad)
        for record in cases:
            observe = Mock(return_value=record)
            with self.subTest(record=record), self.assertRaises(ValueError):
                checker(self.binding, observe=observe)()
            self.assertEqual(observe.call_count, 1)

    def test_boot_change_after_layout_is_rejected(self):
        bad = copy.deepcopy(self.ram)
        bad['identity']['boot_id'] = 'aaaaaaaa-1234-1234-1234-123456789abc'
        observe = Mock(side_effect=[self.ram, self.layout, bad])
        with self.assertRaisesRegex(ValueError, 'UUID differs'):
            checker(self.binding, observe=observe)()

    def test_other_card_or_repinned_geometry_is_rejected(self):
        bad = dict(self.layout, cid='b'*32)
        for layout in (bad, dict(self.layout, sectors=1)):
            observe = Mock(side_effect=[self.ram, layout])
            with self.assertRaises(ValueError):
                checker(self.binding, observe=observe)()
        self.binding['extent']['mbr_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'layout changed'):
            checker(self.binding, observe=Mock(side_effect=[self.ram, self.layout]))()

    def test_binding_is_frozen_and_results_do_not_mutate_future_checks(self):
        expected = copy.deepcopy(self.binding['extent'])
        check = checker(self.binding, observe=Mock(side_effect=[self.ram, self.layout, self.ram]*2))
        self.binding['extent']['mbr_sha256'] = '0'*64
        self.binding['lease_owner'] = 'e'*64
        result = check()
        result['mbr_sha256'] = 'f'*64
        self.assertEqual(check(), expected)

    def test_emulated_binding_rejected_before_observation(self):
        self.binding['mode'] = 'emulated'
        observe = Mock()
        with self.assertRaises(ValueError):
            checker(self.binding, observe=observe)
        observe.assert_not_called()

    def test_local_reader_captures_json_and_propagates_failure(self):
        self.assertEqual(observe_local('print(\'{"value": 1}\')'), {'value': 1})
        with self.assertRaises(OSError):
            observe_local("raise OSError('reader failed')")


if __name__ == '__main__':
    unittest.main()
