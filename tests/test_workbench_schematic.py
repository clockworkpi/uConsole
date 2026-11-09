"""Functional schematic source mapping and honest observation semantics."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workbench_schematic import COMPONENTS, BY_ID, LINKS, Observations, Source


class SchematicModelTests(unittest.TestCase):
    def test_all_sources_resolve_in_current_tree(self):
        for component in COMPONENTS:
            self.assertTrue((ROOT / component.reference).is_file())
            for link in component.sources:
                with self.subTest(component=component.id, link=link):
                    path, line, text = link.resolve(ROOT)
                    self.assertTrue(path.is_file())
                    self.assertIn(link.symbol, text.splitlines()[line - 1])
        self.assertEqual(len(BY_ID), len(COMPONENTS))
        for left, right, label in LINKS:
            self.assertIn(left, BY_ID)
            self.assertIn(right, BY_ID)
            self.assertTrue(label)

    def test_source_escape_and_missing_symbol(self):
        with self.assertRaises(ValueError):
            Source('unsafe', '../outside.py', 'x').resolve(ROOT)
        with self.assertRaises(ValueError):
            Source('missing', 'tools/workbench_schematic.py', 'not_a_symbol').resolve(ROOT)

    def event(self):
        return dict(identity='vm1', component='keyboard', state='active', time=10.,
                    source='test trace', detail='HID report')

    def test_identity_reset_rejects_late_worker_results(self):
        state = Observations()
        state.bind('vm1')
        self.assertTrue(state.accept(self.event()))
        state.bind('vm2')
        self.assertFalse(state.accept(self.event()))
        self.assertEqual(state.view('keyboard', 10)['state'], 'unknown')
        state.bind(None)
        self.assertFalse(state.accept(self.event()))

    def test_activity_pulse_expires_and_state_goes_stale(self):
        state = Observations()
        state.bind('vm1')
        state.accept(self.event())
        self.assertEqual(state.view('keyboard', 10.1)['state'], 'active')
        self.assertEqual(state.view('keyboard', 11)['state'], 'present')
        self.assertEqual(state.view('keyboard', 14)['state'], 'stale')
        self.assertEqual(state.view('audio', 10)['state'], 'unknown')

    def test_old_samples_and_invalid_timestamps(self):
        state = Observations()
        state.bind('vm1')
        state.accept(self.event())
        old = self.event()
        old['time'] = 9
        self.assertFalse(state.accept(old))
        self.assertEqual(state.dropped, 1)
        for value in (float('nan'), float('inf'), -1, True):
            old['time'] = value
            with self.assertRaises(ValueError):
                state.accept(old)


class SchematicGuiTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
        except ImportError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        from workbench_schematic_gui import Schematic
        self.opened = []
        self.panel = Schematic(self.root, self.opened.append)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_canvas_click_source_search_zoom_and_cleanup(self):
        panel = self.panel
        box = panel.boxes['core']
        x1, y1, x2, y2 = panel.canvas.coords(box)
        x = int((x1 + x2) / 2 - panel.canvas.canvasx(0))
        y = int((y1 + y2) / 2 - panel.canvas.canvasy(0))
        panel.canvas.event_generate('<Motion>', x=x, y=y)
        self.assertEqual(panel.hover_component, 'core')
        self.assertIn('Partial Raspberry Pi', panel.hover_tip.text)
        panel.canvas.event_generate('<ButtonPress-1>', x=x, y=y)
        self.root.update()
        self.assertEqual(panel.selected, 'core')
        self.assertEqual(self.opened[-1].symbol, 'command')
        panel.search.set('trackball')
        self.assertEqual([c.id for c in panel.visible], ['keyboard'])
        panel.listing.selection_set(0)
        panel.list_select()
        self.assertEqual(self.opened[-1].symbol, 'KeyboardBridge')
        previous = panel.scale
        panel.zoom(1.2)
        self.assertAlmostEqual(panel.scale, previous * 1.2)
        panel.reset()
        self.assertTrue(panel.fitted)
        self.assertGreater(panel.scale, 0)
        self.assertLessEqual(panel.scale, 1.)
        panel.window.destroy()
        self.assertIsNone(panel.timer)

    def test_navigation_preserves_unsaved_edits_and_inspects_copy(self):
        import tkinter as tk
        from types import SimpleNamespace
        from unittest.mock import patch
        from uconsole_workbench import Workbench
        editor = tk.Text(self.root)
        editor.insert('1.0', 'my unsaved changes')
        editor.edit_modified(True)
        workbench = SimpleNamespace(root=self.root, editor=editor, filename='original',
                                    status=tk.StringVar())
        source = BY_ID['core'].sources[0]
        with patch('uconsole_workbench.messagebox.askyesno', return_value=False):
            Workbench.schematic_source(workbench, source)
        self.assertEqual(editor.get('1.0', 'end-1c'), 'my unsaved changes')
        self.assertEqual(workbench.filename, 'original')
        with patch('uconsole_workbench.messagebox.askyesno', return_value=True):
            Workbench.schematic_source(workbench, source)
        self.assertIsNone(workbench.filename)
        self.assertFalse(editor.edit_modified())
        self.assertIn('def command(', editor.get('insert linestart', 'insert lineend'))

    def test_blocked_sampler_does_not_block_tk_editing(self):
        import threading
        import time
        import tkinter as tk
        from types import SimpleNamespace
        from workbench_schematic_live import Collector
        started, release = threading.Event(), threading.Event()
        def sample(runtime):
            started.set()
            release.wait(3)
            return []
        self.panel.collector.close()
        collector = self.panel.collector = Collector(.001, SimpleNamespace(sample=sample))
        runtime = SimpleNamespace(identity='blocked-sampler-test', process=SimpleNamespace(poll=lambda: None))
        self.panel.runtime = lambda: runtime
        collector.bind(runtime)
        try:
            self.assertTrue(started.wait(1))
            editor = tk.Text(self.root)
            self.root.after_idle(lambda: editor.insert('1.0', 'responsive edit'))
            before = time.monotonic()
            self.root.update()
            self.assertEqual(editor.get('1.0', 'end-1c'), 'responsive edit')
            self.assertLess(time.monotonic() - before, 1.)
        finally:
            collector.close()
            release.set()
            collector.worker.join(1)

    def test_recording_drop_count_excludes_earlier_samples_and_is_not_added_twice(self):
        panel = self.panel
        panel.observations.bind('owned-test')
        panel.collector.dropped = 100
        panel.record()
        panel.recording.data['dropped'] = 1  # One overflow, separate from collector coalescing.
        panel.collector.dropped = 103
        panel.stop_record()
        self.assertEqual(panel.recording.data['dropped'], 4)
        panel.stop_record()
        self.assertEqual(panel.recording.data['dropped'], 4)


if __name__ == '__main__':
    unittest.main()
