import unittest
import socket
import threading

from forge_modem_at import Modem, ModemState
from serve_modem_at import serve_channels


class SharedModemTests(unittest.TestCase):
    def test_two_socket_channels_share_changes_and_drain_on_eof(self):
        a, primary = socket.socketpair()
        b, secondary = socket.socketpair()
        failures = []
        def run():
            try:
                serve_channels(dict(primary=a,secondary=b), ModemState())
            except Exception as exc:
                failures.append(exc)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        def receive(conn, expected):
            result = b''
            while len(result) < len(expected):
                chunk = conn.recv(1024)
                self.assertTrue(chunk)
                result += chunk
            self.assertEqual(result, expected)
        try:
            primary.settimeout(2)
            secondary.settimeout(2)
            secondary.sendall(b'ATE0\rAT+CEREG=1\r')
            receive(secondary,b'ATE0\r\r\nOK\r\n\r\nOK\r\n')
            primary.sendall(b'AT+CFUN=4\r')
            primary.shutdown(socket.SHUT_WR)
            receive(primary,b'AT+CFUN=4\r\r\nOK\r\n')
            receive(secondary,b'\r\n+CEREG: 0\r\n')
            secondary.sendall(b'AT+CFUN?\r')
            receive(secondary,b'\r\n+CFUN: 4\r\nOK\r\n')
            secondary.shutdown(socket.SHUT_WR)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
        finally:
            for conn in (a,b,primary,secondary):
                conn.close()

    def setUp(self):
        self.modem = Modem()
        for channel in ('primary','secondary'):
            self.modem.feed(channel, b'ATE0\rAT+CEREG=1\r')

    def test_radio_transition_notifies_both_ports_after_command_result(self):
        self.assertEqual(self.modem.feed('primary', b'AT+CFUN=4\r'),
                         {'primary':b'\r\nOK\r\n\r\n+CEREG: 0\r\n',
                          'secondary':b'\r\n+CEREG: 0\r\n'})

    def test_batched_changes_preserve_both_notifications(self):
        result = self.modem.feed('primary', b'AT+CFUN=4\rAT+CFUN=1\r')
        self.assertEqual(result['secondary'], b'\r\n+CEREG: 0\r\n\r\n+CEREG: 1\r\n')

    def test_scenario_registration_drop_roaming_and_no_duplicates(self):
        for registration in (0,5):
            expected = ('\r\n+CEREG: %d\r\n' % registration).encode()
            self.assertEqual(self.modem.set_scenario(registration=registration),
                             dict(primary=expected, secondary=expected))
            self.assertEqual(self.modem.set_scenario(registration=registration), {})

    def test_invalid_scenario_is_atomic_and_preserves_partial_command(self):
        self.modem.feed('primary', b'AT+CF')
        with self.assertRaises(ValueError):
            self.modem.set_scenario(sim='absent', rssi=300)
        self.assertEqual(self.modem.state.sim, 'ready')
        self.assertEqual(self.modem.feed('primary', b'UN?\r'), {'primary':b'\r\n+CFUN: 1\r\nOK\r\n'})

    def test_pin_unlock_notifies_subscribers_on_other_port(self):
        modem = Modem(ModemState(sim='pin'))
        modem.feed('secondary', b'AT+CEREG=1\r')
        result = modem.feed('primary', b'AT+CPIN="1234"\r')
        self.assertEqual(result['secondary'], b'\r\n+CEREG: 1\r\n')

    def test_unsubscribed_port_and_signal_only_change_are_silent(self):
        self.modem.feed('secondary', b'AT+CEREG=0\r')
        self.assertEqual(self.modem.set_scenario(rssi=2), {})
        self.assertEqual(self.modem.set_scenario(sim='absent'), {'primary':b'\r\n+CEREG: 0\r\n'})

    def test_non_at_port_cannot_change_state(self):
        with self.assertRaises(ValueError):
            self.modem.feed('gnss', b'AT+CFUN=4\r')
        self.assertEqual(self.modem.state.radio, 1)
