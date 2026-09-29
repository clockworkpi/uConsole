"""Nonblocking controls for owned USB playback or synthetic capture."""
from workbench_diagnostics import report_ui_error, report_job_error
import json
import tkinter as tk
from tkinter import ttk


class AudioPanel:
    def __init__(self, parent, controller, workspace):
        self.controller, self.workspace = controller, workspace
        self.job = self.timer = None
        self.window = tk.Toplevel(parent)
        self.window.title('USB audio surrogate')
        ttk.Label(self.window, text='USB playback/capture surrogates; no host microphone, native jack or carrier audio.').pack(padx=12, pady=8)
        self.status = tk.StringVar(value='Boot with a USB audio mode, then query attachment. Duplex controls both devices; partial effects are not rolled back. Guest ALSA state is a separate check.')
        ttk.Label(self.window, textvariable=self.status, wraplength=600).pack(padx=12, pady=8)
        row = ttk.Frame(self.window)
        row.pack(padx=12, pady=8)
        self.buttons = []
        for label, connected in (('Query', None), ('Connect', True), ('Disconnect', False)):
            button = ttk.Button(row, text=label, command=lambda value=connected: self.submit(value))
            button.pack(side='left', padx=4)
            self.buttons.append(button)
        self.enable()
        self.window.protocol('WM_DELETE_WINDOW', self.close)

    def enable(self):
        for index, button in enumerate(self.buttons):
            allowed = self.job is None and (index == 0 or 'device-control' in self.controller.grants)
            button.state(['!disabled'] if allowed else ['disabled'])

    def submit(self, connected):
        if self.job is not None:
            return
        try:
            submitted = self.controller.submit_audio(self.workspace, connected)
            self.job = submitted['job_id']
            self.enable()
            self.status.set('Waiting for owned-device readback; no rollback is implied on failure.')
            self.timer = self.window.after(100, self.poll)
        except Exception as exc:
            report_ui_error(self.window, 'forge_audio_gui', exc)
            self.status.set(str(exc))

    def poll(self):
        self.timer = None
        result = self.controller.job(self.job)
        report_job_error(self.window, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            self.timer = self.window.after(100, self.poll)
            return
        self.job = None
        self.enable()
        self.status.set(json.dumps(result['result']) if result['status'] == 'completed' else
                        str(result.get('error', result['status'])) + '; inspect evidence, no rollback implied.')

    def close(self):
        if self.job is not None:
            self.status.set('Wait for the audio job to finish before closing.')
            return False
        if self.timer:
            self.window.after_cancel(self.timer)
        self.window.destroy()
        return True
