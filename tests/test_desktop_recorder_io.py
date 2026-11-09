import os
from pathlib import Path
import sys
import threading
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from record_forge_desktop import RecorderIO


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'darwin', 'needs native Tk or Xvfb')
class RecorderIOTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.io = RecorderIO()
        self.owner = threading.get_ident()
        self.callbacks = []
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.io.close)

    def pump(self, complete):
        expired = []
        def tick():
            self.io.poll()
            if complete(): self.root.quit()
            else: self.root.after(5, tick)
        def timeout():
            expired.append(True)
            self.root.quit()
        deadline = self.root.after(3000, timeout)
        self.root.after(0, tick)
        self.root.mainloop()
        self.root.after_cancel(deadline)
        self.assertFalse(expired, 'GUI polling did not complete')

    def test_control_allows_gui_serial_drain_and_callback_stays_on_gui_thread(self):
        drained = threading.Event()
        def control():
            self.assertNotEqual(threading.get_ident(), self.owner)
            if not drained.wait(2): raise TimeoutError('serial backpressure')
            return {'status': 'running'}
        def callback(value, error):
            self.assertEqual(threading.get_ident(), self.owner)
            self.callbacks.append((value, error))
        self.io.submit(None, control, callback)
        self.root.after(20, drained.set)
        self.pump(lambda: bool(self.callbacks))
        self.assertEqual(self.callbacks, [({'status': 'running'}, None)])

    def test_failure_diagnostics_also_leave_gui_free_and_never_retry_input(self):
        drained = threading.Event()
        calls = []
        def fail():
            calls.append('input')
            raise TimeoutError('uncertain input')
        def observe(runtime):
            self.assertNotEqual(threading.get_ident(), self.owner)
            if not drained.wait(2): raise TimeoutError('diagnostic blocked GUI')
            return {'qmp_status': {'status': 'running'}}
        with patch('record_forge_desktop.failure_observation', side_effect=observe):
            self.io.submit(None, fail, lambda result, error: self.callbacks.append(error))
            self.root.after(20, drained.set)
            self.pump(lambda: bool(self.callbacks))
        self.assertEqual(calls, ['input'])
        self.assertEqual(self.callbacks[0]['error'], 'uncertain input')
        self.assertIn('qmp_status', self.callbacks[0]['failure_observation'])

    def test_single_inflight_and_callback_can_schedule_next_exchange(self):
        done = threading.Event()
        def second(value, error): self.callbacks.append('second')
        def first(value, error):
            self.callbacks.append('first')
            self.io.submit(None, lambda: None, second)
        self.io.submit(None, lambda: done.wait(2), first)
        with self.assertRaises(ValueError): self.io.submit(None, lambda: None, second)
        self.root.after(20, done.set)
        self.pump(lambda: len(self.callbacks) == 2)
        self.assertEqual(self.callbacks, ['first', 'second'])

    def test_diagnostic_failure_retains_original_control_error(self):
        def fail(): raise TimeoutError('original control failure')
        with patch('record_forge_desktop.failure_observation', side_effect=RuntimeError('diagnostic unavailable')):
            self.io.submit(None, fail, lambda value, error: self.callbacks.append(error))
            self.pump(lambda: bool(self.callbacks))
        self.assertEqual(self.callbacks[0]['error'], 'original control failure')
        self.assertIn('diagnostic unavailable', self.callbacks[0]['failure_observation']['diagnostic_error'])
