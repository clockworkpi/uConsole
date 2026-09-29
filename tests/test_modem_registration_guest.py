from types import SimpleNamespace
import unittest

from validate_modem_registration_guest import check_transition


class RegistrationEvidenceTests(unittest.TestCase):
    def result(self, status, stdout):
        return SimpleNamespace(returncode=status, stdout=stdout)

    def test_denied_and_roaming(self):
        check_transition(self.result(0, b'\r\n+CEREG: 0,3\r\nOK\r\n'),
                         self.result(1, b'100% packet loss'), 3, False)
        check_transition(self.result(0, b'\r\n+CEREG: 0,5\r\nOK\r\n'),
                         self.result(0, b'0% packet loss'), 5, True)

    def test_transport_failure_is_not_loss_of_service_evidence(self):
        at = self.result(0, b'\r\n+CEREG: 0,3\r\nOK\r\n')
        for ping in (self.result(255, b''), self.result(1, b''),
                     self.result(0, b'100% packet loss')):
            with self.assertRaises(RuntimeError):
                check_transition(at, ping, 3, False)

    def test_stale_or_failed_at_query_rejected(self):
        for at in (self.result(0, b'\r\n+CEREG: 0,1\r\nOK\r\n'),
                   self.result(1, b'\r\n+CEREG: 0,3\r\nOK\r\n')):
            with self.assertRaises(RuntimeError):
                check_transition(at, self.result(1, b'100% packet loss'), 3, False)
