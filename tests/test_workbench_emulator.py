import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import workbench_emulator as managed


class ManagedTests(unittest.TestCase):
    def test_matching_user_build_precedes_bundle_and_stale_build_does_not(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(managed, 'fingerprint', return_value='expected'):
            root = Path(temporary) / 'app'
            builds = Path(temporary) / 'data'
            user = builds / 'emulator/qemu-build'
            packaged = root / 'emulator/bin'
            for directory in (user, packaged):
                directory.mkdir(parents=True)
                for name in ('qemu-system-aarch64', 'qemu-img'):
                    (directory / name).touch()
                managed.record(directory, root)
            self.assertEqual(managed.selected(root, builds), user)
            record = json.loads((user / 'workbench-emulator.json').read_text())
            record['fingerprint'] = 'old'
            (user / 'workbench-emulator.json').write_text(json.dumps(record))
            self.assertEqual(managed.selected(root, builds), packaged)
            (packaged / 'qemu-img').unlink()
            self.assertIsNone(managed.selected(root, builds))

    def test_malformed_manifest_is_not_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for content in ('null', '[]', '{}', 'not json'):
                (directory / 'workbench-emulator.json').write_text(content)
                self.assertFalse(managed.compatible(directory, directory))

    def test_fingerprint_tracks_patch_and_builder_content(self):
        import build_emulator_qemu as builder
        with tempfile.TemporaryDirectory() as temporary, patch.multiple(builder, PATCHES=['a.patch'], MODEL_SOURCES=[]):
            root = Path(temporary)
            (root / 'tools').mkdir()
            (root / 'Code/patch/qemu').mkdir(parents=True)
            (root / 'tools/build_emulator_qemu.py').write_text('builder')
            source = root / 'Code/patch/qemu/a.patch'
            source.write_text('first')
            first = managed.fingerprint(root)
            source.write_text('second')
            self.assertNotEqual(first, managed.fingerprint(root))


class SetupTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))

    def tearDown(self):
        if hasattr(self, 'root'):
            self.root.destroy()

    def test_setup_reports_guest_and_preserves_existing_workspace(self):
        from types import SimpleNamespace
        from workbench_setup import Setup
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            app = SimpleNamespace(root=self.root, workspace=workspace)
            source = Path(__file__).resolve().parents[1]
            panel = Setup(app, source, workspace)
            self.assertIn('import required', panel.status.get())
            (workspace / 'machine.json').write_text('{}')
            panel.refresh()
            self.assertIn('Guest: prepared', panel.status.get())
            panel.close()
            self.assertEqual((workspace / 'machine.json').read_text(), '{}')

    def test_build_logs_failure_and_cancellation_remain_responsive(self):
        import subprocess
        import time
        from types import SimpleNamespace
        from unittest.mock import Mock
        from workbench_setup import Setup
        popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            app = SimpleNamespace(root=self.root, workspace=workspace, report_error=Mock())
            panel = Setup(app, Path(__file__).resolve().parents[1], workspace)
            def launch(code):
                return lambda command, **kwargs: popen([sys.executable, '-u', '-c', code], **kwargs)
            def finish():
                deadline = time.monotonic() + 10
                while panel.process is not None and time.monotonic() < deadline:
                    self.root.after(10, self.root.quit)
                    self.root.mainloop()
                self.assertIsNone(panel.process)
            with patch('workbench_setup.subprocess.Popen', side_effect=launch('print("failure proof"); raise SystemExit(2)')):
                panel.build()
                finish()
            self.assertIn('failure proof', panel.path.read_text())
            app.report_error.assert_called_once()
            with patch('workbench_setup.subprocess.Popen', side_effect=launch('import time; time.sleep(60)')):
                panel.build()
                process = panel.process
                panel.cancel()
                finish()
            self.assertIsNotNone(process.returncode)
            self.assertTrue(panel.path.is_file())
            app.report_error.assert_called_once()  # Cancel is not an application error.
            panel.close()
