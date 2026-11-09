"""Host preferences, setup consent, state vocabulary and terminal integration."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


class PreferencesTests(unittest.TestCase):
    def test_unapproved_download_never_uses_network(self):
        from workbench_onboarding import download, IMAGE_NAME
        with tempfile.TemporaryDirectory() as directory, patch('workbench_onboarding.urllib.request.build_opener') as network:
            cache = Path(directory)
            with self.assertRaisesRegex(ValueError, 'approve'):
                download(cache, allow_download=False)
            (cache/IMAGE_NAME).write_bytes(b'corrupt cache')
            with self.assertRaisesRegex(ValueError, 'approve'):
                download(cache, allow_download=False)
            self.assertEqual((cache/IMAGE_NAME).read_bytes(), b'corrupt cache')
            network.assert_not_called()

    def test_targets_roundtrip_and_validation(self):
        from workbench_targets import destination, load, save
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'hosts.json'
            self.assertEqual(load(path), [])
            rows = [{'host': 'clockworkpi.local', 'username': 'pi'}]
            save(path, rows)
            self.assertEqual(load(path), rows)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(destination('clockworkpi'), 'clockworkpi')
        for host in ('-oProxyCommand=x', 'a b', 'a;id', 'user@host'):
            with self.assertRaises(ValueError):
                destination(host)

    def test_ssh_readonly_and_strict_trust(self):
        from workbench_targets import test_connection
        with patch('workbench_targets.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(test_connection('clockworkpi')['connected'])
        self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][-2:], ['clockworkpi', 'true'])

    def test_state_presentation_preserves_evidence(self):
        from workbench_schematic_gui import presentation
        event = dict(state='unknown', detail='none', source='', time=None)
        self.assertEqual(presentation(event, 'core', running=False)['state'], 'off')
        self.assertEqual(presentation(event, 'core', running=True)['state'], 'waiting')
        self.assertEqual(event['state'], 'unknown')
        event.update(state='disconnected', source='QOM inventory')
        value = presentation(event, 'audio', running=True)
        self.assertEqual(value['state'], 'disabled')
        self.assertIn('Setup', value['detail'])
        event['source'] = 'QOM USB attachment'
        self.assertEqual(presentation(event, 'audio', running=True)['state'], 'disconnected')
        self.assertNotEqual(presentation(event, 'audio', running=False, replay=True)['state'], 'off')


class TkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(self.root.destroy)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_terminal_cursor_colors_clear_and_alternate_screen(self):
        from workbench_terminal import Terminal
        terminal = Terminal(self.root, Mock())
        terminal.feed('hello\rX\x1b[31mR\x1b[0m')
        self.assertEqual(terminal.get('1.0', '1.5'), 'XRllo')
        self.assertEqual(terminal.screen.buffer[0][1].fg, 'red')
        terminal.feed('\x1b[?1049hother\x1b[?1049l')
        self.assertEqual(terminal.get('1.0', '1.5'), 'XRllo')
        terminal.feed('\x1b[2J\x1b[Hdone')
        self.assertEqual(terminal.get('1.0', '1.4'), 'done')
        terminal.feed('\x1b[3;4H')
        self.assertEqual(terminal.index('insert'), '3.3')
        response = Mock()
        terminal.feed('\x1b[6n', reply=response)
        response.assert_called_once_with(b'\x1b[3;4R')
        response.reset_mock()
        terminal.feed('\x1b[6n')
        response.assert_not_called()

    def test_terminal_key_input_and_paste_confirmation(self):
        from workbench_terminal import Terminal
        send = Mock()
        terminal = Terminal(self.root, send)
        terminal.key(SimpleNamespace(keysym='c', char='\x03', state=4))
        send.assert_called_with(b'\x03')
        terminal.key(SimpleNamespace(keysym='Up', char='', state=0))
        send.assert_called_with(b'\x1b[A')
        terminal.feed('\x1b[?1h')
        terminal.key(SimpleNamespace(keysym='Up', char='', state=0))
        send.assert_called_with(b'\x1bOA')
        terminal.clipboard_clear()
        terminal.clipboard_append('poweroff\n')
        send.reset_mock()
        with patch('workbench_terminal.messagebox.askyesno', return_value=False):
            terminal.paste()
        send.assert_not_called()

    def wizard(self):
        import tkinter as tk
        from workbench_wizard import Wizard
        app = SimpleNamespace(root=self.root, workspace=Path(self.temp.name)/'guest',
                              guard=lambda fn: fn(), check_startable=Mock())
        for name, value in [('mode', 'desktop'), ('display', 'gtk'), ('keyboard', 'generic'),
                            ('audio', 'none'), ('adc_reference', 'fixed'), ('modem', 'none')]:
            setattr(app, name, tk.StringVar(value=value))
        panel = SimpleNamespace(window=Mock(), flow_active=False, process=None,
                                custom_image=tk.BooleanVar(), begin=Mock())
        app.show_setup = Mock(side_effect=lambda: setattr(app, 'setup_panel', panel))
        return app, panel, Wizard(app)

    def test_wizard_cancel_does_not_apply(self):
        app, panel, wizard = self.wizard()
        wizard.values['audio'].set('playback')
        wizard.window.destroy()
        self.assertEqual(app.audio.get(), 'none')
        panel.begin.assert_not_called()

    def test_wizard_finish_snapshots_permissions_and_options(self):
        app, panel, wizard = self.wizard()
        wizard.values['keyboard'].set('composite')
        wizard.download.set(True)
        wizard.show(3)
        wizard.next()
        self.assertEqual(app.keyboard.get(), 'composite')
        self.assertTrue(panel.wizard_consent['download'])
        self.assertFalse(panel.wizard_consent['packages'])
        panel.begin.assert_called_once()

    def test_host_save_does_not_approve_and_test_is_async(self):
        from forge_target_gui import TargetPanel
        owner = Mock()
        owner.call.return_value = {'transactions': [], 'execution_granted': False}
        with patch('workbench_targets.default_path', return_value=Path(self.temp.name)/'hosts.json'):
            panel = TargetPanel(self.root, owner, 'gui')
        panel.host_name.set('clockworkpi.local')
        panel.save_host()
        owner.approve_target_policy.assert_not_called()
        owner.submit_target.assert_not_called()
        self.assertEqual(panel.choice['values'], '')
        owner.submit.return_value = {'job_id': 'test'}
        panel.test_host()
        self.assertEqual(panel.job_kind, 'connection-test')
        owner.job.return_value = {'status': 'completed', 'result': {'host': 'clockworkpi.local'}}
        panel.poll()
        self.assertIn('Connected', panel.status.get())
        panel.close()
