import unittest

from validate_modem_composite_guest import check_interfaces


class CompositeGuestEvidenceTests(unittest.TestCase):
    def fixture(self):
        return '\n'.join('ttyUSB%d /sys/devices/usb1/1-1/1-1.2/1-1.2:2.%d/ttyUSB%d option1' % (i,i,i)
                         for i in range(5)) + '\n'

    def test_all_five_roles_on_one_device(self):
        check_interfaces(self.fixture())

    def test_configuration_must_match_declared_descriptor(self):
        legacy = self.fixture().replace(':2.', ':1.')
        check_interfaces(legacy, configuration=1)
        with self.assertRaises(ValueError):
            check_interfaces(legacy)

    def test_missing_port_wrong_driver_wrong_role_and_split_device_fail(self):
        baseline = self.fixture()
        for changed in (baseline.rsplit('\n',2)[0], baseline.replace('option1','ftdi_sio'),
                        baseline.replace(':2.2/',':2.3/'),
                        baseline.replace(':2.2/ttyUSB2',':2.2/ttyUSB3'),
                        baseline.replace('ttyUSB2 /sys/devices/usb1','ttyUSB2 /sys/devices/usb2')):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    check_interfaces(changed)
