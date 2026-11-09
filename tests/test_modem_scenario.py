import json
import unittest
import socket
import threading

from forge_modem_at import Modem
from forge_modem_scenario import ScenarioChannel
from serve_modem_at import serve_channels


class ScenarioChannelTests(unittest.TestCase):
    def test_real_socket_scenario_and_at_notifications_share_state(self):
        at, guest = socket.socketpair()
        control, owner = socket.socketpair()
        links = []
        errors = []
        def run():
            try:
                serve_channels({'primary': at}, Modem().state,
                               links.append, scenario_connection=control)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        def receive(conn, count):
            output = b''
            while len(output) < count:
                piece = conn.recv(count-len(output))
                self.assertTrue(piece)
                output += piece
            return output
        try:
            guest.settimeout(2)
            owner.settimeout(2)
            guest.sendall(b'ATE0\rAT+CEREG=1\r')
            expected = b'ATE0\r\r\nOK\r\n\r\nOK\r\n'
            self.assertEqual(receive(guest, len(expected)), expected)
            owner.sendall(self.request(1, registration=3))
            response = b''
            while not response.endswith(b'\n'):
                response += receive(owner, 1)
            self.assertEqual(json.loads(response)['observed']['registration'], 3)
            self.assertEqual(links, [True, False])
            self.assertEqual(receive(guest, 13), b'\r\n+CEREG: 3\r\n')
            guest.sendall(b'AT+CEREG?\r')
            expected = b'\r\n+CEREG: 1,3\r\nOK\r\n'
            self.assertEqual(receive(guest, len(expected)), expected)
            guest.shutdown(socket.SHUT_WR)
            owner.shutdown(socket.SHUT_WR)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertIsNone(at.gettimeout())
            self.assertIsNone(control.gettimeout())
        finally:
            for conn in (at, guest, control, owner):
                conn.close()

    def test_failed_link_update_produces_no_scenario_ack(self):
        def failure(up):
            if not up:
                raise TimeoutError('No QMP acknowledgement')
        channel = ScenarioChannel(Modem(link_changed=failure))
        with self.assertRaises(TimeoutError):
            channel.feed(self.request(1, registration=3))
        self.assertTrue(channel.modem.link_failed)
        with self.assertRaises(RuntimeError):
            channel.feed(self.request(2, registration=1))

    def setUp(self):
        self.links = []
        self.modem = Modem(link_changed=self.links.append)
        self.modem.feed('primary', b'ATE0\rAT+CEREG=1\r')
        self.channel = ScenarioChannel(self.modem)

    def request(self, sequence, **changes):
        return (json.dumps(dict(sequence=sequence, changes=changes))+'\n').encode()

    def test_fragmented_loss_and_roaming_update_link_before_ack(self):
        request = self.request(1, registration=2)
        self.assertEqual(self.channel.feed(request[:-1]), {})
        result = self.channel.feed(request[-1:])
        self.assertEqual(self.links, [True, False])
        self.assertEqual(result['primary'], b'\r\n+CEREG: 2\r\n')
        self.assertFalse(json.loads(result['scenario'])['observed']['link_up'])
        result = self.channel.feed(self.request(2, registration=5))
        self.assertEqual(self.links, [True, False, True])
        self.assertEqual(result['primary'], b'\r\n+CEREG: 5\r\n')

    def test_replayed_sequence_does_not_mutate(self):
        self.channel.feed(self.request(1, registration=2))
        with self.assertRaises(ValueError):
            self.channel.feed(self.request(1, registration=1))
        self.assertEqual(self.modem.state.registration, 2)

    def test_duplicate_fields_and_oversize_fail_closed(self):
        with self.assertRaises(ValueError):
            self.channel.feed(b'{"sequence":1,"sequence":2,"changes":{"radio":4}}\n')
        self.assertEqual(self.links, [True])
        with self.assertRaises(BufferError):
            self.channel.feed(b'x'*4097)
        self.assertLessEqual(len(self.channel.pending), 4096)

    def test_invalid_changes_do_not_partially_mutate(self):
        with self.assertRaises(ValueError):
            self.channel.feed(self.request(1, radio=4, registration=99))
        self.assertEqual(self.modem.state.radio, 1)
        self.assertEqual(self.links, [True])

    def test_no_pin_in_response(self):
        result = self.channel.feed(self.request(1, sim='pin', pin='9876'))
        self.assertNotIn(b'9876', result['scenario'])
        self.assertNotIn('pin', json.loads(result['scenario'])['observed'])

    def test_partial_request_at_eof_is_not_success(self):
        self.channel.feed(b'{"sequence":1')
        with self.assertRaises(ValueError):
            self.channel.finish()
        self.assertEqual(self.links, [True])

    def test_query_has_no_link_updates_or_notifications(self):
        self.modem.feed('primary', b'AT+CF')
        result = self.channel.feed(b'{"sequence":1,"query":true}\n')
        self.assertEqual(set(result), {'scenario'})
        self.assertEqual(json.loads(result['scenario'])['status'], 'observed')
        self.assertEqual(self.links, [True])
        self.assertEqual(bytes(self.modem.ports['primary'].pending), b'AT+CF')
        self.assertNotIn('pin', json.loads(result['scenario'])['observed'])

    def test_invalid_query_or_query_mixed_with_mutation_is_rejected(self):
        for payload in (b'{"sequence":1,"query":1}\n',
                        b'{"sequence":1,"query":true,"changes":{"radio":4}}\n'):
            with self.assertRaises(ValueError):
                self.channel.feed(payload)
        self.assertEqual(self.links, [True])
