"""Explicit logical-contact deck; never captures editor or global host input."""
from workbench_diagnostics import report_ui_error, report_job_error
import tkinter as tk
from tkinter import ttk
from collections import deque
from forge_keyboard_host import HostKeys, HostPointer, MATRIX_ROWS, wheel_commands
from types import SimpleNamespace


MATRIX = MATRIX_ROWS
DIRECT = ('Up', 'Down', 'Left', 'Right', 'Game A', 'Game B', 'Game X', 'Game Y',
          'Shift L', 'Shift R', 'Ctrl L', 'Ctrl R', 'Alt L', 'Mouse L', 'Alt R',
          'Mouse R', 'Trackball')


class KeyboardDeck:
    def __init__(self, parent, controller, workspace):
        runtime = controller.runtime(workspace)
        if runtime.args.keyboard != 'composite':
            raise ValueError('Select composite keyboard before booting this VM')
        self.identity = runtime.identity
        self.controller, self.workspace = controller, workspace
        self.held, self.buttons = set(), {}
        self.job = None
        self.failed = False
        self.host_keys = HostKeys()
        self.host_pointer = HostPointer()
        self.host_queue = deque()
        self.host_active = False
        self.pending_releases = {}
        self.wheel_remainder = 0
        self.window = tk.Toplevel(parent)
        self.window.title('Firmware keyboard deck — logical contacts')
        self.status = tk.StringVar(value='Matrix wiring layout. Click to hold/release; no global key capture.')
        ttk.Label(self.window, textvariable=self.status).pack(fill='x', padx=8, pady=8)
        self.typing = tk.Canvas(self.window, height=40, takefocus=True, background='#243447')
        self.typing.pack(fill='x', padx=8, pady=4)
        self.typing.create_text(8, 20, anchor='w', fill='white',
                                text='Click here to type • US key mapping • Menu = Fn • focus loss releases host holds')
        self.typing.bind('<Button-1>', lambda event: self.typing.focus_set())
        self.typing.bind('<KeyPress>', self.key_press)
        self.typing.bind('<KeyRelease>', self.key_release)
        self.typing.bind('<FocusOut>', self.focus_out)
        self.pointer = tk.Canvas(self.window, height=90, takefocus=True, background='#304338')
        self.pointer.pack(fill='x', padx=8, pady=4)
        self.pointer.create_text(8, 20, anchor='w', fill='white',
                                 text='Pointer pad: click to focus • 4 pixels/trackball edge • left/middle/right buttons')
        self.pointer.bind('<Motion>', self.pointer_motion)
        self.pointer.bind('<Leave>', lambda event: self.host_pointer.reset())
        self.pointer.bind('<FocusOut>', self.focus_out)
        self.select_scroll = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.window, text='Enable Select-scroll: firmware also presses Space / game button 9',
                        variable=self.select_scroll).pack(anchor='w', padx=8)
        self.pointer.bind('<Button-4>', lambda event: self.pointer_wheel(1))
        self.pointer.bind('<Button-5>', lambda event: self.pointer_wheel(-1))
        self.pointer.bind('<MouseWheel>', self.wheel_event)
        for button in (1, 2, 3):
            self.pointer.bind(f'<ButtonPress-{button}>', lambda event: self.pointer_button(event))
            self.pointer.bind(f'<ButtonRelease-{button}>', lambda event: self.pointer_button(event, release=True))
        grid = ttk.Frame(self.window)
        grid.pack(padx=8)
        for row, labels in enumerate(MATRIX):
            for column, label in enumerate(labels):
                if label:
                    self.contact(grid, ('matrix', row, column), label, row, column)
        direct = ttk.Frame(self.window)
        direct.pack(padx=8, pady=8)
        for index, label in enumerate(DIRECT):
            self.contact(direct, ('key', index), label, index // 8, index % 8)
        actions = ttk.Frame(self.window)
        actions.pack(padx=8, pady=8)
        ttk.Button(actions, text='Release deck holds', command=self.release_all).pack(side='left')
        for value, label in ((1, 'Keyboard mode'), (0, 'Game mode')):
            ttk.Button(actions, text=label, command=lambda v=value:
                       self.send([('switch', v), ('run', 10)], set(self.held))).pack(side='left')
        for direction in range(4):
            ttk.Button(actions, text=f'Trackball edge {direction}', command=lambda d=direction:
                       self.send([('edge', d), ('run', 10)], set(self.held))).pack(side='left')
        self.window.protocol('WM_DELETE_WINDOW', self.close)

    def contact(self, parent, contact, label, row, column):
        button = ttk.Button(parent, text=label, width=10,
                            command=lambda: self.toggle(contact))
        button.grid(row=row, column=column)
        self.buttons[contact] = (button, label)

    def toggle(self, contact):
        if self.host_active:
            self.status.set('Leave the typing area and wait for host key releases before using deck buttons.')
            return
        held = set(self.held)
        value = int(contact not in held)
        if value:
            if len(held) >= 32:
                self.status.set('Release a contact before holding another (limit 32).')
                return
            held.add(contact)
        else:
            held.remove(contact)
        self.send([(*contact, value), ('run', 10)], held)

    def release_all(self):
        if self.held:
            self.send([(*contact, 0) for contact in sorted(self.held)] + [('run', 10)], set())

    def host_event(self, event, *, release=False, clear=False):
        if self.failed:
            return 'break'
        if not self.host_active and (self.held or self.job):
            self.status.set('Release deck holds and finish pending actions before host typing.')
            return 'break'
        try:
            commands, held = self.host_keys.change(event.keycode if not clear else None,
                                                  event.keysym if not clear else None,
                                                  release=release, clear=clear)
            if commands:
                if len(self.host_queue) >= 64:
                    raise ValueError('Host input queue overflow; stop the VM before recreating input')
                self.host_queue.append((commands, held))
                self.host_active = True
            self.drain_host()
        except ValueError as exc:
            report_ui_error(self.window, 'forge_keyboard_gui', exc)
            self.failed = True
            self.status.set(str(exc))
        return 'break'

    def key_press(self, event):
        pending = self.pending_releases.pop(event.keycode, None)
        if pending is not None:
            self.window.after_cancel(pending)
        return self.host_event(event)

    def key_release(self, event):
        # X11 may encode auto-repeat as adjacent release/press events. Delay
        # release until idle so the matching press can cancel that pair.
        pending = self.pending_releases.pop(event.keycode, None)
        if pending is not None:
            self.window.after_cancel(pending)

        def release():
            self.pending_releases.pop(event.keycode, None)
            self.host_event(event, release=True)

        self.pending_releases[event.keycode] = self.window.after_idle(release)
        return 'break'

    def focus_out(self, event):
        self.host_pointer.reset()
        self.wheel_remainder = 0
        for pending in self.pending_releases.values():
            self.window.after_cancel(pending)
        self.pending_releases.clear()
        return self.host_event(event, clear=True)

    def pointer_button(self, event, *, release=False):
        if not release and self.window.focus_get() != self.pointer:
            self.pointer.focus_set()
            self.host_pointer.reset()
            return 'break'  # First click activates the pad, not the guest.
        return self.host_event(SimpleNamespace(keycode=-event.num, keysym=f'Mouse{event.num}'),
                               release=release)

    def pointer_motion(self, event):
        if self.window.focus_get() != self.pointer or self.failed:
            return 'break'
        if not self.host_active and (self.held or self.job):
            self.status.set('Release deck holds before using pointer input.')
            self.host_pointer.reset()
            return 'break'
        try:
            batches = self.host_pointer.move(event.x, event.y)
            if len(self.host_queue) + len(batches) > 64:
                raise ValueError('Pointer queue overflow; stop the VM before recreating input')
            for commands in batches:
                self.host_queue.append((commands, set(self.host_keys.held)))
            if batches:
                self.host_active = True
            self.drain_host()
        except ValueError as exc:
            report_ui_error(self.window, 'forge_keyboard_gui', exc)
            self.failed = True
            self.status.set(str(exc))
        return 'break'

    def drain_host(self):
        if self.job or self.failed:
            return
        if self.host_queue:
            commands, held = self.host_queue.popleft()
            self.send(commands, held, host=True)
        elif not self.host_keys.held:
            self.host_active = False

    def wheel_event(self, event):
        if self.window.focus_get() != self.pointer or self.failed or not self.select_scroll.get():
            self.wheel_remainder = 0
            return self.pointer_wheel(0)
        unit = 1 if self.window.tk.call('tk', 'windowingsystem') == 'aqua' else 120
        total = self.wheel_remainder + event.delta
        notches = (abs(total) // unit) * (1 if total >= 0 else -1)
        self.wheel_remainder = total - notches * unit
        return self.pointer_wheel(notches)

    def pointer_wheel(self, notches):
        if self.window.focus_get() != self.pointer or self.failed:
            return 'break'
        if not self.select_scroll.get():
            self.wheel_remainder = 0
            self.status.set('Wheel disabled: Select-scroll also emits Space or game button 9. Enable explicitly.')
            return 'break'
        if self.host_keys.held or (not self.host_active and (self.held or self.job)):
            self.status.set('Release held contacts before Select-scroll.')
            return 'break'
        try:
            commands = wheel_commands(notches)
            if commands:
                if len(self.host_queue) >= 64:
                    raise ValueError('Wheel queue overflow; stop the VM before recreating input')
                self.host_queue.append((commands, set()))
                self.host_active = True
                self.drain_host()
        except ValueError as exc:
            report_ui_error(self.window, 'forge_keyboard_gui', exc)
            self.failed = True
            self.status.set(str(exc))
        return 'break'

    def send(self, commands, held, *, host=False):
        if self.host_active and not host:
            self.status.set('Wait for host input to release before using deck controls.')
            return
        if self.job or self.failed:
            self.status.set('Wait for the current action, or inspect the failed bridge before restarting the VM.')
            return
        try:
            if self.controller.runtime(self.workspace).identity != self.identity:
                raise ValueError('This deck belongs to an earlier VM; close and reopen it')
            job = self.controller.submit_keyboard(self.workspace, commands)
        except (ValueError, PermissionError) as exc:
            report_ui_error(self.window, 'forge_keyboard_gui', exc)
            if host:
                self.failed = True
            self.status.set(str(exc))
            return
        self.job = job['job_id']
        self.status.set('Sending firmware input…')
        self.window.after(25, lambda: self.poll(held))

    def poll(self, held):
        result = self.controller.job(self.job)
        report_job_error(self.window, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            self.window.after(25, lambda: self.poll(held))
            return
        self.job = None
        if result['status'] != 'completed':
            self.failed = True
            self.status.set('Input uncertain: inspect job evidence. Held guest keys are not automatically released.')
            return
        self.held = held
        for contact, (button, label) in self.buttons.items():
            button.configure(text=('● ' if contact in held else '') + label)
        self.status.set(f'{len(held)} deck contacts held (limit 32). Agent input may hold other contacts.')
        self.drain_host()

    def close(self):
        if self.job:
            self.status.set('Wait for accepted input to finish before closing.')
            return
        runtime = self.controller.runtimes.get(self.workspace)
        live = runtime and runtime.identity == self.identity and runtime.process.poll() is None
        if live and (self.held or self.failed):
            self.status.set('Release all before closing; if input is uncertain, stop the VM first.')
            return
        for pending in self.pending_releases.values():
            self.window.after_cancel(pending)
        self.window.destroy()
