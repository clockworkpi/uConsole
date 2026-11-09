"""Public Start sequencing, cancellation and exactly-once continuation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


class StartupTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from workbench_setup import Setup
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / 'guest'
        self.jobs = {}
        self.app = SimpleNamespace(root=self.root, workspace=self.workspace,
            lifecycle=None, boot_job=None, start_after_setup=False, process=None,
            report_error=Mock(), controller=SimpleNamespace(job=lambda job: self.jobs[job], cancel=Mock()))
        for name in ('mode', 'display', 'keyboard', 'audio', 'adc_reference', 'modem'):
            setattr(self.app, name, tk.StringVar(value='desktop' if name == 'mode' else 'fixture'))
        self.app.import_image = Mock(side_effect=lambda: self.submit('import', 'lifecycle'))
        self.app._start_prepared = Mock(side_effect=self.launch)
        self.selection = patch('workbench_setup.selected', return_value=Path('/bundled/emulator'))
        self.selection.start()
        self.panel = Setup(self.app, Path(__file__).resolve().parents[1], Path(self.temp.name))
        self.panel.custom_image.set(True)
        def dependencies(**kwargs):
            self.panel.dependencies_checked = True
            self.panel.advance()
        self.panel.check_packages = Mock(side_effect=dependencies)
        def checked(directory):
            self.panel.emulator_checked = True
            self.panel.advance()
        self.panel.probe = Mock(side_effect=checked)
        self.desktop_ready = False

    def tearDown(self):
        if hasattr(self, 'panel'):
            self.panel.window.destroy()
            self.selection.stop()
            self.temp.cleanup()
        if hasattr(self, 'root'):
            self.root.destroy()

    def submit(self, job, field):
        self.jobs[job] = dict(status='running', job_id=job)
        setattr(self.app, field, job)

    def launch(self):
        if not self.desktop_ready:
            self.submit('desktop', 'lifecycle')
        else:
            self.submit('boot', 'boot_job')

    def complete(self, job, status='completed'):
        if self.panel.flow_timer:
            self.panel.window.after_cancel(self.panel.flow_timer)
            self.panel.flow_timer = None
        self.jobs[job]['status'] = status
        if job == 'boot':
            self.app.boot_job = None
        else:
            self.app.lifecycle = None
        if status == 'completed':
            if job == 'import':
                self.workspace.mkdir()
                (self.workspace / 'machine.json').write_text(json.dumps({'schema': 1}))
            if job == 'desktop':
                self.desktop_ready = True
        self.panel.poll_flow()

    def test_one_start_imports_prepares_and_boots_exactly_once(self):
        self.panel.begin()
        self.assertEqual(self.panel.flow_stage, 'image')
        self.panel.begin()  # Duplicate Start must not submit another import.
        self.app.import_image.assert_called_once()
        self.complete('import')
        self.assertEqual(self.panel.flow_stage, 'desktop')
        self.complete('desktop')
        self.assertEqual(self.panel.flow_stage, 'boot')
        self.complete('boot')
        self.assertFalse(self.panel.flow_active)
        self.assertIn('Emulator started', self.panel.status.get())
        self.assertEqual(self.app._start_prepared.call_count, 2)
        self.assertIn('Preparing the guest display', self.panel.flow_path.read_text())
        self.assertEqual(str(self.panel.close_button['state']), 'normal')

    def test_cancel_prevents_continuation_after_import_cleanup(self):
        self.panel.begin()
        self.panel.cancel_flow()
        self.app.controller.cancel.assert_called_once_with('import')
        self.assertTrue(self.panel.flow_active)
        self.complete('import', 'cancelled')
        self.app._start_prepared.assert_not_called()
        self.assertFalse(self.panel.flow_active)

    def test_failed_import_never_boots(self):
        self.panel.begin()
        self.complete('import', 'failed')
        self.app._start_prepared.assert_not_called()
        self.assertIn('failed', self.panel.status.get())

    def test_ready_workspace_skips_import_and_preparation(self):
        self.workspace.mkdir()
        (self.workspace / 'machine.json').write_text('{}')
        self.desktop_ready = True
        self.panel.begin()
        self.app.import_image.assert_not_called()
        self.assertEqual(self.panel.flow_stage, 'boot')
        self.complete('boot')
        self.app._start_prepared.assert_called_once()

    def test_missing_emulator_builds_before_import(self):
        with patch('workbench_setup.selected', return_value=None), patch.object(self.panel, 'build') as build:
            self.panel.begin()
            build.assert_called_once()
            self.app.import_image.assert_not_called()
            self.assertEqual(self.panel.flow_stage, 'emulator')
        self.panel.advance()  # A successful build resumes the same workflow.
        self.app.import_image.assert_called_once()

    def test_recommended_image_needs_no_file_picker_or_checksum_entry(self):
        from workbench_onboarding import IMAGE_NAME, IMAGE_SHA256
        self.panel.custom_image.set(False)
        self.app.start_lifecycle = Mock(side_effect=lambda *args: self.submit('import', 'lifecycle'))
        with patch('workbench_setup.messagebox.askyesno', return_value=True), \
                patch.object(self.panel, 'launch_process') as launch:
            self.panel.begin()
        self.assertEqual(self.panel.flow_stage, 'download')
        self.assertIn('download', launch.call_args.args[0])
        self.app.import_image.assert_not_called()
        self.panel.default_image_ready = True  # Only the verified downloader's success sets this.
        self.panel.advance()
        self.app.start_lifecycle.assert_called_once_with(
            ['prepare', str(self.panel.default_cache / IMAGE_NAME), '--sha256', IMAGE_SHA256],
            'Import recommended image')
        self.assertEqual(self.panel.flow_stage, 'image')

    def test_declining_default_download_never_imports_or_boots(self):
        self.panel.custom_image.set(False)
        with patch('workbench_setup.messagebox.askyesno', return_value=False), \
                patch.object(self.panel, 'launch_process') as launch:
            self.panel.begin()
        launch.assert_not_called()
        self.app.import_image.assert_not_called()
        self.app._start_prepared.assert_not_called()
        self.assertFalse(self.panel.flow_active)

    def test_package_install_requires_user_approval(self):
        self.panel.path = Path(self.temp.name) / 'plan.json'
        self.panel.path.write_text(json.dumps(dict(manager='apt', missing=['python3-tk'], privileged=True)))
        with patch('workbench_onboarding.install_command', return_value=['fixed-system-installer']), \
                patch.object(self.panel, 'launch_process') as launch, \
                patch('workbench_setup.messagebox.askyesno', return_value=False):
            self.panel.dependencies_result()
            launch.assert_not_called()
        with patch('workbench_onboarding.install_command', return_value=['fixed-system-installer']), \
                patch.object(self.panel, 'launch_process') as launch, \
                patch('workbench_setup.messagebox.askyesno', return_value=True):
            self.panel.dependencies_result()
            launch.assert_called_once_with(['fixed-system-installer'])
            self.assertEqual(self.panel.process_role, 'packages')

    def test_cancelling_startup_does_not_kill_package_manager(self):
        self.panel.process = Mock(pid=123)
        self.panel.process_role = 'packages'
        self.panel.flow_active = True
        with patch('workbench_setup.os.killpg') as kill:
            self.panel.cancel_flow()
        kill.assert_not_called()
        self.assertTrue(self.panel.flow_cancelled)
        self.assertTrue(self.panel.stop_after_packages)
