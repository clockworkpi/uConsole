import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import uconsole_emulator as emulator


class QMPGreetingTests(unittest.TestCase):
    def test_timeout_identifies_stage_without_retry_or_arguments(self):
        greeting = {'QMP': {'version': {}, 'capabilities': []}}
        for phase, received, writes in (
                ('greeting', [], 0),
                ('qmp_capabilities reply', [greeting], 1),
                ('device_del reply', [greeting, {'return': {}, 'id': 0}], 2)):
            with self.subTest(phase=phase):
                sock, stream = self.connection(received)
                stream.readline.side_effect = [
                    (json.dumps(item) + '\n').encode() for item in received
                ] + [TimeoutError('timed out')]
                with patch.object(emulator, 'control_connection', return_value=sock) as connect:
                    with self.assertRaises(TimeoutError) as caught:
                        emulator.qmp('owned', 'device_del', {'id': 'private-argument'})
                self.assertIn('QMP device_del: timeout during ' + phase, str(caught.exception))
                self.assertNotIn('private-argument', str(caught.exception))
                self.assertEqual(stream.write.call_count, writes)
                connect.assert_called_once_with('owned')

    def test_connect_and_send_timeouts_are_not_retried(self):
        with patch.object(emulator, 'control_connection', side_effect=TimeoutError) as connect:
            with self.assertRaisesRegex(TimeoutError, 'during connect; not retried'):
                emulator.qmp('owned', 'query-status')
        connect.assert_called_once()
        sock, stream = self.connection([{'QMP': {}}])
        stream.write.side_effect = TimeoutError()
        with patch.object(emulator, 'control_connection', return_value=sock):
            with self.assertRaisesRegex(TimeoutError, 'during qmp_capabilities send; not retried'):
                emulator.qmp('owned', 'query-status')
        stream.write.assert_called_once()

    def connection(self, messages):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        stream = sock.makefile.return_value.__enter__.return_value
        stream.readline.side_effect = [(json.dumps(item) + '\n').encode() for item in messages]
        return sock, stream

    def test_queued_event_precedes_greeting_without_replaying_command(self):
        sock, stream = self.connection([
            {'event': 'DEVICE_DELETED', 'data': {'device': 'audio-surrogate'}},
            {'QMP': {'version': {}, 'capabilities': []}},
            {'return': {}, 'id': 0}, {'return': {}, 'id': 1}])
        with patch.object(emulator, 'control_connection', return_value=sock):
            self.assertEqual(emulator.qmp('owned', 'device_del', {'id': 'audio-surrogate'}), {})
        sent = [json.loads(call.args[0])['execute'] for call in stream.write.call_args_list]
        self.assertEqual(sent, ['qmp_capabilities', 'device_del'])

    def test_invalid_greetings_never_send_commands(self):
        for message in ({'return': {}}, {'event': 'x', 'id': 1}, [], {'QMP': False}):
            with self.subTest(message=message):
                sock, stream = self.connection([message])
                with patch.object(emulator, 'control_connection', return_value=sock):
                    with self.assertRaises(ValueError):
                        emulator.qmp('owned', 'device_del')
                stream.write.assert_not_called()

    def test_event_flood_is_bounded(self):
        sock, stream = self.connection([{'event': 'DEVICE_DELETED'}] * 64)
        with patch.object(emulator, 'control_connection', return_value=sock):
            with self.assertRaisesRegex(ValueError, 'Too many'):
                emulator.qmp('owned', 'query-status')
        stream.write.assert_not_called()

    def test_closed_socket_before_greeting_is_not_success(self):
        sock, stream = self.connection([])
        stream.readline.side_effect = None
        stream.readline.return_value = b''
        with patch.object(emulator, 'control_connection', return_value=sock):
            with self.assertRaisesRegex(ConnectionError, 'before greeting'):
                emulator.qmp('owned', 'query-status')
        stream.write.assert_not_called()

    def test_pre_greeting_events_cannot_extend_deadline(self):
        sock, stream = self.connection([{'event': 'DEVICE_DELETED'}])
        with patch.object(emulator, 'control_connection', return_value=sock), \
                patch.object(emulator.time, 'monotonic', side_effect=[0, 1, 6]):
            with self.assertRaisesRegex(TimeoutError, 'greeting deadline'):
                emulator.qmp('owned', 'device_del')
        stream.write.assert_not_called()
