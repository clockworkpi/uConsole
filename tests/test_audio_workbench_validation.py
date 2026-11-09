import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_audio_workbench as validation


class WorkbenchAudioGuards(unittest.TestCase):
    def test_hash_mismatch_precedes_fixture(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(validation, 'sha256', return_value='bad'), \
                patch.object(validation, 'fixture') as fixture:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validation.run(Path(directory) / 'base.img', 'expected', Path(directory) / 'output')
            fixture.assert_not_called()


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'darwin', 'needs Tk display')
class WorkbenchAudioWidgetSelection(unittest.TestCase):
    def tearDown(self):
        import gc
        # Test-local widgets have left scope; release cycles on the Tk thread.
        gc.collect()

    def test_audio_mode_must_exist_in_actual_selector(self):
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        try:
            mode = tk.StringVar(value='none')
            selector = ttk.Combobox(root, textvariable=mode,
                                    values=('none', 'usb-null', 'usb-capture'), state='readonly')
            selector.pack()
            validation.select_audio(root, mode, 'usb-capture')
            self.assertEqual(mode.get(), 'usb-capture')
            self.assertEqual(selector.current(), 2)
            with self.assertRaisesRegex(ValueError, 'missing'):
                validation.select_audio(root, mode, 'host-microphone')
            self.assertEqual(mode.get(), 'usb-capture')
            ttk.Combobox(root, textvariable=mode, values=('usb-capture',)).pack()
            with self.assertRaisesRegex(ValueError, 'missing'):
                validation.select_audio(root, mode, 'usb-capture')
        finally:
            # Cocoa schedules initial root-window work on the idle queue.
            # Drain it while this test's root still exists, not in a later
            # test's interpreter after this root has been destroyed.
            root.update_idletasks()
            root.destroy()

    def test_selects_unique_real_button_and_rejects_ambiguity(self):
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        try:
            frame = ttk.Frame(root)
            frame.pack()
            selected = ttk.Button(frame, text='Start')
            selected.pack()
            self.assertIs(validation.button(root, 'Start'), selected)
            with self.assertRaises(ValueError):
                validation.button(root, 'Missing')
            ttk.Button(root, text='Start').pack()
            with self.assertRaises(ValueError):
                validation.button(root, 'Start')
        finally:
            root.update_idletasks()
            root.destroy()
