"""Non-blocking prerequisite setup and cancellable managed emulator builds."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

from workbench_emulator import selected, fingerprint


class Setup:
    def __init__(self, app, root, build_root):
        self.app, self.root, self.build_root = app, root, build_root
        self.process = None
        self.cancelled = False
        self.flow_active = False
        self.flow_job = None
        self.flow_timer = None
        self.flow_stage = None
        self.flow_cancelled = False
        self.flow_stream = None
        self.rebuild_pending = False
        self.packages_attempted = False
        self.custom_image = tk.BooleanVar(value=False)
        self.window = tk.Toplevel(app.root)
        self.window.title('Workbench setup — emulator and guest')
        self.window.geometry('850x540')
        self.status = tk.StringVar()
        ttk.Label(self.window, textvariable=self.status, wraplength=810).pack(fill='x', padx=12, pady=12)
        ttk.Label(self.window, text='Start sets up the standard uConsole CM4 desktop automatically.\n'
                  'It checks host packages, downloads and verifies the recommended Linux image,\n'
                  'prepares the workspace, and boots. Downloads and package installs ask for approval.',
                  wraplength=810).pack(fill='x', padx=12)
        self.advanced = ttk.Frame(self.window)
        buttons = ttk.Frame(self.advanced)
        buttons.pack(fill='x')
        self.build_button = ttk.Button(buttons, text='Build / Rebuild emulator', command=lambda: app.guard(self.request_build))
        self.build_button.pack(side='left')
        self.cancel_button = ttk.Button(buttons, text='Cancel build', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left')
        self.import_button = ttk.Button(buttons, text='Import guest image', command=lambda: app.guard(app.import_image))
        self.import_button.pack(side='left')
        ttk.Button(buttons, text='Refresh status', command=self.refresh).pack(side='left')
        ttk.Button(buttons, text='Help', command=lambda: app.show_help('Getting started')).pack(side='left')
        options = ttk.Frame(self.advanced)
        options.pack(fill='x', pady=4)
        ttk.Checkbutton(options, text='Use my own Linux image instead of the recommended image',
                        variable=self.custom_image).pack(side='left')
        ttk.Button(options, text='Install host prerequisites', command=lambda: app.guard(self.request_packages)).pack(side='right')
        actions = ttk.Frame(self.window)
        actions.pack(fill='x', padx=12, pady=4)
        self.start_button = ttk.Button(actions, text='Set up and start', command=lambda: app.guard(app.start))
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Cancel startup', command=self.cancel_flow, state='disabled')
        self.stop_button.pack(side='left')
        self.close_button = ttk.Button(actions, text='Close', command=self.close)
        self.close_button.pack(side='right')
        ttk.Button(actions, text='Advanced tools', command=self.toggle_advanced).pack(side='right', padx=6)
        self.progress = ttk.Progressbar(self.window, mode='indeterminate')
        self.progress.pack(fill='x', padx=12)
        self.log = tk.Text(self.window, height=15, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True, padx=12, pady=12)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Destroy>', self.destroyed, add='+')
        self.refresh()

    def destroyed(self, event):
        if event.widget is not self.window:
            return
        if self.flow_timer is not None:
            self.window.after_cancel(self.flow_timer)
            self.flow_timer = None
        if self.flow_stream is not None:
            self.flow_stream.close()
            self.flow_stream = None

    def refresh(self):
        if self.flow_active:
            return
        directory = selected(self.root, self.build_root)
        guest = (self.app.workspace / 'machine.json').is_file()
        self.status.set(f'Emulator: {"ready" if directory else "build required"}\n'
                        f'Guest: {"prepared" if guest else "import required"} — {self.app.workspace}')

    def toggle_advanced(self):
        if self.advanced.winfo_manager():
            self.advanced.pack_forget()
        else:
            self.advanced.pack(fill='x', padx=12, pady=4, before=self.progress)
            self.append(f'Emulator: {selected(self.root, self.build_root)}\n'
                        f'Expected patch fingerprint: {fingerprint(self.root)}\n')

    def build(self):
        self.process_role = 'build'
        self.launch_process([sys.executable, '-u', str(self.root / 'tools/build_emulator_qemu.py')])

    def request_build(self):
        if self.process is not None or self.flow_active:
            return
        self.rebuild_pending = True
        self.packages_attempted = False
        self.check_packages(build=True)

    def request_packages(self):
        if self.process is not None or self.flow_active:
            return
        self.rebuild_pending = False
        self.packages_attempted = False
        self.check_packages(build=True)

    def check_packages(self, *, build):
        self.dependency_build = build
        self.process_role = 'dependency-check'
        self.launch_process([sys.executable, '-u', str(self.root / 'tools/workbench_onboarding.py'),
                             'packages', *(['--build'] if build else [])])

    def dependencies_result(self):
        import json
        from workbench_onboarding import install_command
        plan = json.loads(self.path.read_text())
        if plan['missing']:
            if self.packages_attempted:
                raise RuntimeError('Packages are still missing after installation: ' + ', '.join(plan['missing']))
            command = install_command(plan)
            privilege = ('The system will ask for administrator authorization. Workbench stays unprivileged.'
                         if plan['privileged'] else 'Homebrew will install these packages as your user.')
            consent = getattr(self, 'active_consent', None) if self.flow_active else None
            if consent is not None and not set(plan['missing']) <= consent['packages']:
                raise ValueError('Required package installation was not approved in Setup: '+', '.join(plan['missing']))
            if consent is None and not messagebox.askyesno('Install required host packages?',
                    'Workbench needs these packages and their dependencies:\n\n' + ', '.join(plan['missing']) +
                    '\n\n' + privilege + '\nExisting packages will not be removed. '
                    'Cancellation stops later startup steps, not an active package transaction.\n\nInstall now?',
                    parent=self.window):
                self.rebuild_pending = False
                if self.flow_active:
                    self.finish_flow('Package installation declined. Nothing else will start; retry when ready.')
                return
            import shlex
            self.append('Approved package command: ' + shlex.join(command) + '\n')
            self.packages_attempted = True
            self.process_role = 'packages'
            self.launch_process(command)
            self.cancel_button.configure(state='disabled')
            self.status.set('Installing approved packages. Complete the system authorization prompt if shown.')
        else:
            self.dependencies_checked = True
            if self.flow_active:
                self.advance()
            elif self.rebuild_pending:
                self.rebuild_pending = False
                self.build()
            else:
                self.append('All prerequisite packages are installed.\n')

    def download_default(self):
        from workbench_onboarding import IMAGE_NAME, IMAGE_BYTES, IMAGE_URL
        self.default_cache = self.build_root / 'emulator/downloads'
        consent = getattr(self, 'active_consent', None)
        if not (self.default_cache / IMAGE_NAME).is_file():
            consent = getattr(self, 'active_consent', None)
            if consent is not None and not consent['download']:
                raise ValueError('Recommended image download was not approved in Setup')
            if consent is None and not messagebox.askyesno('Set up the recommended uConsole desktop?',
                    f'Download the official CM4 v3.1 image ({IMAGE_BYTES / 1e9:.2f} GB) over HTTPS, '
                    'verify its pinned SHA-256, and prepare a private working image?\n\n'
                    'Allow at least 20 GiB free. Existing images and physical devices are not overwritten.\n\n'
                    f'Source: {IMAGE_URL}\n\nStart download and continue setup?', parent=self.window):
                self.finish_flow('Default-image download cancelled. Advanced tools also offers a custom image.')
                return
        self.process_role = 'download'
        self.launch_process([sys.executable, '-u', str(self.root / 'tools/workbench_onboarding.py'),
                             'download', '--cache', str(self.default_cache),
                             *(['--cache-only'] if consent is not None and not consent['download'] else [])])

    def probe(self, directory):
        self.process_role = 'probe'
        self.launch_process([sys.executable, '-u', str(self.root / 'tools/workbench_emulator.py'),
                             '--check', str(directory), self.options['display']])

    def launch_process(self, command):
        if self.process is not None:
            return
        logs = self.build_root / 'emulator/build-logs'
        logs.mkdir(parents=True, exist_ok=True)
        self.path = logs / f'build-{time.time_ns()}.log'
        self.writer = self.path.open('xb')
        env = dict(os.environ, UCONSOLE_ROOT=str(self.root), UCONSOLE_BUILD_DIR=str(self.build_root))
        try:
            self.process = subprocess.Popen(command,
                env=env, stdout=self.writer, stderr=subprocess.STDOUT, start_new_session=True)
        except BaseException:
            self.writer.close()
            raise
        self.reader = self.path.open('rb')
        self.cancelled = False
        self.stop_after_packages = False
        self.build_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.start()
        self.append(f'{self.process_role.title()} log: {self.path}\n')
        self.window.after(100, self.poll)

    def append(self, text):
        if self.flow_stream is not None:
            self.flow_stream.write(text)
            self.flow_stream.flush()
        self.log.configure(state='normal')
        self.log.insert('end', text)
        if int(self.log.index('end-1c').split('.')[0]) > 2000:
            self.log.delete('1.0', '1000.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def poll(self):
        self.append(self.reader.read(65536).decode('utf-8', errors='replace'))
        code = self.process.poll()
        if self.cancelled and time.monotonic() >= self.kill_at:
            self.signal(signal.SIGKILL)
        if code is None:
            self.window.after(100, self.poll)
            return
        if self.cancelled:
            self.signal(signal.SIGKILL)
        # Drain the final output before closing the retained log.
        self.append(self.reader.read().decode('utf-8', errors='replace'))
        self.reader.close()
        self.writer.close()
        self.process = None
        self.progress.stop()
        self.build_button.configure(state='disabled' if self.flow_active else 'normal')
        self.cancel_button.configure(state='disabled')
        self.append(f'\n{self.process_role.title()} {"cancelled" if self.cancelled else "finished"}; exit {code}. Log: {self.path}\n')
        self.refresh()
        try:
            if self.cancelled or self.stop_after_packages or (self.flow_active and self.flow_cancelled):
                self.rebuild_pending = False
                if self.flow_active:
                    self.finish_flow('Startup cancelled; no further steps will run. Completed package/image changes remain.')
            elif code:
                raise RuntimeError(f'{self.process_role} exited {code}; log: {self.path}')
            elif self.process_role == 'dependency-check':
                self.dependencies_result()
            elif self.process_role == 'packages':
                self.check_packages(build=self.dependency_build)
            elif self.flow_active:
                if self.process_role == 'probe':
                    self.emulator_checked = True
                elif self.process_role == 'download':
                    self.default_image_ready = True
                self.advance()
        except Exception as exc:
            self.rebuild_pending = False
            if self.flow_active:
                self.finish_flow(f'Setup failed: {exc}')
            self.app.report_error('Environment setup', exc)

    def signal(self, number):
        try:
            os.killpg(self.process.pid, number)
        except ProcessLookupError:
            pass

    def cancel(self):
        if self.process is not None and self.process_role == 'packages':
            self.stop_after_packages = True
            self.rebuild_pending = False
            self.append('The package transaction must finish safely. Later startup stages have been cancelled.\n')
            return
        if self.process is not None and not self.cancelled:
            self.cancelled = True
            self.kill_at = time.monotonic() + 5
            self.signal(signal.SIGTERM)
            self.append('Cancellation requested; waiting for build process cleanup.\n')

    def close(self):
        if self.flow_active:
            self.cancel_flow()
            return
        if self.process is not None:
            self.cancel()
            return
        self.window.destroy()

    def begin(self):
        if self.flow_active:
            return
        if self.process is not None:
            raise ValueError('Wait for the current emulator build, or cancel it first')
        self.active_consent = getattr(self, 'wizard_consent', None)
        self.wizard_consent = None  # Approval applies only to this run, never a later Start.
        self.flow_active = True
        self.flow_cancelled = False
        self.flow_job = None
        self.emulator_checked = False
        self.dependencies_checked = False
        self.packages_attempted = False
        self.default_image_ready = False
        self.use_custom_image = self.custom_image.get()
        logs = self.build_root / 'emulator/startup-logs'
        logs.mkdir(parents=True, exist_ok=True)
        self.flow_path = logs / f'start-{time.time_ns()}.log'
        self.flow_stream = self.flow_path.open('x', encoding='utf-8')
        self.options = {name: getattr(self.app, name).get() for name in
                        ('mode', 'display', 'keyboard', 'audio', 'adc_reference', 'modem')}
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.close_button.configure(state='disabled')
        self.build_button.configure(state='disabled')
        self.import_button.configure(state='disabled')
        self.progress.start()
        self.append(f'Startup log: {self.flow_path}\nRequested mode: {self.options["mode"]}; '
                    f'display: {self.options["display"]}\n')
        self.advance()

    def advance(self):
        if self.flow_cancelled:
            self.finish_flow('Cancelled; no further startup stages will run.')
            return
        try:
            directory = selected(self.root, self.build_root)
            if not self.dependencies_checked:
                self.stage('dependencies', 'Checking required host packages')
                self.check_packages(build=directory is None)
                return
            if directory is None:
                self.stage('emulator', 'Building the pinned patched emulator')
                self.build()
                return
            if not self.emulator_checked:
                self.stage('emulator', 'Checking emulator runtime dependencies and display support')
                self.probe(directory)
                return
            if not (self.app.workspace / 'machine.json').is_file():
                if self.app.workspace.exists():
                    raise ValueError(f'Workspace {self.app.workspace} exists but is not prepared. '
                                     'Its files were preserved. Choose a new workspace or recover the interrupted import.')
                if self.use_custom_image:
                    self.stage('image', 'Choose a Linux image; checksum verification and import will follow')
                    if self.active_consent is not None:
                        source, digest = self.active_consent['custom']
                        self.app.start_lifecycle(['prepare', source, '--sha256', digest], 'Import custom image')
                    else:
                        self.app.import_image()
                else:
                    from workbench_onboarding import IMAGE_NAME, IMAGE_SHA256
                    if not self.default_image_ready:
                        self.stage('download', 'Downloading and verifying the recommended CM4 Linux image')
                        self.download_default()
                        return
                    self.app.start_lifecycle(['prepare', str(self.default_cache / IMAGE_NAME),
                                              '--sha256', IMAGE_SHA256], 'Import recommended image')
                if self.app.lifecycle is None:
                    self.finish_flow('Image selection cancelled; nothing was started.')
                    return
                self.flow_job = self.app.lifecycle
                self.stage('image', 'Verifying and preparing the guest image')
            else:
                for name, value in self.options.items():
                    getattr(self.app, name).set(value)
                self.app._start_prepared()
                if self.app.lifecycle is not None:
                    self.flow_job = self.app.lifecycle
                    self.stage('desktop', 'Preparing the guest display and desktop')
                elif self.app.boot_job is not None:
                    self.flow_job = self.app.boot_job
                    self.stage('boot', 'Starting the emulator')
                else:
                    raise RuntimeError('Startup did not submit a preparation or boot job')
            self.flow_timer = self.window.after(100, self.poll_flow)
        except Exception as exc:
            self.finish_flow(f'Startup failed: {exc}')
            self.app.report_error('Start environment', exc)

    def stage(self, name, description):
        self.flow_stage = name
        self.progress.stop()
        self.progress.start()
        self.status.set(description)
        self.append('\n' + description + '\n')

    def poll_flow(self):
        self.flow_timer = None
        if not self.flow_active:
            return
        try:
            result = self.app.controller.job(self.flow_job)
            state = result['status']
            self.status.set(f'{self.flow_stage}: {state} • job {self.flow_job}\nLog: {self.flow_path}')
            # Workbench owns runtime adoption and cleanup. Never advance before
            # its poller has acknowledged the terminal job.
            pending = self.app.boot_job if self.flow_stage == 'boot' else self.app.lifecycle
            if state not in ('completed', 'failed', 'cancelled') or pending is not None:
                self.flow_timer = self.window.after(100, self.poll_flow)
                return
            import json
            self.append(json.dumps(result, indent=2) + '\n')
            if self.flow_cancelled or state != 'completed':
                self.finish_flow(f'Startup {"cancelled" if self.flow_cancelled else state}. '
                                 'Completed image changes are retained; cancellation is not rollback.')
            elif self.flow_stage == 'boot':
                self.finish_flow('Emulator started. You can close this window; the guest keeps running.\n'
                                 'Boot-job completion confirms emulator startup, not desktop/login readiness.')
            else:
                self.advance()
        except Exception as exc:
            self.finish_flow(f'Startup failed: {exc}')
            self.app.report_error('Start environment', exc)

    def cancel_flow(self):
        if not self.flow_active or self.flow_cancelled:
            return
        self.flow_cancelled = True
        self.app.start_after_setup = False
        self.append('Startup cancellation requested; waiting for owned-job cleanup.\n')
        if self.process is not None:
            self.cancel()
        elif self.flow_job is not None:
            self.app.controller.cancel(self.flow_job)
        else:
            self.finish_flow('Startup cancelled.')

    def finish_flow(self, message):
        process = getattr(self.app, 'process', None)
        if self.flow_cancelled and process is not None and process.poll() is None:
            message += '\nThe emulator already started and remains running. Use Power off to shut it down cleanly.'
        self.flow_active = False
        self.app.start_after_setup = False
        self.flow_job = None
        if self.flow_timer is not None:
            self.window.after_cancel(self.flow_timer)
            self.flow_timer = None
        self.progress.stop()
        self.status.set(message)
        self.append('\n' + message + '\n')
        if self.flow_stream is not None:
            self.flow_stream.close()
            self.flow_stream = None
        self.start_button.configure(state='normal')
        self.stop_button.configure(state='disabled')
        self.close_button.configure(state='normal')
        self.build_button.configure(state='normal')
        self.import_button.configure(state='normal')
