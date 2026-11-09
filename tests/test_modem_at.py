import unittest
import socket
import threading

from forge_modem_at import ATPort, ModemState
from serve_modem_at import serve


class ModemATTests(unittest.TestCase):
    def test_socket_transport_and_clean_disconnect(self):
        host, guest = socket.socketpair()
        worker = threading.Thread(target=serve, args=(host, ModemState()), daemon=True)
        worker.start()
        try:
            guest.settimeout(2)
            guest.sendall(b'AT\r')
            output = b''
            while not output.endswith(b'OK\r\n'):
                output += guest.recv(1024)
            self.assertEqual(output, b'AT\r\r\nOK\r\n')
            guest.shutdown(socket.SHUT_WR)
            worker.join(2)
            self.assertFalse(worker.is_alive())
        finally:
            guest.close()
            host.close()

    def test_socket_adapter_emits_registration_notifications(self):
        host, guest = socket.socketpair()
        worker = threading.Thread(target=serve, args=(host, ModemState()), daemon=True)
        worker.start()
        try:
            guest.settimeout(2)
            expected = (b'ATE0\r\r\nOK\r\n\r\nOK\r\n'
                        b'\r\nOK\r\n\r\n+CEREG: 0\r\n')
            guest.sendall(b'ATE0\rAT+CEREG=1\rAT+CFUN=4\r')
            output = b''
            while len(output) < len(expected):
                chunk = guest.recv(1024)
                self.assertTrue(chunk)
                output += chunk
            self.assertEqual(output, expected)
            guest.shutdown(socket.SHUT_WR)
            worker.join(2)
            self.assertFalse(worker.is_alive())
        finally:
            guest.close()
            host.close()

    def test_invalid_scenarios_rejected(self):
        for options in (dict(sim='unknown'),dict(rssi=32),dict(radio=True),dict(pin='abcd'),
                        dict(sim='pin',pin_attempts=0)):
            with self.assertRaises(ValueError):
                ModemState(**options)

    def test_fragmentation_and_crlf_do_not_duplicate_responses(self):
        port = ATPort()
        self.assertEqual(port.feed(b'A'), b'')
        self.assertEqual(port.feed(b'T\r\n'), b'AT\r\r\nOK\r\n')
        self.assertEqual(port.feed(b'ATE0\rAT\r'), b'ATE0\r\r\nOK\r\n\r\nOK\r\n')

    def test_locked_sim_unlock_and_registration(self):
        port = ATPort(ModemState(sim='pin'))
        self.assertEqual(port.execute('AT+CPIN?'), ['+CPIN: SIM PIN','OK'])
        self.assertEqual(port.execute('AT+CEREG?'), ['+CEREG: 0,0','OK'])
        self.assertEqual(port.execute('AT+CPIN="1234"'), ['OK'])
        self.assertEqual(port.execute('AT+CEREG?'), ['+CEREG: 0,1','OK'])

    def test_wrong_pins_lock_synthetic_sim_and_do_not_reset_on_new_port(self):
        state = ModemState(sim='pin')
        port = ATPort(state, errors=1)
        for _ in range(3):
            self.assertEqual(port.execute('AT+CPIN="0000"'), ['+CME ERROR: 16'])
        other = ATPort(state, errors=2)
        self.assertEqual(other.execute('AT+CPIN?'), ['+CPIN: SIM PUK','OK'])
        self.assertEqual(other.execute('AT+CPIN="1234"'), ['+CME ERROR: SIM PUK required'])

    def test_absent_sim_error_modes(self):
        port = ATPort(ModemState(sim='absent'))
        for mode, expected in ((0,'ERROR'),(1,'+CME ERROR: 10'),(2,'+CME ERROR: SIM not inserted')):
            port.execute('AT+CMEE='+str(mode))
            self.assertEqual(port.execute('AT+CPIN?'), [expected])

    def test_ports_share_radio_but_not_echo_or_subscription(self):
        state = ModemState(registration=5)
        primary, secondary = ATPort(state), ATPort(state)
        primary.execute('ATE0')
        primary.execute('AT+CEREG=1')
        self.assertTrue(secondary.echo)
        self.assertEqual(secondary.notifications(), b'')
        self.assertEqual(primary.notifications(), b'\r\n+CEREG: 5\r\n')
        secondary.execute('AT+CFUN=4')
        self.assertEqual(primary.execute('AT+CSQ'), ['+CSQ: 99,99','OK'])
        self.assertEqual(primary.notifications(), b'\r\n+CEREG: 0\r\n')

    def test_oversize_discards_whole_line_and_recovers(self):
        port = ATPort(echo=False)
        self.assertEqual(port.feed(b'x'*100000), b'')
        self.assertEqual(len(port.pending), 0)
        self.assertEqual(port.feed(b'AT+CFUN=4\rAT\r'), b'\r\nERROR\r\n\r\nOK\r\n')
        self.assertEqual(port.state.radio, 1)

    def test_invalid_bytes_and_unsupported_commands_fail_without_effect(self):
        port = ATPort(echo=False)
        for command in (b'\xff',b'AT+CFUN=9',b'AT+BOOTLDR',b'AT;rm -rf /',b'AT+CGACT=2,1'):
            self.assertEqual(port.feed(command+b'\r'), b'\r\nERROR\r\n')
        self.assertEqual(port.state.radio, 1)

    def test_embedded_newline_does_not_join_commands(self):
        port = ATPort(echo=False)
        self.assertEqual(port.feed(b'AT+CFUN=\n4\r'), b'\r\nERROR\r\n')
        self.assertEqual(port.state.radio, 1)
        self.assertEqual(port.feed(b'AT\r\n'), b'\r\nOK\r\n')
