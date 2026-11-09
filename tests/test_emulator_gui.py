"""Desktop editor lifecycle checks; run under Xvfb on headless Linux."""
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipUnless(tk and (os.environ.get('DISPLAY') or sys.platform in ('darwin', 'win32')), 'needs Tk and a desktop display')
class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        from uconsole_workbench import Workbench
        self.temporary = tempfile.TemporaryDirectory()
        history = patch('uconsole_workbench.history_default_path',
                        return_value=Path(self.temporary.name) / 'private/jobs.sqlite3')
        history.start()
        self.addCleanup(history.stop)
        # Prepared-workspace lifecycle tests mock Runtime.start below. They must
        # not depend on a developer's cached QEMU existing on the test host.
        emulator = patch('workbench_emulator.selected', return_value=Path(self.temporary.name) / 'mock-qemu')
        emulator.start()
        self.addCleanup(emulator.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Workbench(self.root, Path(self.temporary.name))
        self.default_display = self.app.display.get()
        self.default_mode = self.app.mode.get()
        self.app.mode.set('maintenance')
        self.app.display.set('none')
        # These tests exercise the prepared-workspace lifecycle primitive.
        # The public Start coordinator has separate staged-workflow tests.
        self.app.start = self.app._start_prepared
        self.app.diagnostics.path = Path(self.temporary.name) / 'diagnostics/application.jsonl'

    def tearDown(self):
        self.app.close_attachment()
        if self.app.controller:
            self.app.controller.close()
        self.root.destroy()
        # Destroying widgets does not release Python callback/widget cycles.
        # Reclaim Tk on its creating thread before later executor tests can
        # trigger cyclic collection and delete the interpreter in a worker.
        self.app = self.root = None
        gc.collect()
        self.temporary.cleanup()

    def test_display_selector_defaults_to_native_desktop(self):
        pending = [self.root]
        matches = []
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() == 'TCombobox' and str(widget['textvariable']) == str(self.app.display):
                matches.append(widget)
        self.assertEqual(len(matches), 1)
        self.assertIn('cocoa', matches[0]['values'])
        self.assertEqual(self.default_display, 'cocoa' if sys.platform == 'darwin' else 'gtk')
        self.assertEqual(self.default_mode, 'desktop')

    def test_editor_saves_exact_content_and_tracks_unsaved_edits(self):
        path = Path(self.temporary.name) / 'example.txt'
        self.app.filename = path
        self.app.editor.delete('1.0', 'end')
        self.app.editor.insert('1.0', 'uConsole\nsecond line\n')
        self.assertTrue(self.app.editor.edit_modified())
        self.app.save_file()
        self.assertEqual(path.read_text(), 'uConsole\nsecond line\n')
        self.assertFalse(self.app.editor.edit_modified())

    def test_target_panel_is_singleton_and_unconfigured_writes_disabled(self):
        panel = self.app.physical_target()
        self.assertIs(self.app.physical_target(), panel)
        self.assertTrue(all('disabled' in button.state() for button in panel.buttons))
        self.assertIn('Save target', panel.details.get('1.0', 'end'))
        panel.job = 'physical-job'
        with patch('uconsole_workbench.messagebox.showinfo') as notice:
            self.app.close()
        notice.assert_called_once()
        self.assertTrue(self.root.winfo_exists())
        panel.job = None
        panel.close()

    def test_recovery_panel_is_singleton_and_blocks_workbench_close_during_job(self):
        panel = self.app.recovery_controls()
        self.assertIs(self.app.recovery_controls(), panel)
        self.assertIn('disabled', panel.run_button.state())
        panel.job = 'accepted-recovery-job'
        with patch('uconsole_workbench.messagebox.showinfo') as notice:
            self.app.close()
        notice.assert_called_once()
        self.assertTrue(self.root.winfo_exists())
        self.assertFalse(panel.close())
        panel.job = None
        self.assertTrue(panel.close())

    def test_attached_guest_job_releases_serial_and_guards_gui(self):
        from forge_client import ClientSession
        entered, finish = threading.Event(), threading.Event()
        runtime = self.app.runtime = MagicMock()
        runtime.guest_channel_error = None
        runtime.process.poll.return_value = None
        self.app.process = runtime.process
        self.app.active_mode = 'maintenance'
        serial = self.app.serial = MagicMock()
        owner = self.app.job_controller()
        owner.runtimes['gui'] = runtime
        def execute(*args, **kwargs):
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('attached guest fixture')
            return {'exit_code': 0, 'stdout': 'agent result', 'stderr': ''}
        runtime.execute.side_effect = execute
        client = ClientSession(owner, ['gui'], ['guest-exec'], dispatch=self.app.agent_operation)
        submitted = client.call('guest_exec', {'workspace': 'gui', 'script': 'true'})
        try:
            self.assertTrue(entered.wait(2))
            serial.close.assert_called_once()
            self.assertIsNone(self.app.serial)
            for operation in (self.app.send, self.app.start, self.app.poweroff,
                              self.app.live_power, self.app.refresh_image):
                with self.assertRaisesRegex(ValueError, 'attached agent'):
                    operation()
            with patch('uconsole_workbench.messagebox.showinfo') as dialog:
                self.app.close()
                dialog.assert_called_once()
            client.close()
            self.app.poll_agent()
            self.assertEqual(self.app.agent_job, submitted['job_id'])
        finally:
            finish.set()
            owner.jobs[submitted['job_id']][2].result(timeout=5)
        self.app.poll_agent()
        self.assertIsNone(self.app.agent_job)
        self.assertIs(self.app.runtime, runtime)
        self.assertIn('agent result', self.app.console.get('1.0', 'end'))
        runtime.process.poll.return_value = 0
        self.app.release()

    def test_denied_attachment_does_not_touch_gui_serial(self):
        from forge_client import ClientSession
        serial = self.app.serial = MagicMock()
        client = ClientSession(self.app.job_controller(), ['gui'], dispatch=self.app.agent_operation)
        with self.assertRaises(PermissionError):
            client.call('guest_exec', {'workspace': 'gui', 'script': 'true'})
        serial.close.assert_not_called()
        self.assertIsNone(self.app.agent_job)

    def test_recovery_attachment_tracks_job_and_readonly_listing_during_other_work(self):
        from forge_client import ClientSession
        from types import SimpleNamespace
        owner = self.app.job_controller()
        approved = SimpleNamespace(operation='reconcile-root', machine_id='c'*32,
                                   name='proof', session_pin='b'*64)
        registry = MagicMock(sha256='a'*64)
        registry.get.return_value = approved
        registry.describe.return_value = [{'name': 'proof'}]
        with patch.object(owner, 'load_recovery_policy', return_value=registry):
            owner.approve_recovery_policy('/owner/policy', 'a'*64)
        readonly = ClientSession(owner, ['gui'], dispatch=self.app.agent_operation)
        self.app.lifecycle = 'unrelated-job'
        self.assertFalse(readonly.call('recovery_jobs', {'workspace': 'gui'})['execution_granted'])
        self.app.lifecycle = None
        entered, finish = threading.Event(), threading.Event()
        def execute(job):
            entered.set()
            if not finish.wait(5): raise TimeoutError('recovery fixture')
            return {'root_written': False}
        client = ClientSession(owner, ['gui'], ['target-recovery'], dispatch=self.app.agent_operation)
        with patch('forge_recovery_jobs.execute', side_effect=execute):
            submitted = client.call('recovery_job', {'workspace': 'gui', 'job': 'proof'})
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.app.agent_job, submitted['job_id'])
                with patch('uconsole_workbench.messagebox.showinfo') as dialog:
                    self.app.close()
                    dialog.assert_called_once()
                client.close()
                self.assertFalse(owner.jobs[submitted['job_id']][2].done())
            finally:
                finish.set()
                owner.jobs[submitted['job_id']][2].result(timeout=5)
        self.app.poll_agent()
        self.assertIsNone(self.app.agent_job)
        self.assertIn('recovery_reconcile-root completed', self.app.status.get())

    def test_agent_boot_completion_exposes_exact_owner_runtime(self):
        owner = self.app.job_controller()
        runtime = MagicMock()
        runtime.args.mode = 'maintenance'
        runtime.process.poll.return_value = None
        owner.runtimes['gui'] = runtime
        submitted = owner.submit('gui', 'boot', lambda: {'identity': 'fixture'})
        self.app.agent_job = submitted['job_id']
        owner.jobs[submitted['job_id']][2].result(timeout=5)
        self.app.poll_agent()
        self.assertIs(self.app.runtime, runtime)
        self.assertIs(self.app.process, runtime.process)
        self.assertEqual(self.app.active_mode, 'maintenance')
        self.assertIsNone(self.app.agent_job)
        runtime.process.poll.return_value = 0
        self.app.release()

    def test_editor_opens_keyboard_firmware_source_on_startup(self):
        from uconsole_workbench import ROOT, BUILD_ROOT
        project = (ROOT / 'Code' if (ROOT / '.git').exists() else BUILD_ROOT / 'projects')
        expected = project.resolve() / 'uconsole_keyboard/uconsole_keyboard.ino'
        self.assertEqual(self.app.filename, expected)
        self.assertEqual(self.app.editor.get('1.0', 'end-1c'), expected.read_text())
        self.assertFalse(self.app.editor.edit_modified())

    def test_installed_source_opens_as_user_project_and_saves_without_changing_bundle(self):
        from uconsole_workbench import Workbench
        installed = Path(self.temporary.name) / 'installed'
        source = installed / 'Code/uconsole_keyboard/uconsole_keyboard.ino'
        source.parent.mkdir(parents=True)
        source.write_text('bundled firmware\n')
        data = Path(self.temporary.name) / 'user-data'
        window = tk.Toplevel(self.root)
        with patch('uconsole_workbench.ROOT', installed), patch('uconsole_workbench.BUILD_ROOT', data):
            app = Workbench(window, Path(self.temporary.name) / 'guest')
        self.assertEqual(app.filename, data.resolve() / 'projects/uconsole_keyboard/uconsole_keyboard.ino')
        app.editor.delete('1.0', 'end')
        app.editor.insert('1.0', 'user modifications\n')
        app.save_file()
        self.assertEqual(app.filename.read_text(), 'user modifications\n')
        self.assertEqual(source.read_text(), 'bundled firmware\n')

    def test_stopped_controls_do_not_connect_to_unowned_vm(self):
        with patch('uconsole_workbench.qmp') as monitor:
            with self.assertRaises(ValueError):
                self.app.control('stop')
            monitor.assert_not_called()

    def test_pause_job_is_async_and_holds_guards_until_readback(self):
        entered, finish = threading.Event(), threading.Event()
        ui_thread = threading.get_ident()
        runtime = self.app.runtime = MagicMock()
        self.app.process = runtime.process
        self.app.process.poll.return_value = None

        def runstate(name, running):
            self.assertNotEqual(threading.get_ident(), ui_thread)
            self.assertEqual((name, running), ('gui', False))
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('pause readback fixture')
            return {'observed': {'status': 'paused', 'running': False}}

        with patch('forge_controller.Controller.runstate', side_effect=runstate):
            job = self.app.control('stop')
            try:
                self.assertTrue(entered.wait(2))
                self.app.poll_control()
                self.assertEqual(self.app.control_job, job['job_id'])
                for action in (self.app.send, self.app.live_power, self.app.poweroff,
                               self.app.start_replay, lambda: self.app.control('cont')):
                    with self.assertRaisesRegex(ValueError, 'readback'):
                        action()
                with patch('uconsole_workbench.messagebox.showinfo') as dialog:
                    self.app.close()
                    dialog.assert_called_once()
            finally:
                finish.set()
            self.app.controller.jobs[job['job_id']][2].result(timeout=5)
        self.app.poll_control()
        self.assertIsNone(self.app.control_job)
        self.assertEqual(self.app.status.get(), 'Paused')
        record = self.app.controller.history.get(job['job_id'], [self.app.workspace])
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['context'], {'requested_running': False})
        self.app.process.poll.return_value = 0
        self.app.release()

    def test_power_profile_snapshot_validation_cancel_and_clear(self):
        profile = Path(self.temporary.name) / 'power.json'
        profile.write_text('{"schema":1,"power":{"ac_present":false}}')
        with patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(profile)):
            self.app.load_scenario()
        snapshot = self.app.initial_scenario
        self.assertFalse(snapshot.power.ac_present)
        self.assertIn(snapshot.sha256[:12], self.app.scenario_label.get())
        profile.write_text('{}')
        with patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(profile)):
            with self.assertRaises(ValueError):
                self.app.load_scenario()
        self.assertIs(self.app.initial_scenario, snapshot)
        with patch('uconsole_workbench.filedialog.askopenfilename', return_value=''):
            self.app.load_scenario()
        self.assertIs(self.app.initial_scenario, snapshot)
        self.app.clear_scenario()
        self.assertIsNone(self.app.initial_scenario)
        self.assertIn('default', self.app.scenario_label.get())

    def test_power_profile_cannot_change_while_busy(self):
        for field in ('process', 'display_setup', 'lifecycle'):
            setattr(self.app, field, object())
            try:
                with self.assertRaisesRegex(ValueError, 'Stop the emulator'):
                    self.app.clear_scenario()
                with patch('uconsole_workbench.filedialog.askopenfilename') as choose:
                    with self.assertRaises(ValueError):
                        self.app.load_scenario()
                    choose.assert_not_called()
            finally:
                setattr(self.app, field, None)

    def test_live_power_requires_owner_and_valid_value_and_retains_evidence(self):
        with self.assertRaises(ValueError):
            self.app.live_power()
        self.app.process = MagicMock()
        self.app.process.poll.return_value = None
        self.app.runtime = MagicMock(identity='fixture')
        try:
            self.app.power_field.set('battery_capacity')
            self.app.power_value.set('true')
            with patch('forge_controller.change_power') as change:
                with self.assertRaises(ValueError):
                    self.app.live_power(change=True)
                change.assert_not_called()
                self.assertEqual(list(self.app.workspace.glob('power-event-*')), [])
            self.app.power_value.set('25')
            with patch('forge_controller.change_power', return_value={'observed': {'battery_capacity': 25}}) as change:
                job = self.app.live_power(change=True)
                result = self.app.controller.jobs[job['job_id']][2].result(timeout=5)
                self.app.poll_power()
                self.assertEqual(change.call_args.args, (self.app.runtime.control, 'battery_capacity', 25))
                self.assertTrue(callable(change.call_args.kwargs['record']))
            self.assertEqual(result['observed']['battery_capacity'], 25)
            record = next(self.app.workspace.glob('power-event-*')).read_text()
            self.assertIn('"requested": {"battery_capacity": 25}', record)
            self.assertIn('"status": "completed"', record)
            with patch('forge_controller.query_power', side_effect=ValueError('readback failed')):
                job = self.app.live_power()
                with self.assertRaises(ValueError):
                    self.app.controller.jobs[job['job_id']][2].result(timeout=5)
                self.app.poll_power()
            self.assertTrue(any('"status": "failed"' in path.read_text()
                                for path in self.app.workspace.glob('power-event-*')))
        finally:
            self.app.process = self.app.runtime = None

    def test_adc_controls_use_shared_jobs_and_retain_evidence(self):
        self.app.runtime = MagicMock(identity='adc-fixture')
        self.app.process = self.app.runtime.process
        self.app.process.poll.return_value = None
        try:
            for field, value in (('adc_input_uv', 1650000), ('adc_powered', False)):
                self.app.power_field.set(field)
                self.app.power_value.set(json.dumps(value))
                with patch('forge_controller.change_power',
                           return_value={'observed': {field: value}}) as change:
                    job = self.app.live_power(change=True)
                    self.app.controller.jobs[job['job_id']][2].result(timeout=5)
                    self.app.poll_power()
                    self.assertEqual(change.call_args.args, (self.app.runtime.control, field, value))
                    self.assertTrue(callable(change.call_args.kwargs['record']))
                self.assertTrue(any(json.dumps({'requested': {field: value}})[1:-1] in path.read_text()
                                    for path in self.app.workspace.glob('power-event-*')))
        finally:
            self.app.process = self.app.runtime = None

    def test_power_job_runs_off_ui_thread_and_retains_guards_through_readback(self):
        entered, finish = threading.Event(), threading.Event()
        ui_thread = threading.get_ident()
        self.app.runtime = MagicMock(identity='power-fixture')
        self.app.process = self.app.runtime.process
        self.app.process.poll.return_value = None

        def query(control):
            self.assertNotEqual(threading.get_ident(), ui_thread)
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('power readback fixture')
            return {'power': {'ac_present': True}}

        with patch('forge_controller.query_power', side_effect=query):
            job = self.app.live_power()
            try:
                self.assertTrue(entered.wait(2))
                self.app.poll_power()
                self.assertEqual(self.app.power_job, job['job_id'])
                for action in (self.app.send, self.app.live_power, self.app.poweroff,
                               self.app.start_replay, lambda: self.app.control('stop')):
                    with self.assertRaisesRegex(ValueError, 'power operation'):
                        action()
                self.assertFalse(self.app.controller.cancel(job['job_id'])['requested'])
                with patch('uconsole_workbench.messagebox.showinfo') as dialog:
                    self.app.close()
                    dialog.assert_called_once()
            finally:
                finish.set()
            self.app.controller.jobs[job['job_id']][2].result(timeout=5)
        self.app.poll_power()
        record = self.app.controller.history.get(job['job_id'], [self.app.workspace])
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['operation'], 'power_query')
        self.assertTrue(Path(record['context']['evidence_path']).exists())
        self.app.process.poll.return_value = 0
        self.app.release()

    def test_replay_worker_snapshot_cancel_and_conflict_guards(self):
        from forge_replay import ReplayCancelled
        schedule = self.app.workspace / 'events.json'
        schedule.write_text('{"schema":1,"events":[{"at_ms":0,"power":{"ac_present":false}}]}')
        entered = threading.Event()
        main_thread = threading.get_ident()
        self.app.process = MagicMock()
        self.app.process.poll.return_value = None
        self.app.runtime = MagicMock(identity='fixture')
        def worker(replay, runtime, cancel, evidence):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertFalse(replay.events[0].value)
            entered.set()
            if not cancel.wait(5):
                raise TimeoutError('fixture cancellation')
            raise ReplayCancelled('fixture')
        try:
            with patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(schedule)), \
                 patch('forge_controller.run_recorded', side_effect=worker):
                self.app.start_replay()
                job_id = self.app.replay_job
                self.assertTrue(entered.wait(5))
                schedule.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'Another controller job'):
                    self.app.controller.call('power_query', {'workspace': 'gui'})
                for action in (self.app.start_replay, self.app.live_power, self.app.poweroff,
                               lambda: self.app.control('stop')):
                    with self.assertRaisesRegex(ValueError, 'power replay'):
                        action()
                with patch('uconsole_workbench.messagebox.showinfo') as message:
                    self.app.close()
                    message.assert_called_once()
                self.app.cancel_replay()
                with self.assertRaises(ReplayCancelled):
                    self.app.replay_future.result(timeout=5)
                self.app.poll_replay()
                self.assertIsNone(self.app.replay_future)
                self.assertIn('cancelled', self.app.replay_status.get())
                record = self.app.controller.history.get(job_id, [self.app.workspace])
                self.assertEqual(record['operation'], 'power_replay')
                self.assertEqual(record['status'], 'cancelled')
                self.assertEqual(record['context']['event_count'], 1)
                self.assertEqual(record['context']['clock'], 'host-monotonic')
        finally:
            if self.app.replay_future:
                self.app.cancel_replay()
                try:
                    self.app.replay_future.result(timeout=5)
                except ReplayCancelled:
                    pass
                self.app.poll_replay()
            self.app.process = self.app.runtime = None

    def test_gui_launch_passes_snapshot_and_displays_verified_evidence(self):
        from forge_scenario import Scenario
        from uconsole_emulator import parser
        profile = Path(self.temporary.name) / 'power.json'
        profile.write_text('{"schema":1,"power":{}}')
        self.app.initial_scenario = Scenario.load(profile)
        with patch('forge_controller.Controller.boot', return_value={'scenario': {'observed': {}}}) as boot:
            self.app.launch_locked(parser().parse_args(['run', '--mode', 'maintenance',
                                                        '--qmp-port', '4444', '--serial-port', '4445']))
            self.app.controller.jobs[self.app.boot_job][2].result(timeout=5)
        boot.assert_called_once_with('gui', 'maintenance', self.app.initial_scenario,
                                     display='none', qmp_port=4444, serial_port=4445,
                                     keyboard='generic', audio='none', adc_reference='fixed', modem='none')
        self.app.poll_boot()
        self.assertIn('Verified initial power profile', self.app.console.get('1.0', 'end'))
        self.assertIn('observed', self.app.console.get('1.0', 'end'))
        self.app.process = self.app.runtime = None

    def test_gui_audio_selection_reaches_shared_boot_job(self):
        from uconsole_emulator import parser
        with patch('forge_controller.Controller.boot', return_value={}) as boot:
            self.app.launch_locked(parser().parse_args(['run', '--mode', 'maintenance', '--audio', 'usb-null',
                                                       '--serial-port', '4445']))
            job_id = self.app.boot_job
            self.app.controller.jobs[job_id][2].result(timeout=5)
        self.assertEqual(boot.call_args.kwargs['audio'], 'usb-null')
        self.assertEqual(self.app.controller.job(job_id)['context']['audio'], 'usb-null')
        self.app.poll_boot()
        self.app.process = self.app.runtime = None

    def test_adc_reference_selection_is_snapshotted_for_shared_boot(self):
        (self.app.workspace / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        self.app.mode.set('maintenance')
        self.app.adc_reference.set('missing')
        with patch('forge_controller.Controller.boot', return_value={}) as boot:
            self.app.start()
            job_id = self.app.boot_job
            self.app.adc_reference.set('fixed')
            self.app.controller.jobs[job_id][2].result(timeout=5)
        self.assertEqual(boot.call_args.kwargs['adc_reference'], 'missing')
        self.assertEqual(self.app.controller.job(job_id)['context']['adc_reference'], 'missing')
        self.app.poll_boot()
        self.app.process = self.app.runtime = None

    def test_modem_selection_is_snapshotted_for_shared_boot(self):
        (self.app.workspace / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        self.app.mode.set('maintenance')
        self.app.modem.set('composite')
        with patch('forge_controller.Controller.boot', return_value={}) as boot:
            self.app.start()
            job_id = self.app.boot_job
            self.app.modem.set('none')
            self.app.controller.jobs[job_id][2].result(timeout=5)
        self.assertEqual(boot.call_args.kwargs['modem'], 'composite')
        self.assertEqual(self.app.controller.job(job_id)['context']['modem'], 'composite')
        self.app.poll_boot()
        self.app.process = self.app.runtime = None

    def test_modem_panel_reused_and_pending_job_blocks_workbench_close(self):
        panel = self.app.modem_controls()
        self.assertIs(self.app.modem_controls(), panel)
        panel.job = 'pending-fixture'
        try:
            self.app.close()
            self.assertTrue(self.root.winfo_exists())
            self.assertIn('modem operation', self.app.status.get())
        finally:
            panel.job = None
            panel.close()

    def test_failed_launch_releases_workspace_lock(self):
        from forge_workspace import WorkspaceLock
        (Path(self.temporary.name) / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        with patch.object(self.app, 'launch_locked', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                self.app.start()
        self.assertIsNone(self.app.runtime)
        with WorkspaceLock(self.app.workspace):
            pass

    def test_async_boot_cancel_keeps_guards_until_cleanup(self):
        from forge_controller import JobCancelled
        entered, cleanup, finish = threading.Event(), threading.Event(), threading.Event()
        (self.app.workspace / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')

        def boot(*args, **kwargs):
            entered.set()
            if not self.app.controller.worker_context.cancel.wait(5):
                raise TimeoutError('boot fixture')
            cleanup.set()
            if not finish.wait(5):
                raise TimeoutError('boot cleanup fixture')
            raise JobCancelled('owned VM exited; no rollback')

        with patch('forge_controller.Controller.boot', side_effect=boot):
            self.app.start()
            job_id = self.app.boot_job
            try:
                self.assertTrue(entered.wait(2))
                self.app.mode.set('desktop')
                self.app.cancel_boot()
                self.assertTrue(cleanup.wait(2))
                self.app.poll_boot()
                self.assertEqual(self.app.boot_job, job_id)
                for operation in (self.app.start, self.app.refresh_image, self.app.clear_scenario,
                                  self.app.send, self.app.poweroff):
                    with self.assertRaises(ValueError):
                        operation()
                with patch('uconsole_workbench.messagebox.showinfo') as dialog:
                    self.app.close()
                    dialog.assert_called_once()
            finally:
                finish.set()
            with self.assertRaises(JobCancelled):
                self.app.controller.jobs[job_id][2].result(timeout=5)
        self.app.poll_boot()
        self.assertIsNone(self.app.boot_job)
        self.assertEqual(self.app.active_mode, 'maintenance')
        record = self.app.controller.history.get(job_id, [self.app.workspace])
        self.assertEqual(record['status'], 'cancelled')
        self.assertEqual(record['context']['mode'], 'maintenance')

    def test_failed_boot_retains_controller_owned_runtime_in_gui(self):
        runtime = MagicMock()
        runtime.process.poll.return_value = None
        (self.app.workspace / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')

        def boot(*args, **kwargs):
            self.app.controller.runtimes['gui'] = runtime
            raise TimeoutError('VM cleanup failed')

        with patch('forge_controller.Controller.boot', side_effect=boot):
            self.app.start()
            with self.assertRaisesRegex(TimeoutError, 'cleanup failed'):
                self.app.controller.jobs[self.app.boot_job][2].result(timeout=5)
        self.app.poll_boot()
        self.assertIs(self.app.runtime, runtime)
        self.assertIs(self.app.process, runtime.process)
        runtime.release.assert_not_called()
        runtime.process.poll.return_value = 0
        self.app.release()

    def test_image_operation_is_tracked_and_blocks_conflicting_actions(self):
        entered, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        def worker(name, action, arguments):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertEqual((name, action, arguments), ('gui', 'checkpoint', ('before-upgrade',)))
            entered.set()
            if not release.wait(5):
                raise TimeoutError('fixture release')
            return {'exit_code': 0, 'stdout': 'checkpoint committed', 'stderr': ''}
        with patch('uconsole_workbench.Controller.lifecycle', side_effect=worker):
            self.app.start_lifecycle(['checkpoint', 'before-upgrade'], 'Checkpoint')
            job_id = self.app.lifecycle
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(ValueError, 'Wait for the image operation'):
                    self.app.start()
                with self.assertRaises(ValueError):
                    self.app.recover_image()
                with patch('uconsole_workbench.messagebox.showinfo') as message:
                    self.app.close()
                    message.assert_called_once()
            finally:
                release.set()
            self.app.controller.jobs[job_id][2].result(timeout=5)
        self.app.poll_lifecycle()
        self.assertIsNone(self.app.lifecycle)
        self.assertIn('Checkpoint complete', self.app.status.get())
        from forge_controller import Controller
        with_history = Controller({'agent': self.app.workspace}, history=self.app.lifecycle_path)
        try:
            record = with_history.job(job_id)
            self.assertTrue(record['historical'])
            self.assertEqual(record['status'], 'completed')
            self.assertEqual(record['context']['arguments'], ['before-upgrade'])
            with self.assertRaises(PermissionError):
                with_history.call('checkpoint', {'workspace': 'agent', 'name': 'forbidden'})
        finally:
            with_history.close()

    def test_failed_image_operation_reports_failure_and_releases_controls(self):
        with patch('uconsole_workbench.Controller.lifecycle', side_effect=ValueError('fixture image failure')):
            self.app.start_lifecycle(['refresh-boot'], 'Boot refresh')
            with self.assertRaises(ValueError):
                self.app.controller.jobs[self.app.lifecycle][2].result(timeout=5)
        self.app.poll_lifecycle()
        self.assertIn('Boot refresh failed', self.app.status.get())
        self.assertIn('fixture image failure', self.app.console.get('1.0', 'end'))
        self.assertIsNone(self.app.lifecycle)

    def test_image_cancellation_keeps_controls_locked_until_cleanup_finishes(self):
        from forge_controller import JobCancelled
        entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()
        def worker(*_):
            cancel = self.app.controller.worker_context.cancel
            entered.set()
            if not cancel.wait(5):
                raise TimeoutError('fixture cancel')
            cleanup.set()
            if not release.wait(5):
                raise TimeoutError('fixture cleanup')
            raise JobCancelled('fixture cleanup complete; changes not rolled back')
        with patch('uconsole_workbench.Controller.lifecycle', side_effect=worker):
            self.app.start_lifecycle(['checkpoint', 'cancel-me'], 'Checkpoint')
            job_id = self.app.lifecycle
            try:
                self.assertTrue(entered.wait(2))
                self.app.cancel_lifecycle()
                self.assertTrue(cleanup.wait(2))
                self.app.poll_lifecycle()
                self.assertEqual(self.app.lifecycle, job_id)
                with self.assertRaises(ValueError):
                    self.app.refresh_image()
            finally:
                release.set()
            with self.assertRaises(JobCancelled):
                self.app.controller.jobs[job_id][2].result(timeout=5)
        self.app.poll_lifecycle()
        self.assertIsNone(self.app.lifecycle)
        self.assertIn('cancelled', self.app.status.get())
        self.assertIn('does not undo', self.app.console.get('1.0', 'end'))
        self.assertEqual(self.app.controller.history.get(job_id, [self.app.workspace])['status'], 'cancelled')

    def test_restore_requires_confirmation(self):
        checkpoint = Path(self.temporary.name) / 'checkpoints/baseline'
        checkpoint.mkdir(parents=True)
        (checkpoint / 'checkpoint.json').write_text('{}')
        with patch('uconsole_workbench.simpledialog.askstring', return_value='baseline'), \
             patch('uconsole_workbench.messagebox.askyesno', return_value=False), \
             patch.object(self.app, 'start_lifecycle') as start:
            self.app.restore_checkpoint()
            start.assert_not_called()

    def test_clean_shutdown_uses_controller_and_holds_gui_guards(self):
        entered, finish = threading.Event(), threading.Event()
        runtime = MagicMock()
        runtime.guest_channel_error = None
        runtime.process.poll.return_value = None
        self.app.runtime, self.app.process = runtime, runtime.process
        self.app.active_mode = 'maintenance'
        serial = self.app.serial = MagicMock()

        def stop(*, force):
            self.assertFalse(force)
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('shutdown fixture')
            runtime.process.poll.return_value = 0

        runtime.stop.side_effect = stop
        try:
            self.app.poweroff()
            job_id = self.app.guest_job
            self.assertTrue(entered.wait(2))
            serial.close.assert_called_once()
            serial.sendall.assert_not_called()
            self.assertTrue(self.app.shutdown_requested)
            for operation in (self.app.send, self.app.poweroff,
                              lambda: self.app.control('stop')):
                with self.assertRaises(ValueError):
                    operation()
            self.assertFalse(self.app.controller.cancel(job_id)['requested'])
            with patch('uconsole_workbench.messagebox.showinfo') as dialog:
                self.app.close()
                dialog.assert_called_once()
        finally:
            finish.set()
            self.app.controller.jobs[job_id][2].result(timeout=5)
        self.app.poll_guest()
        self.assertFalse(self.app.shutdown_requested)
        self.assertIsNone(self.app.guest_job)
        record = self.app.controller.history.get(job_id, [self.app.workspace])
        self.assertEqual(record['operation'], 'stop')
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['result'], {'stopped': True})
        self.app.release()

    def test_failed_clean_shutdown_does_not_force_stop_vm(self):
        runtime = MagicMock()
        runtime.guest_channel_error = None
        runtime.process.poll.return_value = None
        runtime.stop.side_effect = ValueError('Guest refused read-only remount; emulator left running')
        self.app.runtime, self.app.process = runtime, runtime.process
        self.app.active_mode = 'maintenance'
        self.app.poweroff()
        job_id = self.app.guest_job
        with self.assertRaisesRegex(ValueError, 'left running'):
            self.app.controller.jobs[job_id][2].result(timeout=5)
        self.app.poll_guest()
        self.assertFalse(self.app.shutdown_requested)
        self.assertIsNone(self.app.guest_job)
        runtime.stop.assert_called_once_with(force=False)
        runtime.control.assert_not_called()
        self.assertIs(self.app.runtime, runtime)
        self.assertIn('left running', self.app.console.get('1.0', 'end'))
        # Dispose of the mock without pretending the production failure exited.
        runtime.process.poll.return_value = 0
        self.app.release()

    def test_transfer_uses_owned_runtime_and_blocks_serial_until_cancel_cleanup(self):
        from uconsole_agent import GuestTransferCancelled
        entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()
        runtime = MagicMock()
        runtime.guest_channel_error = None
        self.app.runtime = runtime
        self.app.process = runtime.process
        runtime.process.poll.return_value = None
        self.app.active_mode = 'maintenance'
        serial = self.app.serial = MagicMock()
        host = Path(self.temporary.name) / 'source.txt'
        host.write_text('fixture')
        def upload(source, destination, *, cancel):
            self.assertEqual((source, destination), (host.resolve(), '/tmp/fixture'))
            entered.set()
            if not cancel.wait(5):
                raise TimeoutError('cancel fixture')
            cleanup.set()
            if not release.wait(5):
                raise TimeoutError('cleanup fixture')
            raise GuestTransferCancelled('cancelled after cleanup')
        runtime.upload.side_effect = upload
        try:
            self.app.start_transfer('upload', host, '/tmp/fixture', 'Upload')
            job_id = self.app.transfer
            self.assertTrue(entered.wait(2))
            serial.close.assert_called_once()
            self.assertIsNone(self.app.serial)
            for operation in (self.app.send, self.app.poweroff,
                              lambda: self.app.control('stop'),
                              lambda: self.app.start_guest_command('true', 'Fixture')):
                with self.assertRaisesRegex(ValueError, 'transfer'):
                    operation()
            self.app.cancel_transfer()
            self.assertTrue(cleanup.wait(2))
            self.app.poll_transfer()
            self.assertEqual(self.app.transfer, job_id)
        finally:
            release.set()
            if self.app.transfer:
                with self.assertRaises(GuestTransferCancelled):
                    self.app.controller.jobs[self.app.transfer][2].result(timeout=5)
                self.app.poll_transfer()
            if self.app.controller:
                self.app.controller.runtimes.clear()
            self.app.runtime = self.app.process = None
        self.assertIsNone(self.app.transfer)
        self.assertIn('Upload cancelled', self.app.status.get())
        self.assertEqual(self.app.controller.history.get(job_id, [self.app.workspace])['status'], 'cancelled')

    def test_uncertain_transfer_channel_disables_followup_serial_operations(self):
        self.app.runtime = MagicMock(guest_channel_error='unacknowledged upload chunk')
        try:
            for operation in (self.app.send, self.app.poweroff,
                              lambda: self.app.start_guest_command('true', 'Fixture')):
                with self.assertRaisesRegex(ValueError, 'uncertain'):
                    operation()
        finally:
            self.app.runtime = None

    def test_guest_command_callback_runs_on_ui_thread_and_nonzero_exit_is_visible(self):
        runtime = MagicMock(guest_channel_error=None)
        runtime.process.poll.return_value = None
        self.app.runtime, self.app.process = runtime, runtime.process
        self.app.active_mode = 'maintenance'
        main_thread = threading.get_ident()
        callbacks = []
        def execute(script, timeout, cancel):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertEqual((script, timeout), ('exit 7', 12))
            self.assertIsInstance(cancel, threading.Event)
            return {'exit_code': 7, 'stdout': '', 'stderr': 'fixture failure'}
        runtime.execute.side_effect = execute
        def receive(result):
            self.assertEqual(threading.get_ident(), main_thread)
            callbacks.append(result)
        try:
            self.app.start_guest_command('exit 7', 'Guest fixture', timeout=12, callback=receive)
            job_id = self.app.guest_job
            self.app.controller.jobs[job_id][2].result(timeout=5)
            self.assertEqual(callbacks, [])
            self.app.poll_guest()
            self.assertEqual(callbacks[0]['exit_code'], 7)
            self.assertIn('command failed (exit 7)', self.app.status.get())
            self.assertIsNone(self.app.guest_job)
        finally:
            if self.app.controller:
                self.app.controller.runtimes.clear()
            self.app.runtime = self.app.process = None

    def test_guest_job_cancellation_blocks_conflicts_through_cleanup(self):
        from forge_guest_jobs import GuestJobCancelled
        runtime = MagicMock(guest_channel_error=None)
        runtime.process.poll.return_value = None
        self.app.runtime, self.app.process = runtime, runtime.process
        self.app.active_mode = 'maintenance'
        entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()
        def execute(script, timeout, cancel):
            entered.set()
            if not cancel.wait(5):
                raise TimeoutError('fixture cancel')
            cleanup.set()
            if not release.wait(5):
                raise TimeoutError('fixture cleanup')
            raise GuestJobCancelled('process group stopped')
        runtime.execute.side_effect = execute
        callback = MagicMock()
        try:
            self.app.start_guest_command('sleep 60', 'Guest fixture', callback=callback)
            job_id = self.app.guest_job
            self.assertTrue(entered.wait(2))
            self.app.cancel_guest()
            self.assertTrue(cleanup.wait(2))
            self.app.poll_guest()
            self.assertEqual(self.app.guest_job, job_id)
            for operation in (self.app.send, self.app.poweroff,
                              lambda: self.app.control('stop'),
                              lambda: self.app.start_transfer('download', self.app.workspace / 'output', '/tmp/a', 'Download')):
                with self.assertRaisesRegex(ValueError, 'guest command'):
                    operation()
            with patch('uconsole_workbench.messagebox.showinfo') as info:
                self.app.close()
                info.assert_called_once()
        finally:
            release.set()
            if self.app.guest_job:
                with self.assertRaises(GuestJobCancelled):
                    self.app.controller.jobs[self.app.guest_job][2].result(timeout=5)
                self.app.poll_guest()
            if self.app.controller:
                self.app.controller.runtimes.clear()
            self.app.runtime = self.app.process = None
        callback.assert_not_called()
        self.assertIn('cancelled', self.app.status.get())

    def test_unapproved_repository_host_task_never_launches(self):
        with patch('forge_controller.subprocess.Popen') as spawn:
            with self.assertRaises(PermissionError):
                self.app.start_host_task('host-check')
            self.app.tasks()
            window = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
            names = next(child for child in window.winfo_children() if isinstance(child, tk.Listbox))
            index = next(i for i in range(names.size()) if '[unapproved host]' in names.get(i))
            names.selection_set(index)
            button = next(child for child in window.winfo_children()
                          if child.winfo_class() == 'TButton')
            with patch('uconsole_workbench.details_window') as error:
                button.invoke()
                self.assertIn('not execution authority', error.call_args.args[2])
            spawn.assert_not_called()

    def configure_host_fixture(self, code):
        policy = self.app.workspace / 'host-policy.json'
        policy.write_text(json.dumps({'schema': 1, 'tasks': {'fixture': {
            'workspace': 'gui', 'cwd': str(self.app.workspace), 'timeout': 20,
            'argv': [str(Path(sys.executable).resolve()), '-c', code]}}}))
        self.app.host_task_policy = policy
        self.app.host_task_sha256 = hashlib.sha256(policy.read_bytes()).hexdigest()
        return policy

    def test_approved_host_task_snapshot_environment_and_main_thread_callback(self):
        policy = self.configure_host_fixture(
            'import os; print("approved fixture"); print(os.getenv("UCONSOLE_TEST_SECRET"))')
        with patch.dict(os.environ, {'UCONSOLE_TEST_SECRET': 'fixture-not-forwarded'}):
            self.app.job_controller()
        policy.write_text('changed after approval')
        callbacks = []
        main_thread = threading.get_ident()
        def receive(result):
            self.assertEqual(threading.get_ident(), main_thread)
            callbacks.append(result)
        self.app.start_host_task('fixture', callback=receive)
        job_id = self.app.host_job
        self.app.controller.jobs[job_id][2].result(timeout=5)
        self.assertEqual(callbacks, [])
        self.app.poll_host_task()
        self.assertEqual(callbacks[0]['stdout'], 'approved fixture\nNone\n')
        self.assertEqual(callbacks[0]['policy_sha256'], self.app.host_task_sha256)
        self.assertIn('complete', self.app.status.get())
        self.assertIsNone(self.app.host_job)

    def test_host_task_cancellation_stops_owned_process_and_releases_gui(self):
        from forge_controller import JobCancelled
        from uconsole_emulator import wait_for_log
        ready = self.app.workspace / 'host-ready'
        self.configure_host_fixture(
            'from pathlib import Path; import time; '
            f'Path({str(ready)!r}).write_text("ready"); time.sleep(60)')
        original = subprocess.Popen
        children = []
        launched = threading.Event()
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            launched.set()
            return process
        with patch('forge_controller.subprocess.Popen', side_effect=spawn):
            self.app.start_host_task('fixture')
            job_id = self.app.host_job
            try:
                self.assertTrue(launched.wait(2))
                wait_for_log(ready, b'ready', children[0], 5)
                with self.assertRaisesRegex(ValueError, 'host task'):
                    self.app.start()
                with self.assertRaises(ValueError):
                    self.app.refresh_image()
                with patch('uconsole_workbench.messagebox.showinfo') as info:
                    self.app.close()
                    info.assert_called_once()
            finally:
                self.app.cancel_host_task()
            with self.assertRaises(JobCancelled):
                self.app.controller.jobs[job_id][2].result(timeout=15)
        self.app.poll_host_task()
        self.assertIsNotNone(children[0].poll())
        self.assertIsNone(self.app.host_job)
        self.assertIn('cancelled', self.app.status.get())

    def test_busy_workspace_does_not_spawn_or_truncate_logs(self):
        from forge_workspace import WorkspaceLock
        workspace = Path(self.temporary.name)
        (workspace / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        (workspace / 'serial.log').write_text('retain evidence')
        with WorkspaceLock(workspace), patch('uconsole_workbench.subprocess.Popen') as spawn:
            self.app.start()
            with self.assertRaisesRegex(ValueError, 'busy'):
                self.app.controller.jobs[self.app.boot_job][2].result(timeout=5)
            self.app.poll_boot()
            spawn.assert_not_called()
        self.assertEqual((workspace / 'serial.log').read_text(), 'retain evidence')

    def test_desktop_start_prepares_an_unconfigured_overlay(self):
        (Path(self.temporary.name) / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        self.app.mode.set('desktop')
        with patch('forge_controller.Controller.lifecycle', return_value={'exit_code': 0}) as prepare:
            self.app.start()
            job_id = self.app.lifecycle
            self.app.controller.jobs[job_id][2].result(timeout=5)
        self.assertEqual(self.app.display_setup, job_id)
        prepare.assert_called_once_with('gui', 'configure-display',
                                        ('--serial-port', '4445', '--qmp-port', '4444'))
        self.assertTrue(self.app.start_after_setup)
        with patch.object(self.app, 'start') as launch:
            self.app.poll_lifecycle()
            launch.assert_called_once()
        self.assertIsNone(self.app.display_setup)
        self.assertIsNone(self.app.lifecycle)
        self.assertFalse(self.app.start_after_setup)
        record = self.app.controller.history.get(job_id, [self.app.workspace])
        self.assertEqual(record['operation'], 'configure_display')
        self.assertEqual(record['status'], 'completed')

    def test_failed_or_cancelled_desktop_preparation_never_autoboots(self):
        from forge_controller import JobCancelled
        (Path(self.temporary.name) / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        self.app.mode.set('desktop')
        for error in (ValueError('setup failed'), JobCancelled('setup cancelled; no rollback')):
            with self.subTest(error=error), \
                    patch('forge_controller.Controller.lifecycle', side_effect=error):
                self.app.start()
                job_id = self.app.lifecycle
                with self.assertRaises(type(error)):
                    self.app.controller.jobs[job_id][2].result(timeout=5)
                with patch.object(self.app, 'start') as launch:
                    self.app.poll_lifecycle()
                    launch.assert_not_called()
                self.assertIsNone(self.app.display_setup)
                self.assertIsNone(self.app.lifecycle)
                self.assertFalse(self.app.start_after_setup)

    def test_desktop_preparation_cancellation_keeps_guards_until_cleanup(self):
        from forge_controller import JobCancelled
        entered, cleaning, finish = threading.Event(), threading.Event(), threading.Event()
        (Path(self.temporary.name) / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        self.app.mode.set('desktop')

        def prepare(*args):
            entered.set()
            if not self.app.controller.worker_context.cancel.wait(5):
                raise TimeoutError('desktop cancel fixture')
            cleaning.set()
            if not finish.wait(5):
                raise TimeoutError('desktop cleanup fixture')
            raise JobCancelled('Desktop preparation stopped; no rollback')

        with patch('forge_controller.Controller.lifecycle', side_effect=prepare):
            self.app.start()
            job_id = self.app.lifecycle
            try:
                self.assertTrue(entered.wait(2))
                self.app.cancel_lifecycle()
                self.assertTrue(cleaning.wait(2))
                self.app.poll_lifecycle()
                self.assertEqual(self.app.display_setup, job_id)
                self.assertEqual(self.app.lifecycle, job_id)
                with self.assertRaises(ValueError):
                    self.app.start()
                with patch('uconsole_workbench.messagebox.showinfo') as dialog:
                    self.app.close()
                    dialog.assert_called_once()
            finally:
                finish.set()
            with self.assertRaises(JobCancelled):
                self.app.controller.jobs[job_id][2].result(timeout=5)
        with patch.object(self.app, 'start') as launch:
            self.app.poll_lifecycle()
            launch.assert_not_called()
        self.assertIsNone(self.app.display_setup)
        self.assertFalse(self.app.start_after_setup)

    def test_console_selection_and_agent_context_reach_system_clipboard(self):
        self.app.console.configure(state='normal')
        self.app.console.insert('1.0', 'boot message\n')
        self.app.console.tag_add('sel', '1.0', '1.4')
        self.app.console.configure(state='disabled')
        self.app.console.focus_set()
        self.app.copy_selection()
        self.assertEqual(self.root.clipboard_get(), 'boot')
        config = Path(self.temporary.name) / 'machine.json'
        config.write_text('{"machine":"raspi4b","coverage":"partial-cm4"}')
        self.app.copy_context()
        self.assertIn('uConsole CM4 agent context', self.root.clipboard_get())

    def test_console_uses_shared_osc_hyperlink_cleanup(self):
        self.app.append('\x1b]8;;file://guest/etc/service\x1b\\service\x1b]8;;\x1b\\\r\n')
        output = self.app.console.get('1.0', 'end')
        self.assertEqual(output.splitlines()[0].rstrip(), 'service')
        self.assertNotIn('\x1b', output)
        self.assertNotIn('file://guest', output)


if __name__ == '__main__':
    unittest.main()
