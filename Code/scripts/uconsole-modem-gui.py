#!/usr/bin/python3
"""Desktop front end for the uConsole SIM7600G22 modem updater."""
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

HERE = Path(__file__).resolve().parent
BACKEND = HERE / 'uconsole-modem-flash.py'
SYSTEM_HELPER = Path('/usr/lib/uconsole-modem/uconsole-modem-flash.py')
GUIDE = 'https://github.com/clockworkpi/uConsole/wiki/How-to-upgrade-4G-extension-firmware'


class Updater:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.verified = None
        self.events = queue.Queue()
        self.package = tk.StringVar()
        self.version = tk.StringVar(value='LE20B04SIM7600G22')
        self.serial = tk.StringVar()
        self.fastboot = tk.StringVar()
        self.model_confirmed = tk.BooleanVar()
        self.status = tk.StringVar(value='Choose an extracted firmware package, then verify its files.')
        root.title('uConsole Modem Updater')
        root.geometry('900x600')
        root.minsize(680, 500)
        root.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)
        ttk.Label(frame, text='uConsole Modem Updater', font=('', 17, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        ttk.Label(frame, text='SIM7600G22 • Keep the modem powered throughout the update.').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(0, 12))
        ttk.Label(frame, text='Firmware folder').grid(row=2, column=0, sticky='w', padx=(0, 12))
        package_entry = ttk.Entry(frame, textvariable=self.package)
        package_entry.grid(row=2, column=1, sticky='ew')
        browse = ttk.Button(frame, text='Browse…', command=self.choose_package)
        browse.grid(row=2, column=2, padx=(8, 0))
        ttk.Label(frame, text='Firmware version').grid(row=3, column=0, sticky='w', pady=8)
        versions = list(json.loads((HERE / 'modem-firmware-manifest.json').read_text()))
        self.version_box = ttk.Combobox(frame, textvariable=self.version, values=versions, state='readonly')
        self.version_box.grid(row=3, column=1, sticky='ew', pady=8)
        ttk.Label(frame, text='Modem serial').grid(row=4, column=0, sticky='w')
        self.serial_box = ttk.Combobox(frame, textvariable=self.serial)
        self.serial_box.grid(row=4, column=1, sticky='ew')
        detect = ttk.Button(frame, text='Detect modem', command=lambda: self.start('detect'))
        detect.grid(row=4, column=2, padx=(8, 0))
        self.controls = [package_entry, browse, self.version_box, self.serial_box, detect]
        if BACKEND != SYSTEM_HELPER:
            ttk.Label(frame, text='Fastboot (optional)').grid(row=5, column=0, sticky='w', pady=8)
            tool_entry = ttk.Entry(frame, textvariable=self.fastboot)
            tool_entry.grid(row=5, column=1, columnspan=2, sticky='ew', pady=8)
            self.controls.append(tool_entry)
        confirmed = ttk.Checkbutton(frame, text='I confirmed that the connected module is a SIM7600G22.',
                                    variable=self.model_confirmed, command=self.refresh)
        confirmed.grid(row=6, column=0, columnspan=3, sticky='w', pady=10)
        self.controls.append(confirmed)
        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(0, 12))
        verify = ttk.Button(buttons, text='Verify files', command=lambda: self.start('verify'))
        verify.pack(side='left')
        check = ttk.Button(buttons, text='Check selected modem', command=lambda: self.start('check'))
        check.pack(side='left', padx=8)
        self.flash_button = ttk.Button(buttons, text='Flash selected modem', command=self.confirm_flash)
        self.flash_button.pack(side='left')
        guide = ttk.Button(buttons, text='Firmware guide', command=lambda: webbrowser.open(GUIDE))
        guide.pack(side='right')
        self.controls.extend([verify, check, guide])
        self.log = tk.Text(frame, height=12, wrap='word', state='disabled', font=('Monospace', 10))
        self.log.grid(row=8, column=0, columnspan=3, sticky='nsew')
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.grid(row=9, column=0, columnspan=3, sticky='ew', pady=8)
        ttk.Label(frame, textvariable=self.status, wraplength=650).grid(
            row=10, column=0, columnspan=3, sticky='w')
        for variable in (self.package, self.version, self.serial, self.fastboot):
            variable.trace_add('write', self.changed)
        self.refresh()
        self.poll_id = root.after(50, self.poll)

    def snapshot(self):
        return tuple(v.get().strip() for v in (self.package, self.version, self.serial, self.fastboot))

    def changed(self, *_):
        self.verified = None
        self.refresh()

    def refresh(self):
        for widget in self.controls:
            widget.configure(state='disabled' if self.busy else
                             ('readonly' if widget is self.version_box else 'normal'))
        ready = not self.busy and self.verified == self.snapshot() and self.model_confirmed.get()
        self.flash_button.configure(state='normal' if ready else 'disabled')

    def choose_package(self):
        directory = filedialog.askdirectory(title='Choose the extracted firmware folder')
        if directory:
            self.package.set(directory)
            name = Path(directory).name.removesuffix('_cpi_arm64')
            if name in self.version_box['values']:
                self.version.set(name)

    def command(self, action, selection):
        package, version, serial, fastboot = selection
        if action != 'verify' and BACKEND == SYSTEM_HELPER and os.geteuid() != 0:
            command = ['pkexec', str(BACKEND)]
        else:
            command = [sys.executable, str(BACKEND)]
        command += [package, '--version', version]
        if fastboot:
            command += ['--fastboot', fastboot]
        if action == 'verify':
            command += ['--verify-only']
        elif action == 'detect':
            command += ['--list-devices']
        else:
            command += ['--serial', serial]
            if action == 'flash':
                command += ['--flash']
        return command

    def confirm_flash(self):
        if self.busy or self.verified != self.snapshot() or not self.model_confirmed.get():
            return
        if messagebox.askyesno('Write modem firmware',
                f'Write {self.version.get()} to modem {self.serial.get()}?\n\n'
                'This replaces nine firmware partitions. Keep power connected until completion.'):
            self.start('flash')

    def start(self, action):
        if self.busy:
            return
        selection = self.snapshot()
        if not selection[0] or (action in ('check', 'flash') and not selection[2]):
            messagebox.showerror('Selection needed', 'Choose a firmware folder and, for a device check, a modem serial.')
            return
        if action == 'flash' and (self.verified != selection or not self.model_confirmed.get()):
            return
        self.verified = None
        self.busy = True
        self.status.set('Working… Device access may request administrator authorization.')
        self.refresh()
        self.progress.start()
        threading.Thread(target=self.worker, args=(action, selection), daemon=True).start()

    def worker(self, action, selection):
        output = []
        code = 1
        logfile = None
        try:
            state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
            directory = state / 'uconsole-modem-updater'
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            logfile = directory / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')
            with logfile.open('x') as saved:
                saved.write(f'Operation: {action}\nVersion: {selection[1]}\n')
                with subprocess.Popen(self.command(action, selection), stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, errors='replace') as process:
                    for line in process.stdout:
                        output.append(line)
                        saved.write(line)
                        saved.flush()
                        self.events.put(('line', line))
                    code = process.wait()
        except OSError as error:
            self.events.put(('line', f'{error}\n'))
        self.events.put(('done', (action, selection, code, ''.join(output), logfile)))

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        self.log.see('end')
        self.log.configure(state='disabled')

    def complete(self, action, selection, code, output, logfile):
        self.busy = False
        self.progress.stop()
        if code:
            self.status.set('Stopped. Read the log before retrying. No automatic retry will be attempted.')
        elif action == 'check' and selection == self.snapshot():
            self.verified = selection
            self.status.set('Device and files verified. Confirm the module model to enable Flash.')
        elif action == 'detect':
            serials = [s.strip() for s in output.splitlines() if s.strip()]
            self.serial_box.configure(values=serials)
            self.serial.set(serials[0] if len(serials) == 1 else '')
            self.status.set('Modem detected. Check the selected modem before flashing.' if len(serials) == 1
                            else 'Connect exactly one modem in fastboot mode, then detect again.')
        elif action == 'flash':
            self.status.set('Firmware written; reboot requested. Follow the guide to verify USB mode and connectivity.')
        else:
            self.status.set('Files verified. Put the modem in fastboot mode using the guide, then detect it.')
        if logfile:
            self.append(f'Log saved to {logfile}\n')
        self.refresh()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'line':
                    self.append(value)
                else:
                    self.complete(*value)
        except queue.Empty:
            pass
        self.poll_id = self.root.after(50, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('Update in progress', 'Wait for the operation to finish before closing this window.')
            return
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Updater(root)
    root.mainloop()
