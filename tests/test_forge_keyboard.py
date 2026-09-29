"""Persistent production firmware state and owned-transport failure boundaries."""
import os
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_keyboard import KeyboardBridge, default_oracle
from forge_controller import Controller
from forge_keyboard_host import HostPointer, wheel_commands


class Transport:
    def __init__(self):
        self.args = SimpleNamespace(keyboard='composite')
        self.identity = 'owned-fixture'
        self.leds, self.epoch, self.queued = 0, 1, 0
        self.reports = []
        self.calls = []
        self.fail_next = False

    def control(self, command, arguments):
        self.calls.append((command, arguments))
        if command == 'qom-get':
            return (f'queued={self.queued} leds={self.leds} cdc-rx=0 lines=0 '
                    f'reset-requested=0 configuration=1 epoch={self.epoch}')
        if command != 'qom-set':
            raise AssertionError(command)
        self.reports.append(arguments['value'])
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError('lost QMP acknowledgement')


class ControllerKeyboardTests(unittest.TestCase):
    def test_owner_discovery_prefers_package_and_requires_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            build = root / 'build'
            packaged = root / 'bin/keyboard-oracle'
            built = build / 'keyboard-oracle/keyboard-oracle'
            for target in (packaged, built):
                target.parent.mkdir(parents=True)
                target.write_text('fixture')
                target.chmod(0o600)
            self.assertIsNone(default_oracle(root, build))
            built.chmod(0o700)
            self.assertEqual(default_oracle(root, build), built)
            packaged.chmod(0o700)
            self.assertEqual(default_oracle(root, build), packaged)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.controller = Controller({'test': self.root}, grants=('device-control',),
                                     keyboard_oracle=self.root / 'owner-oracle')
        self.addCleanup(self.controller.close)
        self.runtime = MagicMock(identity='owned-fixture')
        self.runtime.process.poll.return_value = 0
        self.controller.runtimes['test'] = self.runtime

    def test_permissions_and_owner_configuration_precede_submission(self):
        self.controller.grants = frozenset()
        with self.assertRaises(PermissionError):
            self.controller.submit_keyboard('test', [('state',)])
        self.controller.grants = frozenset({'device-control'})
        self.controller.keyboard_oracle = None
        with self.assertRaisesRegex(ValueError, 'Owner'):
            self.controller.submit_keyboard('test', [('state',)])
        self.assertEqual(self.controller.jobs, {})

    def test_persistent_bridge_and_failure_evidence(self):
        bridge = MagicMock(identity='owned-fixture')
        bridge.last_attempt = {'status': 'completed', 'reports': []}
        bridge.apply.return_value = bridge.last_attempt
        with patch('forge_controller.KeyboardBridge', return_value=bridge) as factory:
            job = self.controller.submit_keyboard('test', [('state',)])
            self.controller.jobs[job['job_id']][2].result(timeout=5)
            # The worker callback releases the workspace before Future completion.
            bridge.last_attempt = {'status': 'failed', 'pending_report': '0200'}
            bridge.apply.side_effect = TimeoutError('uncertain delivery')
            job = self.controller.submit_keyboard('test', [('run', 1)])
            with self.assertRaises(TimeoutError):
                self.controller.jobs[job['job_id']][2].result(timeout=5)
            factory.assert_called_once_with(self.runtime, self.root / 'owner-oracle')
        records = [json.loads(p.read_text()) for p in self.root.glob('keyboard-event-*.json')]
        self.assertEqual(len(records), 2)
        self.assertIn({'status': 'failed', 'pending_report': '0200'}, records)
        self.assertFalse(self.controller.job_controls[job['job_id']][1])

    def test_stop_reaps_oracle_only_after_successful_vm_stop(self):
        bridge = MagicMock()
        self.controller.keyboards['test'] = bridge
        self.runtime.stop.side_effect = ValueError('guest still running')
        with self.assertRaisesRegex(ValueError, 'still running'):
            self.controller.stop_runtime('test')
        bridge.close.assert_not_called()
        self.assertIs(self.controller.keyboards['test'], bridge)
        self.runtime.stop.side_effect = None
        self.assertEqual(self.controller.stop_runtime('test'), {'stopped': True})
        bridge.close.assert_called_once_with()
        self.assertNotIn('test', self.controller.keyboards)

    def test_oracle_cleanup_error_does_not_skip_owned_vm_cleanup(self):
        bridge = MagicMock()
        bridge.close.side_effect = OSError('oracle wait failed')
        self.controller.keyboards['test'] = bridge
        self.runtime.process.poll.return_value = None
        try:
            with self.assertRaisesRegex(RuntimeError, 'oracle wait failed'):
                self.controller.close()
            self.runtime.stop.assert_called_once_with()
        finally:
            bridge.close.side_effect = None
            self.runtime.process.poll.return_value = 0


class KeyboardBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.oracle = Path(temporary.name) / 'oracle'
        root = Path(__file__).resolve().parents[1]
        subprocess.run([*shlex.split(os.environ.get('CXX', 'c++')), '-std=c++17',
                        '-I', str(root / 'tools/keyboard-oracle'),
                        str(root / 'tools/keyboard-oracle/main.cpp'), '-o', str(cls.oracle)],
                       check=True, capture_output=True, timeout=60)

    def setUp(self):
        self.runtime = Transport()
        self.bridge = KeyboardBridge(self.runtime, self.oracle)
        self.addCleanup(self.bridge.close)

    def test_fn_and_pressed_identity_survive_separate_actions(self):
        self.bridge.apply([('matrix', 7, 2, 1), ('run', 10)])
        pid = self.bridge.process.pid
        self.bridge.apply([('matrix', 1, 0, 1), ('run', 10)])
        self.bridge.apply([('matrix', 7, 2, 0), ('run', 10)])
        result = self.bridge.apply([('matrix', 1, 0, 0), ('run', 10)])
        self.assertEqual(self.bridge.process.pid, pid)
        self.assertIn('0200003a0000000000', self.runtime.reports)
        self.assertEqual(result['reports'][-1]['report_hex'], '020000000000000000')
        self.assertEqual(result['status'], 'completed')

    def test_guest_led_feedback_changes_caps_adjusted_report(self):
        self.bridge.apply([('matrix', 7, 2, 1), ('run', 10)])
        self.bridge.apply([('matrix', 2, 3, 1), ('run', 10)])
        self.runtime.leds = 2
        result = self.bridge.apply([('matrix', 4, 2, 1), ('run', 10)])
        # Caps remains held in slot 0, so A occupies slot 1; Shift cancels host Caps.
        self.assertEqual(result['reports'][-1]['report_hex'], '020200390400000000')
        self.assertEqual(result['reports'][-1]['leds'], 2)

    def test_host_pointer_edges_execute_production_trackball_firmware(self):
        pointer = HostPointer()
        pointer.move(0, 0)
        for commands in pointer.move(16, 0):
            self.bridge.apply(commands)
        mouse = [bytes.fromhex(report) for report in self.runtime.reports if report.startswith('01')]
        self.assertTrue(mouse)
        self.assertTrue(any(0 < report[2] < 128 for report in mouse))
        self.assertTrue(all(report[3] == 0 for report in mouse))

    def test_select_scroll_preserves_firmware_space_side_effect(self):
        self.bridge.apply(wheel_commands(1))
        self.assertIn('0200002c0000000000', self.runtime.reports)
        self.assertIn('020000000000000000', self.runtime.reports)
        self.assertIn('0100000001', self.runtime.reports)
        self.bridge.apply(wheel_commands(-1))
        self.assertIn('01000000ff', self.runtime.reports)

    def test_invalid_commands_never_launch_or_touch_runtime(self):
        for commands in ([('sync', 'a' * 32)], [('run', 33)], [('key', 0, True)],
                         [('matrix', 8, 0, 1)], [('run\nstate', 1)], []):
            with self.subTest(commands=commands), self.assertRaises(ValueError):
                self.bridge.apply(commands)
        self.assertIsNone(self.bridge.process)
        self.assertEqual(self.runtime.calls, [])
        self.assertIsNone(self.bridge.error)

    def test_uncertain_delivery_retains_pending_report_and_never_retries(self):
        self.bridge.apply([('state',)])
        self.runtime.fail_next = True
        with self.assertRaisesRegex(TimeoutError, 'lost QMP'):
            self.bridge.apply([('matrix', 4, 2, 1), ('run', 10)])
        attempt = self.bridge.last_attempt
        self.assertEqual(attempt['pending_report'], '020000040000000000')
        self.assertEqual(attempt['reports'], [])
        self.assertEqual(attempt['status'], 'failed')
        self.assertFalse(attempt['rollback'])
        count = len(self.runtime.calls)
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.bridge.apply([('state',)])
        self.assertEqual(len(self.runtime.calls), count)

    def test_bus_reset_or_owner_change_prevents_further_injection(self):
        for field in ('epoch', 'identity'):
            runtime = Transport()
            with self.subTest(field=field):
                bridge = KeyboardBridge(runtime, self.oracle)
                try:
                    bridge.apply([('state',)])
                    setattr(runtime, field, 2 if field == 'epoch' else 'replacement')
                    before = list(runtime.reports)
                    with self.assertRaises(ValueError):
                        bridge.apply([('matrix', 4, 2, 1), ('run', 10)])
                    self.assertEqual(runtime.reports, before)
                finally:
                    bridge.close()

    def test_queue_deadline_is_not_silent_report_loss(self):
        self.bridge.apply([('state',)])
        self.runtime.queued = 32
        before = list(self.runtime.reports)
        with self.assertRaisesRegex(TimeoutError, 'queue did not drain'):
            self.bridge.apply([('matrix', 4, 2, 1), ('run', 10)], timeout=1)
        self.assertEqual(self.runtime.reports, before)
        self.assertIsNotNone(self.bridge.error)

    def test_close_owns_only_child_and_does_not_claim_key_release(self):
        self.bridge.apply([('matrix', 4, 2, 1), ('run', 10)])
        before = list(self.runtime.calls)
        self.bridge.close()
        self.assertIsNotNone(self.bridge.process.poll())
        self.assertEqual(self.runtime.calls, before)
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.bridge.apply([('state',)])

    def test_concurrent_action_is_rejected_before_dispatch(self):
        self.bridge.mutex.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'Another keyboard action'):
                self.bridge.apply([('state',)])
            self.assertEqual(self.runtime.calls, [])
        finally:
            self.bridge.mutex.release()

    def test_invalid_oracle_protocol_poisoned_without_report_injection(self):
        header = {'evidence': 'host-firmware-semantic-oracle', 'schema': 1}
        cases = (
            [dict(header, schema=True)],
            [header, {'sync': 'wrong-token', 'us': 0}],
            [header, {'us': -1, 'event': 'state', 'args': [0, 0, 0]}],
            [header, {'us': 0, 'event': 'state', 'args': [], 'extra': 1}],
        )
        for records in cases:
            with self.subTest(records=records):
                runtime = Transport()
                bridge = KeyboardBridge(runtime, self.oracle)
                # Prebuffer invalid protocol data; no subprocess or pipe is needed.
                bridge.process = SimpleNamespace(stdin=io.BytesIO())
                bridge.buffer = bytearray(b''.join(json.dumps(r).encode() + b'\n'
                                                   for r in records))
                with self.assertRaises(ValueError):
                    bridge.apply([('state',)])
                self.assertEqual(runtime.reports, [])
                self.assertEqual(bridge.last_attempt['status'], 'failed')
                self.assertIsNotNone(bridge.error)

    def test_short_oracle_write_is_not_retried(self):
        self.bridge.process = SimpleNamespace(stdin=io.BytesIO())
        # Detach the fake child before normal cleanup, which owns real Popen objects.
        self.addCleanup(setattr, self.bridge, 'process', None)
        with patch.object(self.bridge.process.stdin, 'write', return_value=1) as write:
            with self.assertRaisesRegex(OSError, 'Incomplete oracle command write'):
                self.bridge.transact([('state',)], time.monotonic() + 1)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(self.runtime.reports, [])
