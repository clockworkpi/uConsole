"""Explicit physical-target controls over shared controller transactions."""
from workbench_diagnostics import report_ui_error, report_job_error
from workbench_layout import WrappingToolbar
import hashlib
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import uuid


class TargetPanel:
    def __init__(self, parent, controller, workspace):
        self.controller, self.workspace = controller, workspace
        self.job = None
        self.job_kind = 'transition'
        self.pending = None
        self.timer = None
        self.window = tk.Toplevel(parent)
        self.window.title('Physical SSH targets')
        self.selected = tk.StringVar()
        self.status = tk.StringVar(value='No hardware write occurs until explicitly confirmed.')
        self.build_hosts()
        listing = controller.call('target_transactions', {'workspace': workspace})
        names = [item['name'] for item in listing['transactions']]
        ttk.Label(self.window, text='Deployment plans — separate from saved hosts. Each plan stays bound to its original host.').pack(padx=12, pady=8)
        ttk.Label(self.window, text='Approved plans (all hosts):').pack(anchor='w', padx=12)
        self.choice = ttk.Combobox(self.window, textvariable=self.selected, values=names, state='readonly', width=40)
        self.choice.pack(padx=12, pady=4)
        if names:
            self.selected.set(names[0])
        self.details = tk.Text(self.window, width=88, height=13, state='disabled', wrap='word')
        self.details.pack(padx=12, pady=4)
        self.choice.bind('<<ComboboxSelected>>', lambda event: self.review())
        author_actions = WrappingToolbar(self.window)
        author_actions.pack(fill='x', padx=12, pady=4)
        self.prepare_button = ttk.Button(author_actions, text='Deploy files…', command=self.prepare_dialog)
        self.prepare_button.pack(side='left')
        self.service_prepare_button = ttk.Button(author_actions, text='Deploy service…', command=self.prepare_service_dialog)
        self.service_prepare_button.pack(side='left', padx=4)
        self.staging_review_button = ttk.Button(author_actions, text='Review recovery staging…', command=self.review_staging)
        self.staging_review_button.pack(side='left', padx=4)
        self.approve_button = ttk.Button(author_actions, text='Approve reviewed plan…', command=self.approve)
        self.approve_button.pack(side='left', padx=4)
        self.approve_button.state(['disabled'])
        actions = WrappingToolbar(self.window)
        actions.pack(fill='x', padx=12, pady=8)
        self.buttons = []
        self.inspect_button = ttk.Button(actions, text='Inspect recovery prerequisites', command=self.inspect_recovery)
        self.inspect_button.pack(side='left', padx=4)
        if not listing['execution_granted'] or not names:
            self.inspect_button.state(['disabled'])
        for direction, label in (('apply', 'Apply to hardware…'), ('restore', 'Restore hardware…')):
            button = ttk.Button(actions, text=label, command=lambda d=direction: self.start(d))
            button.pack(side='left', padx=4)
            self.buttons.append(button)
            if not listing['execution_granted'] or not names:
                button.state(['disabled'])
        self.buttons.append(self.inspect_button)
        self.reconcile_buttons = []
        for direction in ('apply', 'restore'):
            button = ttk.Button(actions, text='Reconcile staging '+direction+'…',
                                command=lambda d=direction: self.reconcile_staging(d))
            button.pack(side='left', padx=4)
            self.buttons.append(button)
            self.reconcile_buttons.append(button)
            button.state(['disabled'])
        ttk.Button(self.window, text='Recheck job status', command=self.poll).pack(padx=12, pady=4)
        ttk.Label(self.window, textvariable=self.status, wraplength=650).pack(padx=12, pady=8)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.review()

    def build_hosts(self):
        from workbench_targets import default_path, load, destination
        self.host_path = default_path()
        self.hosts = load(self.host_path)
        frame = ttk.LabelFrame(self.window, text='1. Choose or add an SSH host', padding=10)
        frame.pack(fill='x', padx=12, pady=8)
        self.host_selected = tk.StringVar()
        self.host_name = tk.StringVar()
        self.host_user = tk.StringVar()
        self.host_choice = ttk.Combobox(frame, textvariable=self.host_selected, state='readonly',
                                       values=[destination(**row) for row in self.hosts], width=35)
        self.host_choice.grid(row=0, column=1, sticky='ew')
        ttk.Label(frame, text='Saved hosts').grid(row=0, column=0, sticky='w')
        self.host_choice.bind('<<ComboboxSelected>>', lambda event: self.select_host())
        ttk.Button(frame, text='Add host', command=self.new_host).grid(row=0, column=2, padx=6)
        ttk.Label(frame, text='Hostname / IP / SSH alias').grid(row=1, column=0, sticky='w')
        self.host_entry = ttk.Entry(frame, textvariable=self.host_name, width=35)
        self.host_entry.grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Label(frame, text='Username (optional)').grid(row=2, column=0, sticky='w')
        ttk.Entry(frame, textvariable=self.host_user).grid(row=2, column=1, sticky='ew')
        ttk.Button(frame, text='Save target', command=self.save_host).grid(row=1, column=2, padx=6)
        ttk.Button(frame, text='Test connection', command=self.test_host).grid(row=2, column=2, padx=6)
        ttk.Label(frame, text='Uses your existing SSH keys/config. Blank username uses SSH defaults.\n'
                  'Saving or testing a host never approves deployment. Deploy actions use the fields above.',
                  wraplength=700).grid(row=3, column=0, columnspan=3, sticky='w', pady=6)
        if self.hosts:
            self.host_choice.current(0)
            self.select_host()

    def select_host(self):
        index = self.host_choice.current()
        if index >= 0:
            self.host_name.set(self.hosts[index]['host'])
            self.host_user.set(self.hosts[index]['username'])

    def new_host(self):
        self.host_selected.set('')
        self.host_name.set('')
        self.host_user.set('')
        self.host_entry.focus_set()

    def current_host(self):
        from workbench_targets import destination
        return destination(self.host_name.get(), self.host_user.get())

    def save_host(self):
        from workbench_targets import destination, save
        try:
            host = self.current_host()
            row = {'host': self.host_name.get().strip(), 'username': self.host_user.get().strip()}
            rows = [item for item in self.hosts if destination(**item) != host] + [row]
            save(self.host_path, rows)
            self.hosts = rows
            self.host_choice.configure(values=[destination(**item) for item in rows])
            self.host_selected.set(host)
            self.status.set('Saved '+host+'. No deployment permissions granted.')
        except Exception as exc:
            report_ui_error(self.window, 'save_ssh_target', exc)

    def test_host(self):
        from workbench_targets import test_connection
        if self.job is not None:
            self.status.set('Wait for the current target job.')
            return
        try:
            host = self.current_host()
            submitted = self.controller.submit(self.workspace, 'ssh_connection_test',
                lambda: test_connection(host), context={'host': host, 'deployment_performed': False})
            self.job, self.job_kind = submitted['job_id'], 'connection-test'
            self.status.set('Testing SSH connection to '+host+'…')
            self.timer = self.window.after(100, self.poll)
        except Exception as exc:
            report_ui_error(self.window, 'test_ssh_target', exc)

    def plan_summary(self):
        from forge_target_journal import locked
        approved = self.controller.targets.get(self.selected.get(), self.workspace)
        if approved.kind == 'recovery-stage':
            from forge_recovery_stage_gui import summary
            return summary(approved)
        if approved.kind == 'service':
            from forge_target_service_dispatch import locked as service_locked, transaction, digest
            from forge_target_journal import read_record
            with service_locked(approved.journal) as fd:
                binding = read_record(fd, 'authorization.json')
                if digest(binding) != approved.authorization_sha256:
                    raise ValueError('Service authorization differs from owner approval')
                pair = transaction(fd, binding['transaction_sha256'])
                scope = pair['apply']['scope']
                return {'kind': 'service', 'host': binding['host'],
                        'authorization_sha256': approved.authorization_sha256,
                        'transaction_sha256': binding['transaction_sha256'],
                        'services': scope['services'], 'paths': scope['files'] + scope['links'],
                        'journal': str(approved.journal)}
        with locked(approved.journal) as (_, plan):
            digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
            if digest != approved.plan_sha256:
                raise ValueError('Plan differs from owner approval; restart with a reviewed policy')
            return {'host': plan['host'], 'plan_sha256': digest,
                    'paths': [item['path'] for item in plan['before']['files']],
                    'journal': str(approved.journal)}

    def show_details(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def review(self):
        try:
            summary = self.plan_summary() if self.selected.get() else {
                'Getting started': 'Enter hostname and username above, then Save target and Test connection. '
                'Deploy files or Deploy service captures a backup and builds a plan first. '
                'Review and approve that plan before Apply to hardware. Restore hardware restores the selected plan backup. '
                'No approved plans yet; saving a host does not create one.'}
            text = json.dumps(summary, indent=2)
            listing = self.controller.call('target_transactions', {'workspace': self.workspace})
            for button in self.reconcile_buttons:
                button.state(['!disabled'] if summary.get('kind') == 'recovery-stage' and
                             listing['execution_granted'] and self.job is None else ['disabled'])
        except Exception as exc:
            report_ui_error(self.window, 'forge_target_gui', exc)
            text = str(exc)
        self.show_details(text)

    def review_staging(self):
        from forge_recovery_stage_gui import review_dialog
        review_dialog(self)

    def reconcile_staging(self, direction):
        from forge_recovery_stage_gui import reconcile
        reconcile(self, direction)

    def prepare_dialog(self):
        if self.job is not None:
            self.status.set('Wait for the current target job.')
            return
        try:
            host = self.current_host()
        except ValueError as exc:
            report_ui_error(self.window, 'deploy_files', exc)
            return
        mappings = []
        while True:
            source = filedialog.askopenfilename(title='Select local artifact', parent=self.window)
            if not source:
                return
            target = simpledialog.askstring('Target file', 'Absolute destination on the physical target:', parent=self.window)
            if not target:
                return
            mode = simpledialog.askstring('Target permissions', 'Octal mode (e.g. 0755); blank preserves existing mode or uses 0644:', parent=self.window)
            if mode is None:
                return
            mappings.append(dict(source=source, target=target, **({'mode': mode} if mode else {})))
            if not messagebox.askyesno('Another artifact', 'Add another file to this transaction?', parent=self.window):
                break
        parent = filedialog.askdirectory(title='Parent directory for private backup and journal', parent=self.window)
        if not parent:
            return
        if not messagebox.askyesno('Capture backup only', f'Read these target files from {host} and store a private host backup?\n\n'
                + '\n'.join(item['target'] for item in mappings) + '\n\nNo deployment will occur.', parent=self.window):
            return
        self.prepare(host, mappings, Path(parent) / ('target-' + uuid.uuid4().hex))

    def prepare_service_dialog(self):
        if self.job is not None:
            self.status.set('Wait for the current target job.')
            return
        try:
            host = self.current_host()
        except ValueError as exc:
            report_ui_error(self.window, 'deploy_service', exc)
            return
        source = filedialog.askopenfilename(title='Select standalone service unit', parent=self.window)
        if not source:
            return
        unit = simpledialog.askstring('Service name', 'Literal service name (for example my-app.service):',
                                      initialvalue=Path(source).name, parent=self.window)
        if not unit:
            return
        parent = filedialog.askdirectory(title='Parent directory for private service backup and review', parent=self.window)
        if not parent:
            return
        if not messagebox.askyesno('Capture service backup only',
                f'Read {unit} and its state from {host}, then prepare an active-service plan?\n\n'
                'No target writes, authorization or deployment occur. Existing enablement is preserved; '
                'new services use multi-user.target. Application data is not backed up.', parent=self.window):
            return
        self.prepare(host, [], Path(parent) / ('service-' + uuid.uuid4().hex), service=(unit, source))

    def prepare(self, host, mappings, output, *, service=None):
        if self.job is not None:
            raise ValueError('Wait for the current target job')
        try:
            mappings = json.loads(json.dumps(mappings))
            if service is not None:
                from forge_target_service_prepare import author
                unit, source = service
                operation = lambda: author(output, host, unit, source)
            else:
                from forge_target_prepare import author
                operation = lambda: author(output, host, mappings)
            submitted = self.controller.submit(self.workspace, 'target_prepare',
                operation, context={'host': host, 'output': str(output),
                                                               'deployment_performed': False})
            self.job, self.job_kind = submitted['job_id'], 'prepare'
            self.pending = None
            self.approve_button.state(['disabled'])
            self.choice.configure(state='disabled')
            for button in self.buttons:
                button.state(['disabled'])
            self.status.set('Preparing verified backup and review; no target writes.')
            self.timer = self.window.after(100, self.poll)
        except Exception as exc:
            report_ui_error(self.window, 'forge_target_gui', exc)
            self.status.set(str(exc))

    def approve(self):
        if self.job is not None or self.pending is None:
            return
        try:
            review = self.pending
            if review.get('kind') == 'recovery-stage':
                from forge_recovery_stage_gui import approve
                approve(self, review)
                return
            if review.get('kind') == 'service':
                self.approve_service(review)
                return
            from forge_target_journal import locked
            with locked(review['journal']) as (_, plan):
                digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
                if digest != review['plan_sha256']:
                    raise ValueError('Prepared plan changed since review; prepare a new transaction')
            self.show_details(json.dumps(review, indent=2))
            if not messagebox.askyesno('Approve physical transaction',
                    f'Approve the displayed plan for {review["host"]}?\n\nPlan SHA-256: {review["plan_sha256"]}\n\n'
                    'This enables later Apply/Restore, but does not deploy now. Already authorized target-write '
                    'clients may also invoke this transaction. Previous transactions stay available.', parent=self.window):
                return
            from forge_target_journal import private_directory, write_record
            import os
            transactions = {item.name: item.definition()
                            for item in self.controller.targets.transactions.values()} if self.controller.targets else {}
            name = 'prepared-' + uuid.uuid4().hex
            transactions[name] = {'workspace': self.workspace, 'journal': review['journal'],
                                  'plan_sha256': review['plan_sha256']}
            directory = Path(review['journal']).parent
            filename = 'approved-policy-' + uuid.uuid4().hex + '.json'
            fd = private_directory(directory)
            try:
                digest = write_record(fd, filename, {'schema': 1, 'transactions': transactions})
            finally:
                os.close(fd)
            self.controller.approve_target_policy(directory / filename, digest)
            self.choice.configure(values=list(transactions))
            self.selected.set(name)
            self.pending = None
            self.approve_button.state(['disabled'])
            for button in self.buttons:
                button.state(['!disabled'])
            self.review()
            self.status.set(f'Approved, not deployed. Retain policy {directory / filename}; SHA-256 {digest}')
        except Exception as exc:
            report_ui_error(self.window, 'forge_target_gui', exc)
            self.status.set(str(exc))

    def approve_service(self, review):
        from forge_target_service_dispatch import locked, transaction, provision_and_authorize
        review = json.loads(json.dumps(review))
        with locked(review['journal']) as fd:
            transaction(fd, review['transaction_sha256'])
        self.show_details(json.dumps(review, indent=2))
        if not messagebox.askyesno('Approve physical service transaction',
                f'Approve {review["unit"]} on {review["host"]}?\n\n'
                f'Transaction SHA-256: {review["transaction_sha256"]}\n\n'
                'Confirm that you reviewed the unit executable, dependency effects and application-data recovery. '
                'This creates a private target journal directory and permits later Apply/Restore; '
                'it does not deploy or stop the service. Authorized target-write clients may invoke it.', parent=self.window):
            return
        submitted = self.controller.submit(self.workspace, 'target_service_approve',
            lambda: provision_and_authorize(review['journal'], review['transaction_sha256'], host=review['host']),
            context={'host': review['host'], 'transaction_sha256': review['transaction_sha256'], 'deployment_performed': False})
        self.job, self.job_kind = submitted['job_id'], 'service-approve'
        self.approve_button.state(['disabled'])
        self.choice.configure(state='disabled')
        for button in self.buttons:
            button.state(['disabled'])
        self.status.set('Provisioning private target journal and recording approval; no deployment.')
        self.timer = self.window.after(100, self.poll)

    def install_service_approval(self, review):
        from forge_target_journal import private_directory, write_record
        import os
        transactions = {item.name: item.definition() for item in self.controller.targets.transactions.values()} if self.controller.targets else {}
        name = 'service-' + uuid.uuid4().hex
        transactions[name] = {'kind': 'service', 'workspace': self.workspace, 'journal': review['journal'],
                              'authorization_sha256': review['authorization_sha256']}
        directory = Path(review['journal']).parent
        filename = 'approved-policy-' + uuid.uuid4().hex + '.json'
        fd = private_directory(directory)
        try:
            digest = write_record(fd, filename, {'schema': 1, 'transactions': transactions})
        finally:
            os.close(fd)
        self.controller.approve_target_policy(directory / filename, digest)
        self.choice.configure(values=list(transactions))
        self.selected.set(name)
        self.pending = None
        for button in self.buttons:
            button.state(['!disabled'])
        self.review()
        self.status.set(f'Service approved, not deployed. Retain policy {directory / filename}; SHA-256 {digest}')

    def start(self, direction):
        if self.job is not None:
            self.status.set('Wait for the current hardware job; do not reverse an uncertain operation.')
            return
        try:
            selected = self.selected.get()
            summary = self.plan_summary()
            if not messagebox.askyesno('Confirm physical target write',
                    f'{direction.title()} transaction {self.selected.get()} on {summary["host"]}?\n\n'
                    + '\n'.join(summary['paths']) + '\n\nStop unrelated writers first. '
                    'Running jobs cannot be cancelled; failures may leave partial changes.', parent=self.window):
                return
            if self.selected.get() != selected or self.plan_summary() != summary:
                raise ValueError('Selected target approval changed during confirmation')
            submitted = self.controller.submit_target(self.workspace, selected, direction)
            self.job = submitted['job_id']
            self.job_kind = 'transition'
            self.choice.configure(state='disabled')
            for button in self.buttons:
                button.state(['disabled'])
            self.status.set(f'Physical {direction} job {self.job}; waiting for acknowledged result.')
            self.timer = self.window.after(100, self.poll)
        except Exception as exc:
            report_ui_error(self.window, 'forge_target_gui', exc)
            self.status.set(str(exc))

    def inspect_recovery(self):
        if self.job is not None:
            self.status.set('Wait for the current target job.')
            return
        try:
            submitted = self.controller.submit_target_recovery(self.workspace, self.selected.get())
            self.job = submitted['job_id']
            self.job_kind = 'recovery-inspect'
            self.choice.configure(state='disabled')
            for button in self.buttons:
                button.state(['disabled'])
            self.status.set('Reading approved target prerequisites; no boot changes or readiness claim.')
            self.timer = self.window.after(100, self.poll)
        except Exception as exc:
            report_ui_error(self.window, 'forge_target_gui', exc)
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
            report_ui_error(self.window, 'forge_target_gui', exc)
            self.status.set('Job status unavailable; retained job '+self.job+'. Recheck status, do not resubmit: '+str(exc))
            return
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            self.timer = self.window.after(100, self.poll)
            return
        self.job = None
        if self.job_kind == 'connection-test':
            if result['status'] == 'completed':
                self.status.set('Connected to '+result['result']['host']+'. No deployment permissions granted.')
            else:
                self.status.set('SSH test '+result['status']+': '+str(result.get('error', '')))
            return
        self.choice.configure(state='readonly')
        listing = self.controller.call('target_transactions', {'workspace': self.workspace})
        for button in self.buttons:
            button.state(['!disabled'] if listing['execution_granted'] and listing['transactions'] else ['disabled'])
        staging = any(item['name'] == self.selected.get() and item.get('kind') == 'recovery-stage'
                      for item in listing['transactions'])
        for button in self.reconcile_buttons:
            button.state(['!disabled'] if staging and listing['execution_granted'] else ['disabled'])
        if self.job_kind == 'staging-reconcile':
            self.show_details(json.dumps(result.get('result', {'error': result.get('error')}), indent=2))
            self.status.set('Reconciliation '+result['status']+'; inspect conflicts and requires_new_boot. No retry or reboot performed.')
            return
        if self.job_kind == 'recovery-inspect':
            self.show_details(json.dumps(result.get('result', {'error': result.get('error')}), indent=2))
            self.status.set('Inspection ' + result['status'] + '; backup and independent boot recovery remain required.')
            return
        if self.job_kind == 'prepare' and result['status'] == 'completed':
            self.pending = result['result']
            self.show_details(json.dumps(self.pending, indent=2))
            self.approve_button.state(['!disabled'])
            self.status.set('Backup and plan prepared. Review all changes, then explicitly approve; nothing deployed.')
            return
        if self.job_kind == 'service-approve' and result['status'] == 'completed':
            try:
                self.install_service_approval(result['result'])
            except Exception as exc:
                report_ui_error(self.window, 'forge_target_gui', exc)
                self.status.set('Target authorization retained; policy registration failed: ' + str(exc))
            return
        self.status.set(f'{result["status"]}: ' + (str(result.get('error', '')) +
                        ' Inspect the journal and reconcile before reversing; no rollback is implied.'
                        if result['status'] != 'completed' else 'Target acknowledged the transition. Journal retained.'))

    def close(self):
        if self.job is not None:
            self.status.set('Wait for the hardware job to finish before closing this panel.')
            return False
        if self.timer:
            self.window.after_cancel(self.timer)
        self.window.destroy()
        return True
