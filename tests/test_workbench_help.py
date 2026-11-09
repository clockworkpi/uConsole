"""Offline help and durable, copyable error regression coverage."""
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from workbench_diagnostics import Diagnostics, report_job_error
try:
    from workbench_help import Guide, Tooltip, TOPICS
except ImportError:
    Guide = None


class DiagnosticsTests(unittest.TestCase):
    def test_parseable_private_log_with_context_and_escaped_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'logs/application.jsonl'
            logger = Diagnostics('/workspace', path)
            with patch('sys.stderr'):
                details = logger.emit('action_failed', 'start', ValueError('missing\nmachine.json'))
            records = path.read_text().splitlines()
            self.assertEqual(len(records), 1)
            record = json.loads(records[0])
            self.assertEqual(record['level'], 'ERROR')
            self.assertEqual(record['action'], 'start')
            self.assertEqual(record['workspace'], '/workspace')
            self.assertEqual(record['message'], 'missing\nmachine.json')
            self.assertIn(str(path), details)
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == 'posix', 'POSIX private-file checks')
    def test_symlink_refused_without_overwriting_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            target.write_text('preserve')
            link = Path(directory) / 'application.jsonl'
            link.symlink_to(target)
            with patch('sys.stderr'):
                details = Diagnostics('/workspace', link).emit('action_failed', 'start', ValueError('test'))
            self.assertIn('Could not persist', details)
            self.assertEqual(target.read_text(), 'preserve')

    def test_failed_command_logged_once_without_output_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = Diagnostics('/workspace', Path(directory) / 'log.jsonl')
            class Root:
                _workbench_diagnostics = logger
            result = {'status': 'completed', 'job_id': 'one',
                      'result': {'exit_code': 1, 'stdout': 'private output'}}
            with patch('sys.stderr'):
                report_job_error(Root(), result)
                report_job_error(Root(), result)
            text = logger.recent()
            self.assertEqual(len(text.splitlines()), 1)
            self.assertNotIn('private output', text)


@unittest.skipUnless(Guide and (os.environ.get('DISPLAY') or sys.platform in ('darwin', 'win32')), 'needs Tk desktop')
class HelpTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.logs = tempfile.TemporaryDirectory()
        log_path = patch('workbench_diagnostics.default_path',
                         return_value=Path(self.logs.name) / 'application.jsonl')
        log_path.start()
        self.addCleanup(log_path.stop)
        self.addCleanup(self.logs.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()
        self.root = None
        gc.collect()

    def test_search_matches_contents_and_empty_results_are_clear(self):
        guide = Guide(self.root)
        guide.query.set('machine.json')
        self.assertIn('Getting started', guide.matches)
        self.assertIn('Errors and diagnostics', guide.matches)
        self.assertTrue(guide.body.tag_ranges('match'))
        guide.query.set('no such topic 12345')
        self.assertEqual(guide.matches, [])
        self.assertIn('No matching topics', guide.body.get('1.0', 'end'))
        guide.query.set('')
        self.assertEqual(len(guide.matches), len(TOPICS))

    def test_setup_command_is_searchable_and_copyable(self):
        guide = Guide(self.root, 'QEMU setup', setup_command='UCONSOLE_BUILD_DIR=/private/data python3 builder.py')
        guide.query.set('/private/data')
        self.assertEqual(guide.matches, ['QEMU setup'])
        guide.copy_topic()
        self.assertIn('UCONSOLE_BUILD_DIR=/private/data', self.root.clipboard_get())

    def test_toolbar_controls_fit_default_and_narrow_windows(self):
        from uconsole_workbench import Workbench
        from workbench_layout import WrappingToolbar
        with tempfile.TemporaryDirectory() as directory:
            app = Workbench(self.root, Path(directory) / 'new-workspace')
            self.root.deiconify()
            for width, height in ((1100, 760), (800, 650), (1500, 900), (1100, 760)):
                self.root.geometry(f'{width}x{height}')
                self.root.update()
                self.assertTrue(app.entry.winfo_viewable())
                self.assertTrue(app.editor.winfo_viewable())
                self.assertTrue(app.console.winfo_viewable())
                bars = [child for child in self.root.winfo_children() if isinstance(child, WrappingToolbar)]
                self.assertEqual(len(bars), 7)
                for bar in bars:
                    for child in bar.winfo_children():
                        self.assertGreater(child.winfo_width(), 1)
                        self.assertTrue(child.winfo_viewable())
                        if child not in bar.rows:
                            hit = self.root.winfo_containing(child.winfo_rootx() + child.winfo_width() // 2,
                                                            child.winfo_rooty() + child.winfo_height() // 2)
                            self.assertIs(hit, child, 'Control is covered by another widget')
                        self.assertLessEqual(child.winfo_rootx() - bar.winfo_rootx() + child.winfo_width(), bar.winfo_width(), str(child))
                        self.assertLessEqual(child.winfo_rooty() - bar.winfo_rooty() + child.winfo_height(), bar.winfo_height(), str(child))
            self.assertIsNone(app.process)

    def test_tooltip_cancel_and_destroy(self):
        from tkinter import ttk
        button = ttk.Button(self.root, text='Test')
        tooltip = Tooltip(button, 'Helpful text')
        tooltip.schedule()
        self.assertIsNotNone(tooltip.timer)
        tooltip.hide()
        self.assertIsNone(tooltip.timer)
        tooltip.show()
        self.assertIsNotNone(tooltip.window)
        button.destroy()
        self.assertIsNone(tooltip.window)

    def test_missing_workspace_opens_setup_instead_of_error(self):
        from uconsole_workbench import Workbench
        with tempfile.TemporaryDirectory() as directory:
            app = Workbench(self.root, Path(directory) / 'new-workspace')
            app.diagnostics.path = Path(directory) / 'logs/application.jsonl'
            app.show_setup()
            app.setup_panel.custom_image.set(True)
            with patch('uconsole_workbench.details_window') as details, patch('sys.stderr'), \
                    patch('workbench_setup.selected', return_value=Path(directory) / 'mock-qemu'), \
                    patch('uconsole_workbench.filedialog.askopenfilename', return_value=''), \
                    patch('workbench_setup.Setup.check_packages', lambda panel, **kwargs:
                          (setattr(panel, 'dependencies_checked', True), panel.advance())), \
                    patch('workbench_setup.Setup.probe', lambda panel, directory:
                          (setattr(panel, 'emulator_checked', True), panel.advance())):
                app.guard(app.start)
            details.assert_not_called()
            self.assertIn('cancelled', app.setup_panel.status.get())
            app.show_help('Images and checkpoints')
            self.assertIn('Import', app.guide.body.get('1.0', 'end'))
            self.assertIn('No guest image prepared', app.status.get())

    def test_import_submits_verified_image_without_blocking_tk(self):
        from uconsole_workbench import Workbench
        with tempfile.TemporaryDirectory() as directory:
            app = Workbench(self.root, Path(directory) / 'new-workspace')
            with patch('uconsole_workbench.filedialog.askopenfilename', return_value='/input.img'), \
                 patch('uconsole_workbench.simpledialog.askstring', return_value='a' * 64), \
                 patch.object(app, 'start_lifecycle') as submit:
                app.import_image()
            submit.assert_called_once_with(['prepare', '/input.img', '--sha256', 'a' * 64], 'Import')

    def test_error_details_can_be_copied_without_screenshots(self):
        from workbench_diagnostics import details_window
        text = 'Complete error details → machine.json\n' + 'More diagnostic context\n' * 100
        window = details_window(self.root, 'Error', text, lambda: None)
        pending = [window]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if 'text' in widget.keys() and widget['text'] == 'Copy text':
                for geometry in ('800x420', '600x260'):
                    window.geometry(geometry)
                    self.root.update()
                    self.assertTrue(widget.winfo_viewable())
                    self.assertLessEqual(widget.winfo_rooty() - window.winfo_rooty() + widget.winfo_height(), window.winfo_height())
                    self.assertIs(self.root.winfo_containing(widget.winfo_rootx() + widget.winfo_width() // 2,
                                                            widget.winfo_rooty() + widget.winfo_height() // 2), widget)
                widget.invoke()
                break
        else:
            self.fail('Missing copy action')
        self.assertEqual(self.root.clipboard_get(), text)

    def test_f1_on_start_opens_contextual_topic(self):
        from uconsole_workbench import Workbench
        with tempfile.TemporaryDirectory() as directory:
            app = Workbench(self.root, Path(directory) / 'new-workspace')
            pending = [self.root]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if 'text' in widget.keys() and widget['text'] == 'Start':
                    # Invoke the registered binding without depending on WM focus.
                    binding = widget.bind('<F1>')
                    self.assertTrue(binding)
                    command = binding.split('[', 1)[1].split()[0]
                    self.root.tk.call(command, 'synthetic-event')
                    break
            else:
                self.fail('Missing Start control')
            self.assertIn('Boot and display', app.guide.body.get('1.0', 'end'))
