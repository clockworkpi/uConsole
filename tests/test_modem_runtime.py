import json
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forge_modem_runtime import OwnedModem
from uconsole_emulator import command, parser


class OwnedModemTests(unittest.TestCase):
    def test_connection_checks_identity_and_readback(self):
        runtime = Mock()
        modem = OwnedModem(runtime)
        modem.worker = Mock()
        modem.worker.is_alive.return_value = True
        runtime.control.side_effect = [[{'name':'modem-device','type':'child<usb-forge-modem>'}], True, {}, False]
        result = modem.connect(False)
        self.assertFalse(result['connected'])
        self.assertEqual([call.args[0] for call in runtime.control.call_args_list],
                         ['qom-list', 'qom-get', 'qom-set', 'qom-get'])
        self.assertIsNone(modem.failure)

    def test_connection_identity_mismatch_never_writes(self):
        runtime = Mock()
        modem = OwnedModem(runtime)
        modem.worker = Mock()
        runtime.control.return_value = [{'name':'modem-device','type':'child<other>'}]
        with self.assertRaisesRegex(ValueError, 'identity'):
            modem.connect(False)
        runtime.control.assert_called_once()

    def test_connection_uncertainty_disables_further_operations(self):
        runtime = Mock()
        modem = OwnedModem(runtime)
        modem.worker = Mock()
        runtime.control.side_effect = [[{'name':'modem-device','type':'child<usb-forge-modem>'}], True,
                                      TimeoutError('lost response')]
        with self.assertRaises(TimeoutError):
            modem.connect(False)
        with self.assertRaises(RuntimeError):
            modem.connect(False)
        self.assertEqual(runtime.control.call_count, 3)

    def test_acknowledgement_uses_one_absolute_deadline(self):
        modem = OwnedModem(Mock())
        modem.worker = Mock()
        modem.worker.is_alive.return_value = True
        modem.owner = Mock()
        modem.owner.recv.return_value = b'{'
        with patch('forge_modem_runtime.time.monotonic', side_effect=[0, 1, 11]):
            with self.assertRaisesRegex(TimeoutError, 'deadline'):
                modem.query()
        self.assertEqual([call.args[0] for call in modem.owner.settimeout.call_args_list], [10, 9])
        self.assertEqual(modem.inspect()['status'], 'unavailable')
        with self.assertRaises(RuntimeError):
            modem.query()
        modem.owner.sendall.assert_called_once()

    def test_link_failure_wakes_owner_and_preserves_cause(self):
        runtime = SimpleNamespace(require_alive=Mock(), control=Mock(return_value={}),
                                  qmp_endpoint=Path('/private/qmp'), process=Mock())
        runtime.process.poll.return_value = None
        a, ga = socket.socketpair()
        b, gb = socket.socketpair()
        modem = OwnedModem(runtime)
        try:
            with patch('uconsole_emulator.control_connection', side_effect=[a, b]):
                modem.start()
            runtime.control.side_effect = TimeoutError('lost QMP acknowledgement')
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                modem.change({'radio':4})
            self.assertLess(time.monotonic()-started, 2)
            modem.worker.join(2)
            self.assertFalse(modem.worker.is_alive())
            self.assertIn('lost QMP acknowledgement', modem.inspect()['error'])
            # No compensating repeat of an uncertain link write.
            self.assertEqual(runtime.control.call_count, 2)
        finally:
            modem.stop(vm_dead=True)
            ga.close()
            gb.close()

    def test_private_worker_changes_and_stops_before_vm(self):
        runtime = SimpleNamespace(require_alive=Mock(), control=Mock(return_value={}),
                                  qmp_endpoint=Path('/private/qmp'),
                                  process=Mock())
        runtime.process.poll.return_value = None
        primary, guest_primary = socket.socketpair()
        secondary, guest_secondary = socket.socketpair()
        modem = OwnedModem(runtime)
        try:
            with patch('uconsole_emulator.control_connection', side_effect=[primary, secondary]) as connect:
                modem.start()
            self.assertEqual([c.args[0] for c in connect.call_args_list],
                             [Path('/private/modem-primary'), Path('/private/modem-secondary')])
            calls = runtime.control.call_count
            observed = modem.query()
            self.assertEqual(observed['status'], 'observed')
            self.assertEqual(runtime.control.call_count, calls)
            result = modem.change({'registration': 3})
            self.assertFalse(result['observed']['link_up'])
            self.assertEqual(result['sequence'], 2)
            modem.stop()
            self.assertFalse(modem.worker.is_alive())
            runtime.control.assert_called_with('set_link', {'name':'modem-device', 'up':False})
        finally:
            modem.stop(vm_dead=True)
            guest_primary.close()
            guest_secondary.close()

    def test_command_requires_owner_and_uses_private_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'machine.json').write_text(json.dumps({'root':'PARTUUID=test'}))
            args = parser().parse_args(['--workspace', directory, 'run', '--modem', 'composite'])
            with self.assertRaisesRegex(ValueError, 'owned runtime'):
                command(args)
            cmd = command(args, path/'private')
            channels = [item for item in cmd if item.startswith('socket,id=modem-')]
            self.assertEqual(len(channels), 2)
            self.assertTrue(all('path=' in item and 'host=' not in item for item in channels))
            self.assertIn('user,id=modem-network,net=10.0.3.0/24', cmd)
            args.modem_at_port = 4560
            with self.assertRaisesRegex(ValueError, 'without --modem-at-port'):
                command(args, path/'private')
