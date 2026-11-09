import unittest
from validate_recovery_qemu import check_log, check_deadline


class RecoveryQemuTests(unittest.TestCase):
    def test_deadline_rejects_early_late_failed_and_external_exits(self):
        text = ('[ 3.0] Run /init as init process\nAccepted publickey for root\n'
                '[ 303.2] reboot: Restarting system\n')
        self.assertAlmostEqual(check_deadline(text, 0)['init_to_reboot_guest_seconds'], 300.2)
        for bad in (text.replace('303.2', '30.2'), text.replace('303.2', '330.2'),
                    text + 'Kernel panic', text + 'Forge recovery failed;',
                    text.replace('Accepted publickey for root', ''), text + text):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                check_deadline(bad, 0)
        with self.assertRaises(RuntimeError):
            check_deadline(text, 124)

    def test_only_expected_boundary_and_order_pass(self):
        text = ('Run /init as init process\nregistered new interface driver brcmfmac\n'
                'ip: SIOCGIFFLAGS: No such device\n'
                'Forge recovery failed; returning to normal boot\nreboot: Restarting system\n')
        check_log(text, 0)
        for invalid in (text.replace('ip: SIOCGIFFLAGS: No such device', ''),
                        'mount: mounting proc on /proc failed\n' + text,
                        text + 'Kernel panic', '\n'.join(reversed(text.splitlines()))):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                check_log(invalid, 0)
        with self.assertRaises(RuntimeError):
            check_log(text, 1)
