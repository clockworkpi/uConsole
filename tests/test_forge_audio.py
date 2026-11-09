import json
import os
import gc
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_audio import operation, query
from forge_controller import Controller
from forge_client import ClientSession


class AudioControlsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.present = True
        self.calls = []
        self.runtime = SimpleNamespace(args=SimpleNamespace(audio='usb-null'), identity='owned', control=self.control)

    def control(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'qom-list':
            model = 'usb-forge-capture' if self.runtime.args.audio == 'usb-capture' else 'usb-audio'
            return [{'name': 'audio-surrogate', 'type': 'child<' + model + '>'}] if self.present else []
        if name == 'qom-get':
            return self.present
        if name in ('device_add', 'device_del'):
            self.assertTrue((self.root / 'event.jsonl').exists())
            records = (self.root / 'event.jsonl').read_text().splitlines()
            self.assertIn('before', json.loads(records[1]))
            self.present = name == 'device_add'

    def test_query_and_disconnect_verify_state_with_durable_intent(self):
        self.assertTrue(query(self.runtime)['connected'])
        result = operation(self.runtime, self.root / 'event.jsonl', False)
        self.assertFalse(result['observed']['connected'])
        self.assertEqual(json.loads((self.root / 'event.jsonl').read_text().splitlines()[-1])['status'], 'completed')

    def test_capture_connect_uses_synthetic_device_without_host_backend(self):
        self.runtime.args.audio = 'usb-capture'
        self.present = False
        result = operation(self.runtime, self.root / 'event.jsonl', True)
        self.assertTrue(result['observed']['capture'])
        self.assertFalse(result['observed']['host_microphone'])
        self.assertIn(('device_add', {'driver': 'usb-forge-capture', 'id': 'audio-surrogate'}), self.calls)

    def test_capture_rejects_wrong_device_type(self):
        self.runtime.args.audio = 'usb-capture'
        self.runtime.control = lambda *args: [{'name': 'audio-surrogate', 'type': 'child<usb-audio>'}]
        with self.assertRaisesRegex(ValueError, 'unexpected QOM'):
            query(self.runtime)

    def test_idempotent_request_does_not_recreate_existing_device(self):
        operation(self.runtime, self.root / 'event.jsonl', True)
        self.assertFalse(any(name.startswith('device_') for name, args in self.calls))

    def test_wrong_model_and_nonboolean_request_cannot_write_device(self):
        self.runtime.args.audio = 'none'
        with self.assertRaises(ValueError):
            query(self.runtime)
        with self.assertRaises(ValueError):
            operation(self.runtime, self.root / 'event.jsonl', 1)
        self.assertEqual(self.calls, [])

    def test_failed_mutation_does_not_claim_rollback(self):
        control = self.control
        def interrupted(name, args):
            value = control(name, args)
            if name == 'device_del':
                raise ConnectionError('lost acknowledgement')
            return value
        self.runtime.control = interrupted
        with self.assertRaises(ConnectionError):
            operation(self.runtime, self.root / 'event.jsonl', False)
        self.assertFalse(self.present)
        result = json.loads((self.root / 'event.jsonl').read_text().splitlines()[-1])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['rollback'])

    def test_controller_and_attached_client_enforce_device_control_grant(self):
        owner = Controller({'main': self.root}, ('device-control',))
        self.addCleanup(owner.close)
        client = ClientSession(owner, ['main'])
        with self.assertRaises(PermissionError):
            client.call('audio_set', {'workspace': 'main', 'connected': False})
        with patch.object(owner, 'runtime', return_value=self.runtime):
            job = client.call('audio_query', {'workspace': 'main'})
            owner.jobs[job['job_id']][2].result(timeout=5)
        self.assertTrue(owner.job(job['job_id'])['result']['connected'])


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'darwin', 'needs Tk display')
class AudioPanelTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from forge_audio_gui import AudioPanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.controller = MagicMock(grants={'device-control'})
        self.controller.submit_audio.return_value = {'job_id': 'audio-job'}
        self.panel = AudioPanel(self.root, self.controller, 'gui')

    def tearDown(self):
        if self.panel.timer:
            self.panel.window.after_cancel(self.panel.timer)
        self.root.destroy()
        self.panel = self.root = self.controller = None
        gc.collect()

    def test_buttons_submit_shared_jobs_and_prevent_close_while_active(self):
        self.panel.buttons[2].invoke()
        self.controller.submit_audio.assert_called_once_with('gui', False)
        self.assertFalse(self.panel.close())
        self.assertTrue(all('disabled' in button.state() for button in self.panel.buttons))

    def test_readonly_owner_can_query_but_not_change_attachment(self):
        self.controller.grants = set()
        self.panel.enable()
        self.assertNotIn('disabled', self.panel.buttons[0].state())
        self.assertIn('disabled', self.panel.buttons[1].state())
        self.assertIn('disabled', self.panel.buttons[2].state())

    def test_failed_readback_does_not_claim_rollback(self):
        self.panel.job = 'audio-job'
        self.controller.job.return_value = {'status': 'failed', 'error': 'lost QMP response'}
        self.panel.poll()
        self.assertIn('no rollback implied', self.panel.status.get())
        self.assertIsNone(self.panel.job)
