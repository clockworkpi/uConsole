"""Owned process controls, identity checks and serial transaction exclusion."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_runtime import Runtime
from forge_workspace import WorkspaceLock
from uconsole_emulator import control_command, parser
from uconsole_agent import GuestChannelUncertain


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        (self.workspace / 'machine.json').write_text(json.dumps({'root': 'PARTUUID=1234-02'}))
        self.args = parser().parse_args(['--workspace', str(self.workspace), 'run', '--mode', 'maintenance'])
        self.runtime = Runtime(self.args)
        self.child = MagicMock()
        self.child.poll.return_value = None
        self.addCleanup(self.cleanup_runtime)

    def cleanup_runtime(self):
        self.child.poll.return_value = 0
        self.runtime.release()

    def start(self):
        with patch('forge_runtime.refresh_boot'), \
             patch('forge_runtime.subprocess.Popen', return_value=self.child) as spawn:
            self.runtime.start()
        return spawn.call_args

    def test_start_owns_private_sockets_and_workspace_lock(self):
        call = self.start()
        cmd = call.args[0]
        self.assertIn('unix:', cmd[cmd.index('-qmp') + 1])
        self.assertIn(self.runtime.identity, cmd)
        self.assertNotIn('tcp:', cmd[cmd.index('-qmp') + 1])
        with self.assertRaisesRegex(ValueError, 'busy'):
            WorkspaceLock(self.workspace)
        with self.assertRaisesRegex(ValueError, 'running emulator'):
            self.runtime.release()

    def test_composite_modem_initializes_while_vm_paused(self):
        self.args.modem = 'composite'
        exists = Path.exists
        with patch('forge_runtime.refresh_boot'), \
             patch('forge_runtime.subprocess.Popen', return_value=self.child) as spawn, \
             patch('pathlib.Path.exists', lambda path: path.name == 'qmp' or exists(path)), \
             patch('forge_modem_runtime.OwnedModem') as model, \
             patch.object(self.runtime, 'control') as control:
            self.runtime.start()
        self.assertIn('-S', spawn.call_args.args[0])
        model.assert_called_once_with(self.runtime)
        model.return_value.start.assert_called_once_with()
        control.assert_called_once_with('cont')

    def test_clean_stop_stops_modem_before_qemu(self):
        self.start()
        events = []
        self.runtime.modem = MagicMock()
        self.runtime.modem.stop.side_effect = lambda **kwargs: events.append('modem')
        def quit(*args):
            events.append('qemu')
            self.child.poll.return_value = 0
        with patch.object(self.runtime, 'execute', return_value={'exit_code': 0}), \
             patch.object(self.runtime, 'control', side_effect=quit):
            self.runtime.stop()
        self.assertEqual(events[:2], ['modem', 'qemu'])

    def test_forced_stop_terminates_owned_vm_despite_modem_uncertainty(self):
        self.start()
        modem = MagicMock()
        self.runtime.modem = modem
        def stop(*, vm_dead=False):
            if not vm_dead:
                raise RuntimeError('Modem shutdown is uncertain')
        modem.stop.side_effect = stop
        self.child.wait.side_effect = lambda **kwargs: setattr(self.child.poll, 'return_value', 0)
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            self.runtime.stop(force=True)
        self.child.terminate.assert_called_once_with()
        self.assertIsNone(self.runtime.modem)
        self.assertIsNone(self.runtime.directory)
        with WorkspaceLock(self.workspace):
            pass

    def test_uncertain_modem_clean_stop_keeps_vm_owned(self):
        self.start()
        self.runtime.modem = MagicMock()
        self.runtime.modem.stop.side_effect = RuntimeError('Modem shutdown is uncertain')
        with patch.object(self.runtime, 'execute', return_value={'exit_code':0}), \
             patch.object(self.runtime, 'control') as control:
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                self.runtime.stop()
            control.assert_not_called()
        self.child.terminate.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'busy'):
            WorkspaceLock(self.workspace)
        self.runtime.modem.stop.side_effect = None

    def test_controls_check_identity_before_mutation(self):
        self.start()
        with patch('uconsole_emulator.qmp', return_value={'name': 'some other VM'}) as monitor:
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.runtime.control('quit')
            self.assertEqual([call.args[1] for call in monitor.call_args_list], ['query-name'])
        with patch('uconsole_emulator.qmp', side_effect=[{'name': self.runtime.identity}, {}]) as monitor:
            self.runtime.control('stop')
            self.assertEqual([call.args[1] for call in monitor.call_args_list], ['query-name', 'stop'])

    def test_duplex_recording_is_private_exclusive_and_retained(self):
        self.args.audio = 'usb-duplex'
        command = self.start().args[0]
        recording = self.runtime.audio_recording
        self.assertEqual(recording.stat().st_mode & 0o777, 0o600)
        self.assertEqual(recording.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn('usb-forge-capture,id=audio-capture', command)
        recording.write_bytes(b'preserve')
        self.child.poll.return_value = 0
        self.runtime.release()
        self.assertEqual(recording.read_bytes(), b'preserve')

    def test_dead_child_never_connects_to_endpoints(self):
        self.start()
        self.child.poll.return_value = 1
        with patch('uconsole_emulator.qmp') as monitor, \
             patch('uconsole_emulator.control_connection') as serial:
            with self.assertRaisesRegex(ValueError, 'not running'):
                self.runtime.control('quit')
            with self.assertRaises(ValueError):
                self.runtime.connect_serial()
            monitor.assert_not_called()
            serial.assert_not_called()

    def test_failed_refresh_releases_lock_without_spawning(self):
        with patch('forge_runtime.refresh_boot', side_effect=ValueError('invalid guest')), \
             patch('forge_runtime.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'invalid guest'):
                self.runtime.start()
            spawn.assert_not_called()
        with WorkspaceLock(self.workspace):
            pass

    def test_cancel_after_refresh_preserves_logs_and_releases_lock(self):
        log = self.workspace / 'serial.log'
        log.write_text('previous boot evidence')
        check = MagicMock(side_effect=[None, InterruptedError('cancelled')])
        with patch('forge_runtime.refresh_boot') as refresh, \
             patch('forge_runtime.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(InterruptedError, 'cancelled'):
                self.runtime.start(check_cancel=check)
            refresh.assert_called_once()
            spawn.assert_not_called()
        self.assertEqual(log.read_text(), 'previous boot evidence')
        self.assertIsNone(self.runtime.directory)
        with WorkspaceLock(self.workspace):
            pass

    def test_complete_guest_transactions_are_serialized(self):
        self.start()
        operation = MagicMock(return_value={'exit_code': 0})
        with self.runtime.serial_mutex:
            with self.assertRaisesRegex(ValueError, 'in progress'):
                self.runtime.guest_operation(operation, 'hello')
        operation.assert_not_called()
        self.runtime.guest_operation(operation, 'hello')
        operation.assert_called_once_with(self.runtime.serial_endpoint, 'hello')

    def test_failed_remount_does_not_quit_guest(self):
        self.start()
        with patch.object(self.runtime, 'execute', return_value={'exit_code': 1}), \
             patch.object(self.runtime, 'control') as control:
            with self.assertRaisesRegex(ValueError, 'left running'):
                self.runtime.stop()
            control.assert_not_called()

    def test_uncertain_guest_channel_blocks_reuse_without_releasing_vm(self):
        self.start()
        operation = MagicMock(side_effect=GuestChannelUncertain('no completion'))
        with self.assertRaises(GuestChannelUncertain):
            self.runtime.guest_operation(operation)
        self.assertEqual(self.runtime.guest_channel_error, 'no completion')
        with patch('uconsole_agent.serial_exec') as serial, \
             patch('uconsole_agent.guest_put') as upload, \
             patch('uconsole_agent.guest_get') as download, \
             patch.object(self.runtime, 'control') as control:
            for action in (lambda: self.runtime.execute('true'),
                           lambda: self.runtime.upload('/source', '/target'),
                           lambda: self.runtime.download('/source', '/target'),
                           self.runtime.stop):
                with self.assertRaisesRegex(ValueError, 'channel is uncertain'):
                    action()
            for mock in (serial, upload, download, control):
                mock.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'busy'):
            WorkspaceLock(self.workspace)
        self.child.terminate.assert_not_called()

    def test_guest_preflight_error_does_not_poison_channel(self):
        self.start()
        with self.assertRaises(ValueError):
            self.runtime.guest_operation(MagicMock(side_effect=ValueError('invalid guest path')))
        self.assertIsNone(self.runtime.guest_channel_error)
        operation = MagicMock(return_value={'exit_code': 7})
        self.assertEqual(self.runtime.guest_operation(operation), {'exit_code': 7})

    def test_socket_cli_control_requires_matching_identity(self):
        args = parser().parse_args(['control', 'quit', '--qmp-socket', '/tmp/fixture'])
        with patch('uconsole_emulator.qmp') as control:
            with self.assertRaisesRegex(ValueError, 'requires --runtime-id'):
                control_command(args)
            control.assert_not_called()
        args.runtime_id = 'expected'
        with patch('uconsole_emulator.qmp', return_value={'name': 'wrong'}) as control:
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                control_command(args)
            self.assertEqual([call.args[1] for call in control.call_args_list], ['query-name'])


if __name__ == '__main__':
    unittest.main()
