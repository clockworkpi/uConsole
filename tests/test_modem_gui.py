"""Exercise desktop state transitions without USB access or privilege elevation."""
import gc
import importlib.util
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipUnless(tk is not None and (os.environ.get('DISPLAY') or sys.platform == 'darwin'),
                     'requires Tk and a display (make check-gui)')
class ModemGuiTest(unittest.TestCase):
    def setUp(self):
        script = Path(__file__).resolve().parents[1] / 'Code/scripts/uconsole-modem-gui.py'
        spec = importlib.util.spec_from_file_location('modem_gui', script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.root = tk.Tk()
        self.app = self.module.Updater(self.root)
        self.addCleanup(self.close_gui)
        self.app.package.set('/firmware with spaces')
        self.app.serial.set('SERIAL')

    def close_gui(self):
        self.root.destroy()
        self.app = self.root = None
        # Tcl interpreters must be finalized on the creating thread, not
        # during garbage collection triggered by a later worker test.
        gc.collect()

    def enabled(self):
        return str(self.app.flash_button['state']) == 'normal'

    def checked(self):
        self.app.complete('check', self.app.snapshot(), 0, '', None)
        self.app.model_confirmed.set(True)
        self.app.refresh()

    def test_flash_requires_check_and_model_confirmation(self):
        self.assertFalse(self.enabled())
        self.app.complete('verify', self.app.snapshot(), 0, '', None)
        self.app.model_confirmed.set(True)
        self.app.refresh()
        self.assertFalse(self.enabled())
        self.checked()
        self.assertTrue(self.enabled())
        self.app.serial.set('OTHER')
        self.assertFalse(self.enabled())

    def test_failed_or_stale_check_does_not_enable_flash(self):
        self.app.model_confirmed.set(True)
        selection = self.app.snapshot()
        self.app.complete('check', selection, 1, '', None)
        self.assertFalse(self.enabled())
        self.app.package.set('/other')
        self.app.complete('check', selection, 0, '', None)
        self.assertFalse(self.enabled())

    def test_busy_window_cannot_close_or_start_second_operation(self):
        self.app.busy = True
        with patch.object(self.module.messagebox, 'showinfo') as info, patch.object(self.module.threading, 'Thread') as thread:
            self.app.close()
            self.app.start('verify')
            info.assert_called_once()
            thread.assert_not_called()
        self.assertTrue(self.root.winfo_exists())

    def test_cancel_confirmation_does_not_flash(self):
        self.checked()
        with patch.object(self.module.messagebox, 'askyesno', return_value=False), patch.object(self.app, 'start') as start:
            self.app.confirm_flash()
            start.assert_not_called()

    def test_installed_device_access_uses_pkexec_and_verify_does_not(self):
        with patch.object(self.module, 'BACKEND', self.module.SYSTEM_HELPER), patch.object(self.module.os, 'geteuid', return_value=1000):
            command = self.app.command('flash', self.app.snapshot())
            self.assertEqual(command[:2], ['pkexec', str(self.module.SYSTEM_HELPER)])
            self.assertIn('/firmware with spaces', command)
            self.assertIn('--flash', command)
            self.assertNotIn('pkexec', self.app.command('verify', self.app.snapshot()))

    def test_worker_records_failure_and_releases_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / 'stub.py'
            stub.write_text('print("simulated failure", flush=True)\nraise SystemExit(1)\n')
            with patch.dict(os.environ, {'XDG_STATE_HOME': tmp}), patch.object(self.app, 'command', return_value=[self.module.sys.executable, str(stub)]):
                self.app.busy = True
                self.app.worker('check', self.app.snapshot())
                self.app.poll()
            self.assertFalse(self.app.busy)
            self.assertFalse(self.enabled())
            self.assertIn('Stopped.', self.app.status.get())
            logs = list((Path(tmp) / 'uconsole-modem-updater').glob('*.log'))
            self.assertEqual(len(logs), 1)
            self.assertIn('simulated failure', logs[0].read_text())


if __name__ == '__main__':
    unittest.main()
