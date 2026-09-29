"""Nonblocking scenario controls for the owned synthetic composite modem."""
from workbench_diagnostics import report_ui_error, report_job_error
import json
import tkinter as tk
from tkinter import ttk


FIELDS = {
    'registration': ('0', '1', '2', '3', '4', '5'),
    'radio': ('0', '1', '4'),
    'sim': ('ready', 'absent', 'pin', 'puk'),
    'rssi': tuple(map(str, range(32))) + ('99',),
    'ber': tuple(map(str, range(8))) + ('99',),
}


class ModemPanel:
    def __init__(self, parent, controller, workspace):
        self.controller, self.workspace = controller, workspace
        self.job = self.timer = None
        self.window = tk.Toplevel(parent)
        self.window.title('Synthetic modem controls')
        ttk.Label(self.window, text='Boot with the composite modem first. Synthetic AT/RNDIS only; '
                  'no physical RF or PDP equivalence.', wraplength=580).pack(padx=12, pady=8)
        ttk.Label(self.window, text='Registration: 0 idle, 1 home, 2 searching, 3 denied, 4 unknown, '
                  '5 roaming. Radio: 0 minimum, 1 on, 4 off.', wraplength=580).pack(padx=12)
        self.field = tk.StringVar(value='registration')
        self.value = tk.StringVar(value='0')
        row = ttk.Frame(self.window)
        row.pack(padx=12, pady=8)
        ttk.Label(row, text='Requested change:').pack(side='left')
        self.field_box = ttk.Combobox(row, textvariable=self.field, values=tuple(FIELDS),
                                      state='readonly', width=14)
        self.field_box.pack(side='left', padx=4)
        self.value_box = ttk.Combobox(row, textvariable=self.value,
                                      values=FIELDS['registration'], state='readonly', width=10)
        self.value_box.pack(side='left', padx=4)
        self.field_box.bind('<<ComboboxSelected>>', self.select_field)
        self.apply = ttk.Button(row, text='Apply change', command=self.submit)
        self.apply.pack(side='left', padx=4)
        self.query = ttk.Button(row, text='Query state', command=lambda: self.submit(query=True))
        self.query.pack(side='left', padx=4)
        self.refresh = ttk.Button(row, text='Check job', command=self.poll)
        self.refresh.pack(side='left', padx=4)
        cable = ttk.Frame(self.window)
        cable.pack(padx=12, pady=4)
        ttk.Label(cable, text='Synthetic USB cable (SIM/PDP state retained):').pack(side='left')
        self.cable_buttons = []
        for label, connected in (('Connect USB', True), ('Disconnect USB', False)):
            item = ttk.Button(cable, text=label,
                              command=lambda value=connected: self.submit(connected=value))
            item.pack(side='left', padx=4)
            self.cable_buttons.append(item)
        self.status = tk.StringVar(value='Selections are requests, not observed device state. '
                                   'Changes can interrupt guest networking.')
        ttk.Label(self.window, textvariable=self.status, wraplength=580).pack(padx=12, pady=8)
        self.enable()
        self.window.protocol('WM_DELETE_WINDOW', self.close)

    def select_field(self, event=None):
        values = FIELDS[self.field.get()]
        self.value_box.configure(values=values)
        self.value.set(values[0])

    def enable(self):
        allowed = self.job is None and 'device-control' in self.controller.grants
        self.apply.state(['!disabled'] if allowed else ['disabled'])
        for button in self.cable_buttons:
            button.state(['!disabled'] if allowed else ['disabled'])
        self.query.state(['!disabled'] if self.job is None else ['disabled'])
        self.refresh.state(['!disabled'] if self.job is not None else ['disabled'])
        for box in (self.field_box, self.value_box):
            box.configure(state='readonly' if self.job is None else 'disabled')

    def submit(self, query=False, connected=None):
        if self.job is not None:
            return
        try:
            if connected is not None:
                submitted = self.controller.submit_modem(self.workspace, connected=connected)
            elif query:
                submitted = self.controller.submit_modem(self.workspace)
            else:
                field, value = self.field.get(), self.value.get()
                if field not in FIELDS or value not in FIELDS[field]:
                    raise ValueError('Select a supported modem field/value')
                changes = {field: value if field == 'sim' else int(value)}
                submitted = self.controller.submit_modem(self.workspace, changes)
            self.job = submitted['job_id']
            self.enable()
            self.status.set('Waiting for link-synchronized acknowledgement; failure does not imply rollback.')
            self.timer = self.window.after(100, self.poll)
        except Exception as exc:
            report_ui_error(self.window, 'forge_modem_gui', exc)
            self.status.set(str(exc))

    def poll(self):
        if self.timer is not None:
            self.window.after_cancel(self.timer)
            self.timer = None
        if self.job is None:
            return
        try:
            result = self.controller.job(self.job)
            report_job_error(self.window, result)
        except Exception as exc:
            report_ui_error(self.window, 'forge_modem_gui', exc)
            self.status.set(str(exc) + '; use Check job. Do not resubmit an uncertain change.')
            return
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            self.timer = self.window.after(100, self.poll)
            return
        self.job = None
        self.enable()
        self.status.set(json.dumps(result['result']) if result['status'] == 'completed' else
                        str(result.get('error', result['status'])) + '; inspect evidence; no automatic retry or rollback.')

    def close(self):
        if self.job is not None:
            self.status.set('Wait for the modem job to reach terminal status before closing.')
            return False
        if self.timer is not None:
            self.window.after_cancel(self.timer)
        self.window.destroy()
        return True
