import unittest

from validate_modem_network_guest import check_interface


class NetworkGuestEvidenceTests(unittest.TestCase):
    def test_expected_rndis_interface(self):
        check_interface('rndis_host\n/sys/devices/usb1/1-1/1-1.2:2.5\n1e0e:9001\n')

    def test_other_nic_or_interface_rejected(self):
        fixture = 'rndis_host\n/sys/devices/usb1/1-1/1-1.2:2.5\n1e0e:9001\n'
        for changed in (fixture.replace('rndis_host', 'cdc_ether'),
                        fixture.replace(':2.5', ':1.0'),
                        fixture.replace('1e0e:9001', '0525:a4a2'),
                        fixture + 'extra\n'):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                check_interface(changed)
