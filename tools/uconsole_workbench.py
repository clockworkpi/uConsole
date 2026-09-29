#!/usr/bin/env python3
"""Desktop editor and emulator console; Python/Tk on Linux, macOS and Windows."""
import argparse
import codecs
import json
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from uconsole_emulator import BUILD_ROOT, DEFAULT, ROOT, SURROGATE_SCHEMA, DISPLAY_BACKENDS, native_display, parser, command, qmp
from uconsole_agent import ANSI, context_markdown, inspect as agent_inspect, load_tasks
from forge_scenario import Scenario, PROPERTIES, validate_value
from forge_replay import Replay
from forge_projects import keyboard_project
from forge_controller import Controller, GRANTS
from forge_client import ClientSession
from forge_dispatch import OwnerQueue
from forge_local import LocalListener
from forge_history import default_path as history_default_path
from forge_guest_files import listing_script
from forge_audio import MODES as AUDIO_MODES
from workbench_help import Guide, Tooltip, attach_hints
from workbench_diagnostics import Diagnostics, details_window, report_job_error
from workbench_layout import WrappingToolbar

class Workbench:
    def __init__(self, root, workspace, qmp_port=4444, serial_port=4445, *,
                 host_task_policy=None, host_task_sha256=None,
                 target_policy=None, target_policy_sha256=None,
                 recovery_policy=None, recovery_policy_sha256=None,
                 agent_socket=None, agent_grants=(), agent_files_root=None):
        self.root, self.workspace = root, workspace.resolve()
        self.diagnostics = Diagnostics(self.workspace)
        root._workbench_diagnostics = self.diagnostics
        root.report_callback_exception = self.callback_error
        self.qmp_port, self.serial_port = qmp_port, serial_port
        self.process = self.serial = self.log = self.errors = None
        self.runtime = None
        self.agent_listener = self.agent_queue = self.agent_job = None
        self.agent_files_root = agent_files_root
        self.boot_job = None
        self.power_job = None
        self.control_job = None
        self.initial_scenario = None
        self.replay_future = self.replay_job = None
        self.replay_evidence = None
        self.display_setup = None
        self.lifecycle = self.lifecycle_path = None
        self.lifecycle_label = ''
        self.controller = None
        self.target_panel = None
        self.recovery_panel = None
        self.audio_panel = None
        self.modem_panel = None
        self.target_policy, self.target_policy_sha256 = target_policy, target_policy_sha256
        if bool(target_policy) != bool(target_policy_sha256):
            raise ValueError('Target policy and approved SHA-256 must be provided together')
        self.recovery_policy, self.recovery_policy_sha256 = recovery_policy, recovery_policy_sha256
        if bool(recovery_policy) != bool(recovery_policy_sha256):
            raise ValueError('Recovery policy and approved SHA-256 must be provided together')
        self.host_task_policy, self.host_task_sha256 = host_task_policy, host_task_sha256
        if bool(host_task_policy) != bool(host_task_sha256):
            raise ValueError('Host-task policy and approved SHA-256 must be provided together')
        self.host_job = self.host_callback = None
        self.host_label = ''
        self.start_after_setup = False
        self.filename = None
        self.transfer = None
        self.transfer_label = ''
        self.guest_job = self.guest_callback = None
        self.guest_label = ''
        self.shutdown_requested = False
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        if host_task_policy or target_policy or recovery_policy:
            self.job_controller()  # Verify the explicit startup approval before exposing tasks.
        root.title(f'uConsole CM4 Workbench — {self.workspace.name} — partial hardware emulation')
        root.geometry('1100x760')
        root.minsize(800, 650)
        toolbar = WrappingToolbar(root, padding=6)
        toolbar.pack(fill='x')
        self.mode = tk.StringVar(value='desktop')
        ttk.Combobox(toolbar, textvariable=self.mode, values=['maintenance', 'normal', 'desktop'], state='readonly', width=14).pack(side='left')
        self.display = tk.StringVar(value=native_display())
        ttk.Combobox(toolbar, textvariable=self.display, values=DISPLAY_BACKENDS,
                     state='readonly', width=7).pack(side='left', padx=2)
        for label, action in [('Start', self.start), ('Pause', lambda: self.control('stop')),
                              ('Resume', lambda: self.control('cont')), ('Power off', self.poweroff),
                              ('Export image', self.export), ('Open file', self.open_file), ('Save', self.save_file),
                              ('Copy to guest', self.copy_to_guest), ('Live Schematic', self.show_schematic),
                              ('Setup', self.show_wizard)]:
            ttk.Button(toolbar, text=label, command=lambda fn=action: self.guard(fn)).pack(side='left', padx=2)
        agentbar = WrappingToolbar(root, padding=(6, 0, 6, 6))
        agentbar.pack(fill='x')
        image_button = ttk.Menubutton(agentbar, text='Image')
        image_menu = tk.Menu(image_button, tearoff=False)
        for label, action in [('Import guest image', self.import_image),
                              ('Create checkpoint', self.checkpoint), ('Restore checkpoint', self.restore_checkpoint),
                              ('Recover interrupted restore', self.recover_image), ('Refresh boot files', self.refresh_image),
                              ('Cancel image job', self.cancel_lifecycle)]:
            image_menu.add_command(label=label, command=lambda fn=action: self.guard(fn))
        image_button.configure(menu=image_menu)
        image_button.pack(side='left', padx=2)
        for label, action in [('Copy selection', self.copy_selection), ('Copy boot log', self.copy_console),
                              ('Copy agent context', self.copy_context), ('Paste command', self.paste_command),
                              ('Save transcript', self.save_transcript), ('Guest files', self.guest_files),
                              ('Tasks', self.tasks)]:
            ttk.Button(agentbar, text=label, command=lambda fn=action: self.guard(fn)).pack(side='left', padx=2)
        jobsbar = WrappingToolbar(root, padding=(6, 0, 6, 6))
        jobsbar.pack(fill='x')
        self.keyboard = tk.StringVar(value='generic')
        self.audio = tk.StringVar(value='none')
        self.modem = tk.StringVar(value='none')
        self.adc_reference = tk.StringVar(value='fixed')
        ttk.Label(jobsbar, text='Next boot input:').pack(side='left')
        ttk.Combobox(jobsbar, textvariable=self.keyboard, values=('generic', 'composite'),
                     state='readonly', width=10).pack(side='left')
        ttk.Button(jobsbar, text='Keyboard deck',
                   command=lambda: self.guard(self.keyboard_deck)).pack(side='left', padx=2)
        ttk.Button(jobsbar, text='Physical target',
                   command=lambda: self.guard(self.physical_target)).pack(side='left', padx=2)
        ttk.Label(jobsbar, text='Audio surrogate:').pack(side='left')
        ttk.Combobox(jobsbar, textvariable=self.audio, values=AUDIO_MODES,
                     state='readonly', width=9).pack(side='left')
        ttk.Button(jobsbar, text='Audio controls', command=lambda: self.guard(self.audio_controls)).pack(side='left', padx=2)
        profilebar = WrappingToolbar(self.root)
        profilebar.pack(fill='x', padx=8, pady=2)
        ttk.Button(profilebar, text='Recovery jobs', command=lambda: self.guard(self.recovery_controls)).pack(side='left', padx=4)
        ttk.Label(profilebar, text='ADC reference at next boot:').pack(side='left')
        ttk.Combobox(profilebar, textvariable=self.adc_reference, values=('fixed', 'missing'),
                     state='readonly', width=10).pack(side='left', padx=4)
        ttk.Label(profilebar, text='Missing describes the guest supply, not converter power.').pack(side='left')
        ttk.Label(profilebar, text='Modem next boot:').pack(side='left', padx=6)
        ttk.Combobox(profilebar, textvariable=self.modem, values=('none', 'composite'),
                     state='readonly', width=10).pack(side='left')
        ttk.Button(profilebar, text='Modem controls',
                   command=lambda: self.guard(self.modem_controls)).pack(side='left', padx=4)
        for label, action in [('Cancel transfer', self.cancel_transfer),
                              ('Cancel boot', self.cancel_boot),
                              ('Cancel guest command', self.cancel_guest),
                              ('Cancel host task', self.cancel_host_task)]:
            ttk.Button(jobsbar, text=label, command=lambda fn=action: self.guard(fn)).pack(side='left', padx=2)
        ttk.Label(root, text='BCM2711 / 2 GiB • 1280×720 surrogate display • AXP221 PMIC • USB input substitutes • no DSI/GPU fidelity', padding=6).pack(fill='x')
        scenario_bar = WrappingToolbar(root, padding=(6, 0, 6, 6))
        scenario_bar.pack(fill='x')
        ttk.Button(scenario_bar, text='Load power profile',
                   command=lambda: self.guard(self.load_scenario)).pack(side='left')
        ttk.Button(scenario_bar, text='Clear profile',
                   command=lambda: self.guard(self.clear_scenario)).pack(side='left', padx=4)
        self.scenario_label = tk.StringVar(value='Next boot: default power state')
        ttk.Label(scenario_bar, textvariable=self.scenario_label).pack(side='left')
        power_bar = WrappingToolbar(root, padding=(6, 0, 6, 6))
        power_bar.pack(fill='x')
        ttk.Label(power_bar, text='Live power:').pack(side='left')
        self.power_field = tk.StringVar(value='ac_present')
        ttk.Combobox(power_bar, textvariable=self.power_field, values=list(PROPERTIES),
                     state='readonly', width=23).pack(side='left', padx=4)
        self.power_value = tk.StringVar(value='true')
        ttk.Entry(power_bar, textvariable=self.power_value, width=10).pack(side='left')
        ttk.Button(power_bar, text='Apply field',
                   command=lambda: self.guard(lambda: self.live_power(change=True))).pack(side='left', padx=4)
        ttk.Button(power_bar, text='Read state',
                   command=lambda: self.guard(self.live_power)).pack(side='left')
        ttk.Label(power_bar, text='true/false or integer • key events may shut down guest').pack(side='left', padx=6)
        replay_bar = WrappingToolbar(root, padding=(6, 0, 6, 6))
        replay_bar.pack(fill='x')
        ttk.Button(replay_bar, text='Run power schedule',
                   command=lambda: self.guard(self.start_replay)).pack(side='left')
        ttk.Button(replay_bar, text='Cancel replay',
                   command=lambda: self.guard(self.cancel_replay)).pack(side='left', padx=4)
        self.replay_status = tk.StringVar(value='Host-clock replay • no rollback of completed events')
        ttk.Label(replay_bar, textvariable=self.replay_status).pack(side='left')
        panes = ttk.Panedwindow(root, orient='vertical')
        edit_frame = ttk.Labelframe(panes, text='Host source editor')
        self.editor = tk.Text(edit_frame, undo=True, wrap='none', font='TkFixedFont', height=12)
        self.editor.pack(fill='both', expand=True)
        panes.add(edit_frame, weight=1)
        console_frame = ttk.Labelframe(panes, text='Guest terminal — 80×24 • click to type • Ctrl+Shift+C/V copy/paste')
        from workbench_terminal import Terminal
        self.console = Terminal(console_frame, lambda data: self.guard(lambda: self.send_terminal(data)))
        self.console.pack(fill='both', expand=True)
        terminal_scroll = ttk.Scrollbar(console_frame, orient='horizontal', command=self.console.xview)
        terminal_scroll.pack(fill='x')
        self.console.configure(xscrollcommand=terminal_scroll.set)
        self.console.bind('<Command-c>', self.console.copy)
        self.console.bind('<Button-3>', self.console_menu)
        self.editor.bind('<Button-3>', self.editor_menu)
        panes.add(console_frame, weight=2)
        def keep_panes_visible(event):
            minimum = min(80, max(1, event.height // 3))
            divider = panes.sashpos(0)
            if divider < minimum or divider > event.height - minimum:
                panes.sashpos(0, max(minimum, event.height // 3))
        panes.bind('<Configure>', keep_panes_visible)
        self.entry = ttk.Entry(root)
        self.entry.bind('<Return>', lambda event: self.guard(self.send))
        root.bind('<Control-s>', lambda event: self.guard(self.save_file))
        root.bind('<Command-s>', lambda event: self.guard(self.save_file))
        self.status = tk.StringVar(value=f'Stopped • {self.workspace}')
        ttk.Label(root, textvariable=self.status, padding=6).pack(side='bottom', fill='x')
        self.entry.pack(side='bottom', fill='x', padx=6, pady=5)
        panes.pack(fill='both', expand=True)
        firmware, source_update = keyboard_project(ROOT, BUILD_ROOT)
        if firmware:
            self.load_file(firmware)
            suffix = ' • bundled source updated; your project was preserved' if source_update else ''
            self.status.set(f'Stopped • {self.workspace} • {firmware}{suffix}')
        root.protocol('WM_DELETE_WINDOW', self.close)
        menu = tk.Menu(root)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label='User guide (F1)', command=self.show_help)
        help_menu.add_command(label='Getting started', command=self.show_help)
        help_menu.add_command(label='Diagnostics', command=lambda: self.guard(self.show_diagnostics))
        menu.add_cascade(label='Help', menu=help_menu)
        root.configure(menu=menu)
        root.bind('<F1>', lambda event: self.show_help())
        self.help_hints = attach_hints(root, self.show_help)
        for variable, topic, hint in [
                (self.mode, 'Boot and display', 'Next boot mode: maintenance, regular system, or surrogate desktop.'),
                (self.display, 'Boot and display', 'Next boot display backend. “none” means no graphical window.'),
                (self.keyboard, 'Power and device controls', 'Select USB input devices for the next boot.'),
                (self.audio, 'Power and device controls', 'Select surrogate USB audio for the next boot.'),
                (self.modem, 'Power and device controls', 'Select the synthetic modem for the next boot.'),
                (self.adc_reference, 'Power and device controls', 'Guest ADC supply reference; does not control converter power.'),
                (self.power_field, 'Power and device controls', 'Live PMIC property to change or query.'),
                (self.power_value, 'Power and device controls', 'Boolean or integer value for the selected live power field.')]:
            pending = [root]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if 'textvariable' in widget.keys() and str(widget['textvariable']) == str(variable):
                    self.help_hints.append(Tooltip(widget, hint + ' Press F1 for help.'))
                    widget.bind('<F1>', lambda event, name=topic: (self.show_help(name), 'break')[1])
        if not (self.workspace / 'machine.json').is_file():
            self.status.set(f'No guest image prepared • Image → Import guest image • F1 for help • {self.workspace}')
        self.poll_timer = root.after(100, self.poll)
        root.bind('<Destroy>', self.cancel_poll, add='+')
        if agent_socket:
            self.enable_attachment(agent_socket, agent_grants)

    def enable_attachment(self, path, grants=()):
        if self.agent_listener:
            raise ValueError('Workbench already has an attachment listener')
        grants = tuple(grants)
        owner = self.job_controller()
        ClientSession(owner, ['gui'], grants, files_root=self.agent_files_root).close()
        self.agent_queue = OwnerQueue()
        def dispatch(tool, args, invoke):
            return self.agent_queue.call(lambda: self.agent_operation(tool, args, invoke))
        try:
            self.agent_listener = LocalListener(path, lambda: ClientSession(
                owner, ['gui'], grants, files_root=self.agent_files_root, dispatch=dispatch))
        except BaseException:
            self.agent_queue.close()
            self.agent_queue = None
            raise
        self.append(f'Agent attachment: {path}; workspace gui; grants: {sorted(grants)}\n')

    def agent_operation(self, tool, args, invoke):
        if tool in ('host_tasks', 'target_transactions', 'recovery_jobs', 'job_history'):
            return invoke()
        self.require_no_replay()
        if self.lifecycle is not None or self.display_setup is not None:
            raise ValueError('Wait for the GUI image operation to finish')
        if tool in ('guest_exec', 'upload', 'download', 'stop'):
            if not (tool == 'stop' and args.get('force')):
                self.require_guest_idle()
            self.release_serial()
        result = invoke()
        if 'job_id' in result:
            self.agent_job = result['job_id']
            self.append(f'Agent {tool} job {self.agent_job}\n')
        return result

    def poll_agent(self):
        if self.agent_job is None:
            return
        result = self.controller.job(self.agent_job)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        self.agent_job = None
        runtime = self.controller.runtimes.get('gui')
        if runtime and self.runtime is None and runtime.process is not None:
            self.runtime, self.process = runtime, runtime.process
            self.active_mode = runtime.args.mode
            self.decoder.reset()
        self.status.set(f'Agent {result["operation"]} {result["status"]}')
        self.append(json.dumps(result, indent=2) + '\n')

    def close_attachment(self):
        if self.agent_queue:
            self.agent_queue.close()
        if self.agent_listener:
            self.agent_listener.close()
            self.agent_listener = None

    def clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.status.set(f'Copied {len(text):,} characters to the system clipboard')

    def copy_selection(self, event=None):
        try:
            widget = self.root.selection_own_get()
        except tk.TclError:
            widget = self.root.focus_get()
        if widget not in (self.console, self.editor):
            widget = self.console
        try:
            text = widget.get('sel.first', 'sel.last')
        except tk.TclError:
            raise ValueError('Select text in the editor or console first')
        self.clipboard(text)
        return 'break'

    def copy_console(self):
        self.clipboard(self.console.get('1.0', 'end-1c'))

    def copy_context(self):
        state = agent_inspect(self.workspace, None, 120)
        if self.runtime and self.process.poll() is None:
            state['runtime'] = self.runtime.control('query-status')
        self.clipboard(context_markdown(state))

    def paste_command(self):
        text = self.root.clipboard_get()
        # A line-oriented serial input intentionally does not accept embedded newlines.
        self.entry.delete(0, 'end')
        self.entry.insert(0, ' '.join(text.splitlines()))
        self.entry.focus_set()

    def console_menu(self, event):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='Copy selection', command=lambda: self.guard(self.copy_selection))
        menu.add_command(label='Copy terminal screen', command=self.copy_console)
        menu.add_command(label='Paste into terminal…', command=lambda: self.guard(self.console.paste))
        menu.add_command(label='Copy agent context', command=self.copy_context)
        menu.tk_popup(event.x_root, event.y_root)

    def editor_menu(self, event):
        self.editor.focus_set()
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='Cut', command=lambda: self.editor.event_generate('<<Cut>>'))
        menu.add_command(label='Copy', command=lambda: self.editor.event_generate('<<Copy>>'))
        menu.add_command(label='Paste', command=lambda: self.editor.event_generate('<<Paste>>'))
        menu.tk_popup(event.x_root, event.y_root)

    def save_transcript(self):
        name = filedialog.asksaveasfilename(parent=self.root, defaultextension='.txt',
                                            title='Save serial transcript')
        if name:
            target = Path(name)
            if target.exists():
                raise ValueError('Choose a new transcript path')
            serial_log = self.workspace / 'serial.log'
            if serial_log.is_file():
                import shutil
                with serial_log.open('rb') as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
            else:
                with target.open('x') as output:
                    output.write(self.console.get('1.0', 'end-1c'))
            self.status.set(f'Saved transcript to {target}')

    def release_serial(self):
        if self.serial:
            self.serial.close()
            self.serial = None

    def guest_files(self):
        window = tk.Toplevel(self.root)
        window.title('Guest files — maintenance shell')
        window.geometry('700x460')
        path = tk.StringVar(value='/')
        top = ttk.Frame(window, padding=6)
        top.pack(fill='x')
        ttk.Entry(top, textvariable=path).pack(side='left', fill='x', expand=True)
        tree = ttk.Treeview(window, columns=('type', 'bytes'), show='tree headings')
        tree.heading('#0', text='Name')
        tree.heading('type', text='Type')
        tree.heading('bytes', text='Bytes')
        tree.column('type', width=80, anchor='center')
        tree.column('bytes', width=120, anchor='e')
        tree.pack(fill='both', expand=True, padx=6)

        listed_path = '/'
        next_offset = None
        entry_names = {}

        def refresh(offset=0):
            requested_path = path.get()
            def receive(result):
                nonlocal listed_path, next_offset
                if not window.winfo_exists():
                    return
                if result['exit_code']:
                    raise ValueError(result.get('stderr') or result['stdout'] or 'Guest listing failed')
                data = json.loads(result['stdout'])
                tree.delete(*tree.get_children())
                entry_names.clear()
                for entry in data['entries']:
                    display_name = entry['name'].encode('utf-8', 'backslashreplace').decode()
                    item = tree.insert('', 'end', text=display_name, values=(entry['type'], entry['bytes']))
                    entry_names[item] = entry['name']
                listed_path, next_offset = requested_path, data['next_offset']
                path.set(listed_path)
            self.start_guest_command(listing_script(requested_path, offset), 'List guest directory',
                                     callback=receive)

        def next_page():
            if next_offset is not None:
                path.set(listed_path)
                refresh(next_offset)

        def enter(event=None):
            selection = tree.selection()
            if not selection:
                return
            item = tree.item(selection[0])
            name, kind = entry_names[selection[0]], item['values'][0]
            if any(0xd800 <= ord(character) <= 0xdfff for character in name):
                raise ValueError('This filename is not valid UTF-8; use a guest shell to operate on it')
            guest = str(Path(listed_path) / name)
            if kind == 'd':
                path.set(guest)
                refresh()
            else:
                target = filedialog.asksaveasfilename(parent=window, initialfile=name)
                if target:
                    self.start_transfer('download', Path(target), guest, f'Download {name}')

        ttk.Button(top, text='Refresh', command=lambda: self.guard(refresh)).pack(side='left', padx=4)
        ttk.Button(top, text='Up', command=lambda: (path.set(str(Path(path.get()).parent)), self.guard(refresh))).pack(side='left')
        ttk.Button(top, text='Next page', command=lambda: self.guard(next_page)).pack(side='left', padx=4)
        ttk.Button(window, text='Open folder / Download selected file', command=lambda: self.guard(enter)).pack(pady=6)
        tree.bind('<Double-1>', lambda event: self.guard(enter))
        refresh()

    def tasks(self):
        tasks = load_tasks(ROOT / 'uconsole-tasks.json')
        approved = {item['name']: item for item in self.job_controller().call(
            'host_tasks', {'workspace': 'gui'})['tasks']}
        choices = [('guest', name, item) for name, item in tasks.items() if item.get('kind') == 'guest']
        choices += [('host', name, item) for name, item in approved.items()]
        choices += [('unapproved host', name, {}) for name, item in tasks.items()
                    if item.get('kind') == 'host' and name not in approved]
        window = tk.Toplevel(self.root)
        window.title('Agent tasks')
        window.geometry('680x420')
        names = tk.Listbox(window, exportselection=False, height=7)
        for kind, name, _ in choices:
            names.insert('end', f'{name} [{kind}]')
        names.pack(fill='x', padx=6, pady=6)
        output = tk.Text(window, wrap='word', font='TkFixedFont')
        output.pack(fill='both', expand=True, padx=6)

        def run_selected():
            selection = names.curselection()
            if not selection:
                raise ValueError('Choose a task')
            kind, name, task = choices[selection[0]]
            def receive(result):
                if window.winfo_exists():
                    output.delete('1.0', 'end')
                    output.insert('1.0', json.dumps(result, indent=2))
            if kind == 'guest':
                self.start_guest_command(task['script'], f'Task {name}',
                                         timeout=task.get('timeout', 60), callback=receive)
            elif kind == 'host':
                self.start_host_task(name, callback=receive)
            else:
                raise ValueError('Repository host-task definitions are not execution authority. '
                                 'Restart with --host-task-policy and its reviewed --host-task-policy-sha256.')

        def describe(event=None):
            if names.curselection():
                kind, name, task = choices[names.curselection()[0]]
                output.delete('1.0', 'end')
                output.insert('1.0', json.dumps({'name': name, 'kind': kind, **task}, indent=2))
        names.bind('<<ListboxSelect>>', describe)

        ttk.Button(window, text='Run selected task', command=lambda: self.guard(run_selected)).pack(pady=6)

    def guard(self, action):
        try:
            action()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.report_error(getattr(action, '__name__', 'action'), exc)

    def report_error(self, action, exc):
        record = self.diagnostics.emit('action_failed', action, exc)
        details = f'{action}: {exc}\n\nDiagnostic record:\n{record}'
        details_window(self.root, 'uConsole Workbench — Error', details,
                       lambda: self.show_help('Errors and diagnostics'))

    def callback_error(self, kind, exc, traceback):
        self.report_error('Tk callback', exc.with_traceback(traceback))

    def show_schematic(self):
        from workbench_schematic_gui import Schematic
        schematic = getattr(self, 'schematic', None)
        if schematic is not None and schematic.window.winfo_exists():
            schematic.window.lift()
        else:
            self.schematic = Schematic(self.root, lambda source: self.guard(lambda: self.schematic_source(source)),
                                       runtime=lambda: self.runtime, configure=lambda: self.guard(self.show_wizard))
            self.schematic.window.bind('<F1>', lambda event: self.show_help('Live schematic'))
            self.schematic.help_hints = attach_hints(self.schematic.window, self.show_help)

    def schematic_source(self, source):
        if self.editor.edit_modified() and not messagebox.askyesno(
                'Unsaved edits', 'Discard unsaved editor changes?', parent=self.root):
            return
        path, line, text = source.resolve(ROOT)
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', text)
        # Diagram sources are inspection copies. Saving prompts for a project
        # destination rather than writing into the installed application.
        self.filename = None
        self.editor.edit_modified(False)
        self.editor.mark_set('insert', f'{line}.0')
        self.editor.see(f'{line}.0')
        self.editor.tag_remove('sel', '1.0', 'end')
        self.editor.tag_add('sel', f'{line}.0', f'{line}.end')
        self.status.set(f'Inspection copy • {path}:{line} • Save chooses a destination')
        self.root.deiconify()
        self.root.lift()
        self.editor.focus_set()

    def show_wizard(self):
        from workbench_wizard import Wizard
        wizard = getattr(self, 'setup_wizard', None)
        if wizard is not None and wizard.window.winfo_exists():
            wizard.window.lift()
        else:
            self.setup_wizard = Wizard(self)

    def show_setup(self):
        from workbench_setup import Setup
        panel = getattr(self, 'setup_panel', None)
        if panel is not None and panel.window.winfo_exists():
            panel.refresh()
            panel.window.lift()
        else:
            self.setup_panel = Setup(self, ROOT, BUILD_ROOT)

    def show_help(self, topic='Getting started'):
        guide = getattr(self, 'guide', None)
        if guide is not None and guide.window.winfo_exists():
            guide.query.set('')
            guide.show(topic)
            guide.window.lift()
        else:
            setup = (f'UCONSOLE_BUILD_DIR={shlex.quote(str(BUILD_ROOT))} '
                     f'{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / "tools/build_emulator_qemu.py"))}')
            self.guide = Guide(self.root, topic, setup_command=setup)

    def show_diagnostics(self):
        details_window(self.root, 'Workbench diagnostics',
                       f'Log: {self.diagnostics.path}\n\n{self.diagnostics.recent()}',
                       lambda: self.show_help('Errors and diagnostics'))

    def panel_help(self, window, topic):
        window.bind('<F1>', lambda event: self.show_help(topic))
        ttk.Button(window, text='Help (F1)', command=lambda: self.show_help(topic)).pack(anchor='e', padx=8, pady=4)

    def import_image(self):
        if self.workspace.exists():
            raise ValueError(f'Import requires a new workspace directory; {self.workspace} already exists. '
                             'Restart with --workspace /path/to/new-workspace. Existing data is never replaced.')
        source = filedialog.askopenfilename(parent=self.root, title='Import supported CM4 guest image',
                                           filetypes=[('CM4 images', '*.img *.bz2'), ('All files', '*')])
        if not source:
            return
        checksum = simpledialog.askstring('Verify image',
            'Expected SHA-256 (from a trusted source).\nLeave blank only for the pinned official .img.bz2 image.', parent=self.root)
        if checksum is None:
            return
        arguments = ['prepare', source]
        if checksum.strip():
            value = checksum.strip().lower()
            if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
                raise ValueError('Expected SHA-256 must contain exactly 64 hexadecimal characters')
            arguments.extend(['--sha256', value])
        elif not source.endswith('.bz2'):
            raise ValueError('A raw/custom image requires an independently verified SHA-256.')
        self.start_lifecycle(arguments, 'Import')

    def append(self, text):
        self.console.feed(text.replace('\r\n', '\n').replace('\n', '\r\n'))

    def scenario_editable(self):
        if self.agent_job is not None:
            raise ValueError('Wait for the attached agent job to finish')
        if self.boot_job is not None:
            raise ValueError('Wait for boot cleanup before changing its power profile')
        if self.process is not None or self.display_setup is not None or self.lifecycle is not None or self.host_job is not None:
            raise ValueError('Stop the emulator and finish image operations before changing its power profile')

    def load_scenario(self):
        self.scenario_editable()
        name = filedialog.askopenfilename(parent=self.root, title='Load initial power profile',
                                         filetypes=[('JSON profile', '*.json'), ('All files', '*')])
        if not name:
            return
        scenario = Scenario.load(name)
        self.initial_scenario = scenario
        self.scenario_label.set(f'Next boot: {Path(name).name} • SHA-256 {scenario.sha256[:12]} • snapshot')
        self.append('Power profile loaded as an immutable snapshot; reload to use later file edits.\n'
                    + json.dumps({'sha256': scenario.sha256, 'power': scenario.power.__dict__}, indent=2) + '\n')

    def clear_scenario(self):
        self.scenario_editable()
        self.initial_scenario = None
        self.scenario_label.set('Next boot: default power state')

    def start(self):
        panel = getattr(self, 'setup_panel', None)
        if panel is not None and panel.window.winfo_exists() and panel.flow_active:
            panel.window.lift()
            return
        self.check_startable()
        self.show_setup()
        self.setup_panel.begin()

    def check_startable(self):
        if self.agent_job is not None:
            raise ValueError('Wait for the attached agent job to finish')
        if self.boot_job is not None:
            raise ValueError('Wait for boot to finish, or cancel it and wait for cleanup')
        if self.host_job is not None:
            raise ValueError('Wait for the host task to finish, or cancel it and wait for cleanup')
        if self.lifecycle is not None:
            raise ValueError('Wait for the image operation to finish')
        if self.process is not None:
            raise ValueError('This workbench already owns a running emulator')
        if self.display_setup is not None:
            raise ValueError('Surrogate desktop setup is already running')

    def _start_prepared(self):
        self.check_startable()
        if not (self.workspace / 'machine.json').is_file():
            self.show_setup()
            return
        from workbench_emulator import selected
        if selected(ROOT, BUILD_ROOT) is None:
            self.show_setup()
            return
        config = json.loads((self.workspace / 'machine.json').read_text())
        if (self.mode.get() == 'desktop' and
                config.get('surrogate_desktop', {}).get('schema') != SURROGATE_SCHEMA):
            self.start_lifecycle(['configure-display', '--serial-port', str(self.serial_port),
                                  '--qmp-port', str(self.qmp_port)], 'Desktop preparation')
            self.display_setup = self.lifecycle
            self.start_after_setup = True
            self.status.set('Preparing this overlay for the surrogate desktop')
            return
        args = parser().parse_args(['--workspace', str(self.workspace), 'run',
                                    '--mode', self.mode.get(), '--serial-port', str(self.serial_port),
                                    '--qmp-port', str(self.qmp_port), '--display', self.display.get(),
                                    '--keyboard', self.keyboard.get(), '--audio', self.audio.get(),
                                    '--adc-reference', self.adc_reference.get(), '--modem', self.modem.get()])
        try:
            self.launch_locked(args)
        except BaseException:
            self.release()
            raise

    def launch_locked(self, args):
        submitted = self.job_controller().submit_boot('gui', args.mode, self.initial_scenario,
            display=args.display, qmp_port=args.qmp_port, serial_port=args.serial_port,
            keyboard=args.keyboard, audio=args.audio, adc_reference=args.adc_reference,
            modem=getattr(args, 'modem', 'none'))
        self.boot_job = submitted['job_id']
        self.active_mode = args.mode
        self.shutdown_requested = False
        self.decoder.reset()
        self.console.reset_terminal()
        self.log = None
        self.status.set(f'Booting {self.active_mode} • job {self.boot_job}')
        if self.active_mode == 'maintenance':
            self.append('Maintenance starts a root shell, not the desktop. Kernel messages may hide the prompt; '
                        'press Enter in the serial input field to redraw it. Display none opens no graphics window.\n')
        self.append(f'Boot job {self.boot_job}; durable history: {self.lifecycle_path}\n')

    def keyboard_deck(self):
        from forge_keyboard_gui import KeyboardDeck
        existing = getattr(self, 'deck', None)
        if existing and existing.window.winfo_exists():
            existing.window.lift()
            return
        self.deck = KeyboardDeck(self.root, self.job_controller(), 'gui')
        self.panel_help(self.deck.window, 'Power and device controls')

    def cancel_boot(self):
        if self.boot_job is None:
            raise ValueError('No boot job is active')
        result = self.controller.cancel(self.boot_job)
        self.append(result['detail'] + '\n')
        if result['requested']:
            self.status.set('Boot cancellation requested; waiting for owned VM cleanup')

    def poll_boot(self):
        if self.boot_job is None:
            return
        result = self.controller.job(self.boot_job)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        self.boot_job = None
        # A failed boot can retain a live VM when cleanup itself failed. Keep
        # that exact controller-owned Runtime visible; never adopt another VM.
        runtime = self.controller.runtimes.get('gui')
        if runtime and runtime.process is not None:
            self.runtime, self.process = runtime, runtime.process
        self.status.set(f'Boot {result["status"]} • job {result["job_id"]}')
        self.append(json.dumps(result, indent=2) + '\n')
        if result['status'] == 'completed' and 'scenario' in result['result']:
            self.append('Verified initial power profile: ' + json.dumps(result['result']['scenario']) + '\n')

    def require_no_replay(self):
        if self.agent_job is not None:
            raise ValueError('Wait for the attached agent job to finish')
        if self.control_job is not None:
            raise ValueError('Wait for pause/resume readback to finish')
        if self.power_job is not None:
            raise ValueError('Wait for the power operation to finish')
        if self.boot_job is not None:
            raise ValueError('Wait for boot to finish, or cancel it and wait for cleanup')
        if self.host_job is not None:
            raise ValueError('Wait for the host task to finish, or cancel it and wait for cleanup')
        if self.guest_job is not None:
            raise ValueError('Wait for the guest command to finish, or cancel it and wait for cleanup')
        if self.transfer is not None:
            raise ValueError('Wait for the guest transfer to finish, or cancel it and wait for cleanup')
        if self.replay_future is not None:
            raise ValueError('Wait for power replay to finish, or cancel it and wait for confirmation')

    def start_replay(self):
        self.require_no_replay()
        if self.runtime is None or self.process is None or self.process.poll() is not None:
            raise ValueError('Start an emulator in this workbench first')
        if self.shutdown_requested:
            raise ValueError('Wait for shutdown before replaying power events')
        name = filedialog.askopenfilename(parent=self.root, title='Run host-clock power schedule',
                                         filetypes=[('JSON schedule', '*.json'), ('All files', '*')])
        if not name:
            return
        replay = Replay.load(name)
        controller = self.job_controller()
        controller.runtimes['gui'] = self.runtime
        submitted = controller.submit_replay('gui', replay)
        self.replay_job = submitted['job_id']
        self.replay_future = controller.jobs[self.replay_job][2]
        self.replay_evidence = Path(controller.job(self.replay_job)['context']['evidence_path'])
        self.replay_status.set(f'Running {Path(name).name} • {len(replay.events)} events • {replay.sha256[:12]}')
        self.append(f'Power replay job {self.replay_job}; evidence: {self.replay_evidence}\n')

    def cancel_replay(self):
        if self.replay_future is None:
            raise ValueError('No power replay is active')
        result = self.controller.cancel(self.replay_job)
        self.append(result['detail'] + '\n')
        if result['requested']:
            self.replay_status.set('Cancellation requested; waiting for in-flight readback; no rollback')

    def poll_replay(self):
        if self.replay_job is None:
            return
        result = self.controller.job(self.replay_job)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        if result['status'] == 'completed':
            self.replay_status.set(f'Replay completed: {result["result"]["completed_events"]} events')
        elif result['status'] == 'cancelled':
            self.replay_status.set('Replay cancelled; completed events remain applied')
        else:
            self.replay_status.set('Replay failed; effects may remain: ' + result.get('error', 'unknown error'))
        self.append(self.replay_status.get() + f' • {self.replay_evidence}\n' + json.dumps(result, indent=2) + '\n')
        self.replay_future = self.replay_job = None

    def live_power(self, change=False):
        self.require_no_replay()
        if self.runtime is None or self.process is None or self.process.poll() is not None:
            raise ValueError('Start an emulator in this workbench first')
        if self.shutdown_requested:
            raise ValueError('Wait for shutdown before changing or sampling power')
        requested = None
        if change:
            field = self.power_field.get()
            value = json.loads(self.power_value.get())
            validate_value(field, value)
            requested = {field: value}
        controller = self.job_controller()
        controller.runtimes['gui'] = self.runtime
        submitted = controller.call('power_set' if change else 'power_query',
                                    {'workspace': 'gui', **(requested or {})})
        self.power_job = submitted['job_id']
        self.status.set(f'Power operation submitted • job {self.power_job}')
        self.append(f'Power job {self.power_job}; durable history: {self.lifecycle_path}\n')
        return submitted

    def poll_power(self):
        if self.power_job is None:
            return
        result = self.controller.job(self.power_job)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        self.power_job = None
        self.status.set(f'Power operation {result["status"]} • job {result["job_id"]}')
        self.append(json.dumps(result, indent=2) + '\n')
        if result['status'] != 'completed':
            self.append('Power effects may remain; no rollback is implied.\n')

    def control(self, operation):
        self.require_no_replay()
        if self.process is None:
            raise ValueError('Start an emulator in this workbench first')
        if operation not in ('stop', 'cont'):
            raise ValueError('Only pause/resume is supported by this control')
        controller = self.job_controller()
        controller.runtimes['gui'] = self.runtime
        submitted = controller.call('pause' if operation == 'stop' else 'resume', {'workspace': 'gui'})
        self.control_job = submitted['job_id']
        self.status.set(f'Pause/resume submitted • job {self.control_job}')
        return submitted

    def poll_control(self):
        if self.control_job is None:
            return
        result = self.controller.job(self.control_job)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        self.control_job = None
        if result['status'] == 'completed':
            label = 'Running' if result['result']['observed']['running'] else 'Paused'
        else:
            label = 'Pause/resume ' + result['status']
        self.status.set(label)
        self.append(json.dumps(result, indent=2) + '\n')
        if result['status'] != 'completed':
            self.append('Run-state effects may remain; inspect the VM before retrying.\n')

    def send(self):
        self.require_guest_idle()
        if self.serial is None:
            raise ValueError('Serial console is not connected yet')
        self.serial.sendall((self.entry.get() + '\n').encode())
        self.entry.delete(0, 'end')

    def send_terminal(self, data):
        self.require_guest_idle()
        if self.serial is None:
            raise ValueError('Serial console is not connected yet')
        self.serial.sendall(data)

    def terminal_reply(self, data):
        # Device-status replies are allowed only on the current idle serial
        # connection, never while an agent owns it or during offline replay.
        try:
            self.require_guest_idle()
            if self.serial is not None:
                self.serial.sendall(data)
        except (ValueError, OSError):
            pass

    def poweroff(self):
        self.require_no_replay()
        self.require_guest_idle()
        if self.process is None:
            return
        if self.active_mode == 'maintenance':
            controller = self.job_controller()
            controller.runtimes['gui'] = self.runtime
            self.release_serial()
            submitted = controller.call('stop', {'workspace': 'gui', 'force': False})
            self.guest_job = submitted['job_id']
            self.guest_label, self.guest_callback = 'Clean shutdown', None
            self.shutdown_requested = True
            self.status.set(f'Clean shutdown submitted • job {self.guest_job}')
            self.append(f'Clean shutdown job {self.guest_job}; durable history: {self.lifecycle_path}\n'
                        'Waiting for read-only root and VM exit; running shutdown cannot be cancelled.\n')
        else:
            messagebox.showinfo('Clean shutdown', 'Log in and run sudo poweroff in the console. QEMU exits when the guest shuts down.', parent=self.root)

    def export(self):
        if self.process is not None:
            raise ValueError('Shut down and close the emulator before exporting')
        target = filedialog.asksaveasfilename(parent=self.root, defaultextension='.img', title='Export SD image to a new file')
        if target:
            self.start_lifecycle(['export', target], 'Export')

    def start_lifecycle(self, arguments, label):
        if self.agent_job is not None:
            raise ValueError('Wait for the attached agent job to finish')
        if self.boot_job is not None:
            raise ValueError('Wait for boot to finish, or cancel it and wait for cleanup')
        if self.process is not None or self.display_setup is not None or self.lifecycle is not None or self.host_job is not None:
            raise ValueError('Stop the guest and wait for existing operations before changing the image')
        submitted = self.job_controller().submit_lifecycle('gui', arguments[0], arguments[1:])
        self.lifecycle = submitted['job_id']
        self.lifecycle_label = label
        self.status.set(f'{label} submitted • job {self.lifecycle}')
        self.append(f'{label} job {self.lifecycle}; durable history: {self.lifecycle_path}\n')

    def recovery_controls(self):
        if self.recovery_panel is not None and self.recovery_panel.window.winfo_exists():
            self.recovery_panel.window.lift()
            return self.recovery_panel
        from forge_recovery_gui import RecoveryPanel
        self.recovery_panel = RecoveryPanel(self.root, self.job_controller(), 'gui')
        self.panel_help(self.recovery_panel.window, 'Physical target and recovery')
        return self.recovery_panel

    def physical_target(self):
        if self.target_panel is not None and self.target_panel.window.winfo_exists():
            self.target_panel.window.lift()
            return self.target_panel
        from forge_target_gui import TargetPanel
        self.target_panel = TargetPanel(self.root, self.job_controller(), 'gui')
        self.panel_help(self.target_panel.window, 'Physical target and recovery')
        return self.target_panel

    def modem_controls(self):
        if self.modem_panel is not None and self.modem_panel.window.winfo_exists():
            self.modem_panel.window.lift()
            return self.modem_panel
        from forge_modem_gui import ModemPanel
        self.modem_panel = ModemPanel(self.root, self.job_controller(), 'gui')
        self.panel_help(self.modem_panel.window, 'Power and device controls')
        return self.modem_panel

    def audio_controls(self):
        if self.audio_panel is not None and self.audio_panel.window.winfo_exists():
            self.audio_panel.window.lift()
            return self.audio_panel
        from forge_audio_gui import AudioPanel
        self.audio_panel = AudioPanel(self.root, self.job_controller(), 'gui')
        self.panel_help(self.audio_panel.window, 'Power and device controls')
        return self.audio_panel

    def job_controller(self):
        if self.controller is None:
            from forge_keyboard import default_oracle
            self.lifecycle_path = history_default_path()
            grants = ('image-write', 'transfer', 'guest-exec', 'boot', 'force-stop', 'device-control')
            if self.host_task_policy:
                grants += ('host-task',)
            if self.target_policy:
                grants += ('target-write',)
            if self.recovery_policy:
                grants += ('target-recovery',)
            self.controller = Controller({'gui': self.workspace}, grants=grants,
                                         files_root=self.agent_files_root,
                                         history=self.lifecycle_path,
                                         host_task_policy=self.host_task_policy,
                                         host_task_sha256=self.host_task_sha256,
                                         target_policy=self.target_policy,
                                         target_policy_sha256=self.target_policy_sha256,
                                         recovery_policy=self.recovery_policy,
                                         recovery_policy_sha256=self.recovery_policy_sha256,
                                         keyboard_oracle=default_oracle(ROOT, BUILD_ROOT))
        return self.controller

    def start_host_task(self, name, callback=None):
        self.require_no_replay()
        self.require_guest_idle()
        if self.display_setup is not None or self.lifecycle is not None:
            raise ValueError('Wait for display setup and image operations before running a host task')
        submitted = self.job_controller().call('host_task', {'workspace': 'gui', 'task': name})
        self.host_job = submitted['job_id']
        self.host_label, self.host_callback = f'Host task {name}', callback
        self.status.set(f'{self.host_label} submitted • job {self.host_job}')
        self.append(f'{self.host_label} job {self.host_job}; durable history: {self.lifecycle_path}\n')

    def cancel_host_task(self):
        if self.host_job is None:
            raise ValueError('No host task is active')
        result = self.controller.cancel(self.host_job)
        self.append(result['detail'] + '\n')
        if result['requested']:
            self.status.set(f'{self.host_label} cancellation requested; waiting for cleanup')

    def poll_host_task(self):
        if self.host_job is None:
            return
        result = self.controller.job(self.host_job)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        callback = self.host_callback
        self.host_job = self.host_callback = None
        outcome = result['status']
        if outcome == 'completed':
            code = result['result']['exit_code']
            outcome = 'complete' if code == 0 else f'command failed (exit {code})'
        self.status.set(f'{self.host_label} {outcome} • job {result["job_id"]}')
        self.append(json.dumps(result, indent=2) + '\n')
        if callback and result['status'] == 'completed':
            self.guard(lambda: callback(result['result']))

    def require_guest_idle(self):
        if self.agent_job is not None:
            raise ValueError('Wait for the attached agent job to finish')
        if self.control_job is not None:
            raise ValueError('Wait for pause/resume readback to finish')
        if self.power_job is not None:
            raise ValueError('Wait for the power operation to finish')
        if self.boot_job is not None:
            raise ValueError('Wait for boot to finish, or cancel it and wait for cleanup')
        if self.host_job is not None:
            raise ValueError('Wait for the host task to finish, or cancel it and wait for cleanup')
        if self.guest_job is not None:
            raise ValueError('Wait for the guest command to finish, or cancel it and wait for cleanup')
        if self.transfer is not None:
            raise ValueError('Wait for the guest transfer to finish, or cancel it and wait for cleanup')
        if self.shutdown_requested:
            raise ValueError('Wait for the pending shutdown to finish')
        if self.runtime and self.runtime.guest_channel_error:
            raise ValueError('Guest serial channel is uncertain; inspect the error before explicit forced stop/restart')

    def start_guest_command(self, script, label, timeout=60, callback=None):
        self.require_no_replay()
        self.require_guest_idle()
        if self.runtime is None or self.process is None or self.active_mode != 'maintenance':
            raise ValueError('Start maintenance mode before executing guest commands')
        self.runtime.require_alive()
        controller = self.job_controller()
        controller.runtimes['gui'] = self.runtime
        self.release_serial()
        submitted = controller.submit_guest('gui', script, timeout)
        self.guest_job = submitted['job_id']
        self.guest_label, self.guest_callback = label, callback
        self.status.set(f'{label} submitted • job {self.guest_job}')
        self.append(f'{label} job {self.guest_job}; durable history: {self.lifecycle_path}\n')

    def cancel_guest(self):
        if self.guest_job is None:
            raise ValueError('No guest command job is active')
        result = self.controller.cancel(self.guest_job)
        self.append(result['detail'] + '\n')
        if result['requested']:
            self.status.set(f'{self.guest_label} cancellation requested; waiting for cleanup')

    def poll_guest(self):
        if self.guest_job is None:
            return
        result = self.controller.job(self.guest_job)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        callback = self.guest_callback
        self.guest_job = self.guest_callback = None
        if result['operation'] == 'stop':
            self.shutdown_requested = False
        outcome = result['status']
        if outcome == 'completed':
            code = result['result'].get('exit_code', 0)
            outcome = 'complete' if code == 0 else f'command failed (exit {code})'
        self.status.set(f'{self.guest_label} {outcome} • job {result["job_id"]}')
        self.append(json.dumps(result, indent=2) + '\n')
        if callback and result['status'] == 'completed':
            self.guard(lambda: callback(result['result']))

    def start_transfer(self, direction, host, guest, label):
        self.require_no_replay()
        self.require_guest_idle()
        if self.runtime is None or self.process is None or self.active_mode != 'maintenance':
            raise ValueError('Start maintenance mode before transferring files')
        self.runtime.require_alive()
        controller = self.job_controller()
        # This is the Runtime created and owned by this GUI, not a discovered
        # process or another client's VM. Sharing it preserves serial mutex and
        # uncertain-channel state across GUI transfers and other runtime calls.
        controller.runtimes['gui'] = self.runtime
        self.release_serial()
        submitted = controller.submit_transfer('gui', direction, host, guest)
        self.transfer = submitted['job_id']
        self.transfer_label = label
        self.status.set(f'{label} submitted • job {self.transfer}')
        self.append(f'{label} job {self.transfer}; durable history: {self.lifecycle_path}\n')

    def cancel_transfer(self):
        if self.transfer is None:
            raise ValueError('No guest transfer is active')
        result = self.controller.cancel(self.transfer)
        self.append(result['detail'] + '\n')
        if result['requested']:
            self.status.set(f'{self.transfer_label} cancellation requested; waiting for cleanup')

    def poll_transfer(self):
        if self.transfer is None:
            return
        result = self.controller.job(self.transfer)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        self.transfer = None
        outcome = 'complete' if result['status'] == 'completed' else result['status']
        self.status.set(f'{self.transfer_label} {outcome} • job {result["job_id"]}')
        self.append(json.dumps(result, indent=2) + '\n')
        if result['status'] != 'completed':
            self.append('Inspect transfer effects before retrying; cancellation or failure is not a rollback.\n')

    def cancel_lifecycle(self):
        if self.lifecycle is None:
            raise ValueError('No image job is active')
        result = self.controller.cancel(self.lifecycle)
        self.append(result['detail'] + '\n')
        if result['requested']:
            self.status.set(f'{self.lifecycle_label} cancellation requested; waiting for cleanup')

    def poll_lifecycle(self):
        if self.lifecycle is None:
            return
        result = self.controller.job(self.lifecycle)
        report_job_error(self.root, result)
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            return
        desktop_setup = self.display_setup == self.lifecycle
        self.lifecycle = None
        pending = desktop_setup and self.start_after_setup
        if desktop_setup:
            self.display_setup = None
            self.start_after_setup = False
        outcome = 'complete' if result['status'] == 'completed' else result['status']
        self.status.set(f'{self.lifecycle_label} {outcome} • job {result["job_id"]}')
        self.append(json.dumps(result, indent=2) + '\n')
        if result['status'] == 'cancelled':
            self.append('Cancellation does not undo committed image changes. Inspect state before retrying.\n')
        panel = getattr(self, 'setup_panel', None)
        if pending and result['status'] == 'completed' and not (panel and panel.flow_active):
            self.guard(self.start)

    def checkpoint(self):
        name = simpledialog.askstring('Create checkpoint', 'Name for a new checkpoint:', parent=self.root)
        if name:
            self.start_lifecycle(['checkpoint', name], 'Checkpoint')

    def restore_checkpoint(self):
        directory = self.workspace / 'checkpoints'
        names = sorted(path.name for path in directory.iterdir()
                       if (path / 'checkpoint.json').is_file() and not path.name.startswith('.')) if directory.exists() else []
        if not names:
            raise ValueError('This workspace has no completed checkpoints')
        name = simpledialog.askstring('Restore checkpoint',
                                      'Checkpoint name (available: ' + ', '.join(names) + '):', parent=self.root)
        if name and messagebox.askyesno('Restore working image',
                f'Restore {name}? Current image state will be saved in a safety checkpoint first.', parent=self.root):
            self.start_lifecycle(['restore', name], 'Restore')

    def recover_image(self):
        self.start_lifecycle(['recover'], 'Recovery')

    def refresh_image(self):
        self.start_lifecycle(['refresh-boot'], 'Boot refresh')

    def open_file(self):
        if self.editor.edit_modified() and not messagebox.askyesno('Unsaved edits', 'Discard unsaved editor changes?', parent=self.root):
            return
        name = filedialog.askopenfilename(parent=self.root, initialdir=ROOT)
        if name:
            self.load_file(Path(name))

    def load_file(self, filename):
        text = filename.read_text()
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', text)
        self.filename = filename
        self.editor.edit_modified(False)
        self.status.set(str(self.filename))

    def save_file(self):
        if self.filename is None:
            name = filedialog.asksaveasfilename(parent=self.root, initialdir=ROOT)
            if not name:
                return
            self.filename = Path(name)
        self.filename.write_text(self.editor.get('1.0', 'end-1c'))
        self.editor.edit_modified(False)
        self.status.set(f'Saved {self.filename}')

    def copy_to_guest(self):
        if self.process is None or self.active_mode != 'maintenance' or self.transfer:
            raise ValueError('Start maintenance mode and wait for any previous copy to finish')
        self.save_file()
        if self.filename is None:
            return
        destination = simpledialog.askstring('Copy file into image', 'Guest absolute path (replaces existing file):',
                                             initialvalue='/root/' + self.filename.name, parent=self.root)
        if destination:
            self.start_transfer('upload', self.filename, destination, 'Upload')

    def cancel_poll(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self.poll_timer is not None:
            self.root.after_cancel(self.poll_timer)
            self.poll_timer = None

    def poll(self):
        self.poll_timer = None
        self.poll_agent()
        if self.agent_queue:
            self.agent_queue.drain()
        self.poll_boot()
        self.poll_power()
        self.poll_control()
        self.poll_replay()
        self.poll_lifecycle()
        self.poll_guest()
        self.poll_host_task()
        self.poll_transfer()
        if self.process is not None:
            if self.log is None and (self.workspace / 'serial.log').exists():
                self.log = (self.workspace / 'serial.log').open('rb')
            if self.log:
                text = self.decoder.decode(self.log.read(65536))
                self.console.feed(text, reply=self.terminal_reply if self.serial is not None else None)
            if (self.serial is None and self.process.poll() is None and self.transfer is None
                    and self.agent_job is None
                    and self.guest_job is None
                    and not self.runtime.guest_channel_error):
                try:
                    self.serial = self.runtime.connect_serial()
                    self.serial.setblocking(False)
                    self.status.set(f'Running {self.active_mode} • serial connected')
                except OSError:
                    pass
            if self.serial:
                try:
                    # Output is displayed from the complete on-disk serial log.
                    self.serial.recv(65536)
                except BlockingIOError:
                    pass
                except OSError:
                    self.serial.close()
                    self.serial = None
            if self.process.poll() is not None:
                if self.replay_future is not None:
                    self.controller.cancel(self.replay_job)
                elif self.transfer is not None or self.guest_job is not None or self.power_job is not None or self.control_job is not None or self.agent_job is not None:
                    pass  # Keep the Runtime until its transfer worker finishes cleanup.
                else:
                    code = self.process.returncode
                    if code:
                        self.diagnostics.emit('emulator_exited', 'QEMU',
                                              f'Exit status {code}; inspect {self.workspace / "workbench-qemu.log"}')
                    self.release()
                    self.status.set(f'Stopped (exit {code}); see workbench-qemu.log')
        self.poll_timer = self.root.after(100, self.poll)

    def release(self):
        for resource in (self.serial, self.log, self.errors):
            if resource:
                resource.close()
        self.process = self.serial = self.log = self.errors = None
        if self.runtime:
            if self.controller and self.controller.runtimes.get('gui') is self.runtime:
                self.controller.runtimes.pop('gui')
            self.runtime.release()
            self.runtime = None

    def close(self):
        panel = getattr(self, 'setup_panel', None)
        if panel is not None and panel.flow_active:
            panel.window.lift()
            panel.cancel_flow()
            return
        if panel is not None and panel.process is not None:
            panel.window.lift()
            panel.cancel()
            return
        if self.recovery_panel is not None and self.recovery_panel.job is not None:
            messagebox.showinfo('Recovery job in progress',
                                'Keep Workbench open until the recovery job reaches a known terminal state.', parent=self.root)
            return
        if self.modem_panel is not None and self.modem_panel.job is not None:
            self.status.set('Wait for the modem operation to finish before closing.')
            return
        if self.audio_panel is not None and self.audio_panel.job is not None:
            self.status.set('Wait for the audio operation to finish before closing.')
            return
        if self.target_panel is not None and self.target_panel.job is not None:
            messagebox.showinfo('Physical target job in progress',
                                'Wait for target acknowledgement; closing does not roll back hardware changes.', parent=self.root)
            return
        if self.agent_job is not None:
            messagebox.showinfo('Agent job in progress', 'Wait for the attached agent job to finish before closing.', parent=self.root)
            return
        if self.control_job is not None:
            messagebox.showinfo('Pause/resume in progress', 'Wait for run-state readback before closing.', parent=self.root)
            return
        if self.power_job is not None:
            messagebox.showinfo('Power operation in progress',
                                'Wait for power readback to finish before closing; effects are not rolled back.', parent=self.root)
            return
        if self.boot_job is not None:
            messagebox.showinfo('Boot in progress',
                                'Wait for boot, or cancel it and wait for VM cleanup before closing.', parent=self.root)
            return
        if self.host_job is not None:
            messagebox.showinfo('Host task in progress',
                                'Wait for completion, or cancel the host task and wait for cleanup before closing.', parent=self.root)
            return
        if self.guest_job is not None:
            messagebox.showinfo('Guest command in progress',
                                'Wait for completion, or cancel the command and wait for cleanup before closing.', parent=self.root)
            return
        if self.replay_future is not None:
            messagebox.showinfo('Power replay in progress',
                                'Cancel replay and wait for confirmation before closing.', parent=self.root)
            return
        if self.lifecycle is not None:
            messagebox.showinfo('Image operation in progress',
                                'Wait for completion, or cancel the image job and wait for cleanup before closing.', parent=self.root)
            return
        if self.transfer is not None or self.display_setup is not None:
            operation = 'display setup' if self.display_setup is not None else 'file copy'
            messagebox.showinfo('Operation in progress', f'Wait for the {operation} to finish before closing.', parent=self.root)
            return
        if self.editor.edit_modified() and not messagebox.askyesno('Unsaved edits', 'Close and discard unsaved editor changes?', parent=self.root):
            return
        schematic = getattr(self, 'schematic', None)
        if schematic is not None and schematic.window.winfo_exists() and not schematic.can_close():
            return
        if self.process is not None:
            if not messagebox.askyesno('Stop QEMU', 'Has the guest shut down or remounted its root filesystem read-only?\n\nStopping QEMU now removes power from the virtual machine.', parent=self.root):
                return
            try:
                self.runtime.control('quit')
                self.process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.report_error('Stop QEMU', exc)
                return
            self.release()
        self.close_attachment()
        if self.controller:
            self.controller.close()
            self.controller = None
        self.root.destroy()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    if sys.platform == 'darwin':
        # AppKit consumes this native command-line default during Tk startup.
        # Keep it in sys.argv; do not change persistent Python.app preferences.
        p.add_argument('-ApplePersistenceIgnoreState', choices=('YES', 'NO'),
                       help='macOS: ignore saved window state for this process only')
    p.add_argument('--workspace', type=Path, default=DEFAULT)
    p.add_argument('--qmp-port', type=int, default=4444)
    p.add_argument('--serial-port', type=int, default=4445)
    p.add_argument('--host-task-policy', type=Path, help='Owner-reviewed policy; tasks must bind workspace ID gui')
    p.add_argument('--host-task-policy-sha256', help='Explicitly approved SHA-256 of that policy')
    p.add_argument('--target-policy', type=Path, help='Owner-approved physical transactions; workspace ID gui')
    p.add_argument('--target-policy-sha256', help='Approved SHA-256 of physical transaction policy')
    p.add_argument('--recovery-policy', type=Path, help='Owner-approved prepared recovery jobs; workspace ID gui')
    p.add_argument('--recovery-policy-sha256', help='Approved SHA-256 of recovery policy')
    p.add_argument('--agent-socket', type=Path, help='Opt-in private Unix socket for coding agents; workspace ID gui')
    p.add_argument('--agent-allow', action='append', choices=GRANTS, default=[])
    p.add_argument('--agent-files-root', type=Path, help='Owner-approved files root for attached agents')
    args = p.parse_args()
    if bool(args.host_task_policy) != bool(args.host_task_policy_sha256):
        p.error('--host-task-policy and --host-task-policy-sha256 must be supplied together')
    if bool(args.target_policy) != bool(args.target_policy_sha256):
        p.error('--target-policy and --target-policy-sha256 must be supplied together')
    if bool(args.recovery_policy) != bool(args.recovery_policy_sha256):
        p.error('--recovery-policy and --recovery-policy-sha256 must be supplied together')
    if not args.agent_socket and (args.agent_allow or args.agent_files_root):
        p.error('--agent-allow and --agent-files-root require --agent-socket')
    try:
        root = tk.Tk()
        app = Workbench(root, args.workspace, args.qmp_port, args.serial_port,
                  host_task_policy=args.host_task_policy, host_task_sha256=args.host_task_policy_sha256,
                  target_policy=args.target_policy, target_policy_sha256=args.target_policy_sha256,
                  recovery_policy=args.recovery_policy, recovery_policy_sha256=args.recovery_policy_sha256,
                  agent_socket=args.agent_socket, agent_grants=args.agent_allow, agent_files_root=args.agent_files_root)
        from workbench_emulator import selected
        if not (app.workspace / 'machine.json').is_file() or selected(ROOT, BUILD_ROOT) is None:
            app.show_setup()
    except Exception as exc:
        Diagnostics(args.workspace).emit('startup_failed', 'initialize', exc)
        raise
    root.mainloop()


if __name__ == '__main__':
    main()
