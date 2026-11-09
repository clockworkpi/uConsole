"""Owner review and explicit foreground execution of prepared recovery jobs."""
from workbench_diagnostics import report_ui_error, report_job_error
import hashlib
import json
import os
from pathlib import Path
import stat
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


WARNINGS = {
    'backup-card': 'Read the offline card into a new private host backup. This does not authorize restoration.',
    'hash-card': 'Read and hash the offline card under the bound recovery lease. This is not a backup and grants no write or boot-release authority.',
    'prepare-hold': 'Compile a local selector plan from pinned hold, hash and backup/export evidence. No target contact or plan approval occurs; review the resulting pin separately.',
    'restore-root': 'Write the approved original backup to the offline root partition. Source filesystem errors require separate policy approval.',
    'deploy-root': 'Write the approved enhanced image root. The original backup remains the rollback source.',
    'reconcile-root': 'Fence and inspect the recorded attempt. This neither retries disk writes nor releases the recovery hold.',
    'retry-lease': 'Replay only the pending lease request with its original deadline. This does not adopt or extend a lease.',
    'install-hold': 'Change only config.txt to keep subsequent boots in recovery. The independently approved root must still match. No reboot is performed.',
    'release-hold': 'Restore the original config.txt selector only if the independently approved root still matches. This permits a later normal boot; it does not reboot or prove that boot succeeds.',
    'reconcile-hold': 'Fence and inspect a previous boot-selector attempt, including independent card hashes. Do not infer retry or reboot permission from its result.',
}


def policy_pin(filename):
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Select a regular owner policy file')
        data = stream.read(1024*1024+1)
    if len(data) > 1024*1024:
        raise ValueError('Recovery policy exceeds 1 MiB')
    return hashlib.sha256(data).hexdigest()


def summary(job):
    """Owner-only local review; never expose these paths through MCP listings."""
    return dict(name=job.name, operation=job.operation, effect=WARNINGS[job.operation],
                host=job.probe.host, port=job.probe.port, serial=job.probe.serial,
                kernel=job.probe.kernel, boot_id=job.boot_id, machine_id=job.machine_id,
                session=str(job.session), session_sha256=job.session_pin,
                key=str(job.probe.key), known_hosts=str(job.probe.known_hosts),
                arguments=job.arguments)


class RecoveryPanel:
    def __init__(self, parent, controller, workspace):
        self.controller, self.workspace = controller, workspace
        self.job = self.timer = self.pending = None
        self.job_kind = 'recovery'
        self.preparation_output = None
        self.enrollment_candidate = self.enrollment_ready = None
        self.normal_return_source = None
        self.build_host = self.build_candidate = None
        self.window = tk.Toplevel(parent)
        self.window.title('Physical recovery — prepared jobs')
        self.selected = tk.StringVar()
        self.status = tk.StringVar(value='No action runs without confirmation. Prepared RAM-recovery session required.')
        ttk.Label(self.window, text='Backup → validated image change → deploy → reconcile → explicit boot release',
                  wraplength=760).pack(padx=12, pady=8)
        ttk.Label(self.window, text='Advanced recovery controls. Preparation creates drafts. File changes and reboots require separate owner confirmations.',
                  wraplength=760).pack(padx=12, pady=4)
        controls = ttk.Frame(self.window)
        controls.pack(padx=12, pady=4)
        self.load_button = ttk.Button(controls, text='Review policy…', command=self.load_dialog)
        self.load_button.pack(side='left')
        self.prepare_button = ttk.Button(controls, text='Prepare boot staging…', command=self.prepare_dialog)
        self.prepare_button.pack(side='left', padx=4)
        self.enroll_button = ttk.Button(controls, text='Enroll recovery session…', command=self.enroll_dialog)
        self.enroll_button.pack(side='left', padx=4)
        self.backup_button = ttk.Button(controls, text='Prepare backup job…', command=self.backup_dialog)
        self.backup_button.pack(side='left', padx=4)
        self.approve_button = ttk.Button(controls, text='Approve reviewed policy…', command=self.approve)
        self.approve_button.pack(side='left', padx=4)
        self.approve_button.state(['disabled'])
        builder = ttk.Frame(self.window)
        builder.pack(padx=12, pady=4)
        self.discover_build_button = ttk.Button(builder, text='Discover build target…', command=self.discover_build_dialog)
        self.discover_build_button.pack(side='left')
        self.build_button = ttk.Button(builder, text='Build private recovery image…', command=self.build_dialog)
        self.build_button.pack(side='left', padx=4)
        self.publication_button = ttk.Button(builder, text='Prepare publication…', command=self.publication_dialog)
        self.publication_button.pack(side='left', padx=4)
        self.publish_button = ttk.Button(builder, text='Publish reviewed image…', command=self.publish_dialog)
        self.publish_button.pack(side='left', padx=4)
        privacy = ttk.Frame(self.window)
        privacy.pack(padx=12, pady=4)
        self.privacy_prepare_button = ttk.Button(privacy, text='Prepare private boot mount…', command=self.privacy_prepare_dialog)
        self.privacy_prepare_button.pack(side='left')
        self.privacy_apply_button = ttk.Button(privacy, text='Apply private mount policy…', command=self.privacy_apply_dialog)
        self.privacy_apply_button.pack(side='left', padx=4)
        self.privacy_reboot_button = ttk.Button(privacy, text='Reboot for private mount…', command=lambda: self.privacy_verify_dialog(reboot=True))
        self.privacy_reboot_button.pack(side='left', padx=4)
        self.privacy_verify_button = ttk.Button(privacy, text='Verify private mount…', command=self.privacy_verify_dialog)
        self.privacy_verify_button.pack(side='left', padx=4)
        self.tryboot_button = ttk.Button(privacy, text='Boot recovery and enroll…', command=lambda: self.enroll_dialog(tryboot=True))
        self.tryboot_button.pack(side='left', padx=4)
        offline = ttk.Frame(self.window)
        offline.pack(padx=12, pady=4)
        self.source_button = ttk.Button(offline, text='Prepare retained backup source…', command=self.source_dialog)
        self.source_button.pack(side='left')
        self.health_button = ttk.Button(offline, text='Check retained backup filesystems…',
                                       command=lambda: self.source_dialog(health=True))
        self.health_button.pack(side='left', padx=4)
        exports = ttk.Frame(self.window)
        exports.pack(padx=12, pady=4)
        self.derivative_button = ttk.Button(exports, text='Verify exported image lineage…', command=self.derivative_dialog)
        self.derivative_button.pack(side='left')
        self.export_health_button = ttk.Button(exports, text='Check exported root filesystem…', command=self.export_health_dialog)
        self.export_health_button.pack(side='left', padx=4)
        self.hash_button = ttk.Button(exports, text='Prepare current-card hash…',
                                      command=lambda: self.backup_dialog(hash_only=True))
        self.hash_button.pack(side='left', padx=4)
        transitions = ttk.Frame(self.window)
        transitions.pack(padx=12, pady=4)
        self.hold_button = ttk.Button(transitions, text='Prepare recovery hold…', command=self.hold_dialog)
        self.hold_button.pack(side='left')
        self.held_reboot_button = ttk.Button(transitions, text='Reboot into held recovery…', command=self.held_reboot_dialog)
        self.held_reboot_button.pack(side='left', padx=4)
        self.reconcile_prepare_button = ttk.Button(transitions, text='Prepare reconciliation…', command=self.reconcile_dialog)
        self.reconcile_prepare_button.pack(side='left', padx=4)
        self.open_enrollment_button = ttk.Button(transitions, text='Open retained enrollment…', command=self.open_enrollment_dialog)
        self.open_enrollment_button.pack(side='left', padx=4)
        transfers = ttk.Frame(self.window)
        transfers.pack(padx=12, pady=4)
        self.deploy_prepare_button = ttk.Button(transfers, text='Prepare enhanced-root deployment…',
                                               command=lambda: self.root_dialog('deploy-root'))
        self.deploy_prepare_button.pack(side='left')
        self.restore_prepare_button = ttk.Button(transfers, text='Prepare original-root restoration…',
                                                command=lambda: self.root_dialog('restore-root'))
        self.restore_prepare_button.pack(side='left', padx=4)
        self.release_prepare_button = ttk.Button(transfers, text='Prepare normal-boot release…', command=self.release_dialog)
        self.release_prepare_button.pack(side='left', padx=4)
        normal = ttk.Frame(self.window)
        normal.pack(padx=12, pady=4)
        self.normal_reboot_button = ttk.Button(normal, text='Reboot to normal system…',
                                              command=lambda: self.normal_return_dialog(reboot=True))
        self.normal_reboot_button.pack(side='left')
        self.normal_verify_button = ttk.Button(normal, text='Verify normal return…', command=self.normal_return_dialog)
        self.normal_verify_button.pack(side='left', padx=4)
        self.cleanup_button = ttk.Button(normal, text='Clean up recovery artifacts…', command=self.cleanup_dialog)
        self.cleanup_button.pack(side='left', padx=4)
        restoration = ttk.Frame(self.window)
        restoration.pack(padx=12, pady=4)
        self.privacy_restore_button = ttk.Button(restoration, text='Restore original boot-mount policy…',
                                                 command=self.privacy_restore_dialog)
        self.privacy_restore_button.pack(side='left')
        self.original_mount_reboot_button = ttk.Button(restoration, text='Reboot for original permissions…',
                                                       command=lambda: self.original_mount_dialog(reboot=True))
        self.original_mount_reboot_button.pack(side='left', padx=4)
        self.original_mount_verify_button = ttk.Button(restoration, text='Verify original permissions…',
                                                       command=self.original_mount_dialog)
        self.original_mount_verify_button.pack(side='left', padx=4)
        self.choice = ttk.Combobox(self.window, textvariable=self.selected, state='readonly', width=50)
        self.choice.pack(padx=12, pady=4)
        self.choice.bind('<<ComboboxSelected>>', lambda event: self.review())
        self.details = tk.Text(self.window, width=100, height=22, wrap='word', state='disabled')
        self.details.pack(padx=12, pady=4, fill='both', expand=True)
        actions = ttk.Frame(self.window)
        actions.pack(padx=12, pady=4)
        self.run_button = ttk.Button(actions, text='Run selected job…', command=self.start)
        self.run_button.pack(side='left')
        self.recheck_button = ttk.Button(actions, text='Recheck job status', command=self.poll)
        self.recheck_button.pack(side='left', padx=4)
        self.recheck_button.state(['disabled'])
        ttk.Label(self.window, textvariable=self.status, wraplength=760).pack(padx=12, pady=8)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()

    def show(self, value):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', json.dumps(value, indent=2))
        self.details.configure(state='disabled')

    def refresh(self):
        listing = self.controller.call('recovery_jobs', {'workspace': self.workspace})
        names = [item['name'] for item in listing['jobs']]
        self.choice.configure(values=names, state='disabled' if self.job else 'readonly')
        if self.selected.get() not in names:
            self.selected.set(names[0] if names else '')
        self.run_button.state(['!disabled'] if names and listing['execution_granted'] and not self.job and not self.pending else ['disabled'])
        self.load_button.state(['disabled'] if self.job else ['!disabled'])
        self.prepare_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.enroll_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.open_enrollment_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.backup_button.state(['!disabled'] if self.enrollment_ready and not self.job and not self.pending else ['disabled'])
        self.hash_button.state(['!disabled'] if self.enrollment_ready and not self.job and not self.pending else ['disabled'])
        self.hold_button.state(['!disabled'] if self.enrollment_ready and not self.job and not self.pending else ['disabled'])
        self.held_reboot_button.state(['!disabled'] if self.enrollment_ready and not self.job and not self.pending else ['disabled'])
        self.reconcile_prepare_button.state(['!disabled'] if self.enrollment_ready and not self.job and not self.pending else ['disabled'])
        for button in (self.deploy_prepare_button, self.restore_prepare_button, self.release_prepare_button):
            button.state(['!disabled'] if self.enrollment_ready and not self.job and not self.pending else ['disabled'])
        self.approve_button.state(['!disabled'] if self.pending and not self.job else ['disabled'])
        self.recheck_button.state(['!disabled'] if self.job else ['disabled'])
        self.discover_build_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.build_button.state(['!disabled'] if self.build_candidate and not self.job and not self.pending else ['disabled'])
        self.publication_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.publish_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.privacy_prepare_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.privacy_apply_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.privacy_reboot_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.privacy_verify_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.tryboot_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        for button in (self.normal_reboot_button, self.normal_verify_button, self.cleanup_button, self.privacy_restore_button,
                       self.original_mount_reboot_button, self.original_mount_verify_button):
            button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.source_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.health_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.derivative_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        self.export_health_button.state(['disabled'] if self.job or self.pending else ['!disabled'])
        if not self.pending:
            self.review()

    def review(self):
        if self.pending:
            return
        try:
            self.show(summary(self.controller.recovery_jobs.get(self.selected.get(), self.workspace))
                      if self.selected.get() else {'configuration': 'Review an owner-prepared recovery policy, or start with --recovery-policy and --recovery-policy-sha256.'})
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Review unavailable: ' + str(exc))
            self.run_button.state(['disabled'])

    def load_dialog(self):
        if self.job:
            return
        filename = filedialog.askopenfilename(parent=self.window, title='Owner-prepared recovery policy',
                                              filetypes=[('JSON policy', '*.json')])
        if filename:
            try:
                self.load_policy(Path(filename))
            except Exception as exc:
                report_ui_error(self.window, 'forge_recovery_gui', exc)
                self.status.set('Policy review failed: ' + str(exc))

    def discover_build_dialog(self):
        if self.job or self.pending:
            return
        host = simpledialog.askstring('Native recovery builder', 'Normal SSH target (user@hostname):', parent=self.window)
        if not host:
            return
        if not messagebox.askyesno('Read native build identity',
                f'Read the normal boot identity and kernel from {host}? No files will be staged or built.', parent=self.window):
            return
        try:
            self.discover_builder(host)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Discovery not submitted: ' + str(exc))

    def publication_dialog(self):
        if self.job or self.pending:
            return
        directory = filedialog.askdirectory(parent=self.window, title='Completed private recovery build journal')
        if not directory:
            return
        pin = simpledialog.askstring('Build approval', 'Approved build acceptance SHA-256:', parent=self.window)
        if not pin:
            return
        parent = filedialog.askdirectory(parent=self.window, title='Parent directory for publication draft and observations')
        if not parent:
            return
        try:
            from forge_recovery_publication_prepare import inputs
            frozen = inputs(directory, pin)
            self.show(dict(host=frozen['request']['host'], machine_id=frozen['native']['boot']['machine_id'],
                           image_sha256=frozen['native']['sha256'], image_size=frozen['native']['size'],
                           build_acceptance_sha256=pin))
            if not messagebox.askyesno('Prepare private image publication',
                    'Read the displayed target boot identity, fstab, private mount policy and destination absence?\n\n'
                    'This creates a publication draft only. The boot filesystem must already have a verified persistent '
                    'private mount policy. It does not publish credentials, modify fstab, change mount permissions, reboot, '
                    'or qualify fallback.', parent=self.window):
                return
            if inputs(directory, pin) != frozen:
                raise ValueError('Build provenance changed during review')
            import uuid
            self.prepare_publication(directory, pin, Path(parent)/('recovery-publication-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Publication preparation not submitted: ' + str(exc))

    def publish_dialog(self):
        if self.job or self.pending:
            return
        directory = filedialog.askdirectory(parent=self.window, title='Completed publication preparation journal')
        if not directory:
            return
        try:
            from forge_recovery_publication_dispatch import inputs
            pin = policy_pin(Path(directory)/'acceptance.json')
            frozen = inputs(directory, pin)
            plan = frozen['plan']
            self.show(dict(plan=plan, expected_boot=frozen['boot'], preparation_sha256=pin))
            if not messagebox.askyesno('Publish credential-bearing recovery image',
                    f'Publish the displayed exact image on {plan["host"]}?\n\n'
                    f'SHA-256: {plan["sha256"]}\nDestination: {plan["destination"]}\n\n'
                    'This writes one credential-bearing image to the verified private boot filesystem. It does not '
                    'change boot selectors, reboot, qualify fallback or authorize root writes. Keep Workbench open. '
                    'Failure may leave publication uncertain: retain evidence and inspect, never automatically retry.', parent=self.window):
                return
            if inputs(directory, pin) != frozen:
                raise ValueError('Publication changed during confirmation; review again')
            self.publish_image(frozen)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Publication not submitted: ' + str(exc))

    def privacy_prepare_dialog(self):
        if self.job or self.pending:
            return
        host = simpledialog.askstring('Private boot mount setup', 'Normal SSH target (user@hostname):', parent=self.window)
        if not host:
            return
        parent = filedialog.askdirectory(parent=self.window, title='Parent directory for original fstab backup and private-policy draft')
        if not parent:
            return
        if not messagebox.askyesno('Prepare private boot mount',
                f'Read {host} boot/mount identity and back up /etc/fstab?\n\n'
                'The candidate parser uses temporary /run scratch. This does not apply the policy, remount, reboot or publish credentials.',
                parent=self.window):
            return
        try:
            import uuid
            self.prepare_privacy(host, Path(parent)/('boot-privacy-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Privacy preparation not submitted: ' + str(exc))

    def prepare_privacy(self, host, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before privacy preparation')
        submitted = self.controller.prepare_boot_privacy(self.workspace, host, output)
        self.job, self.job_kind = submitted['job_id'], 'prepare-privacy'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Backing up fstab and preparing a private mount draft. Keep Workbench open.')
        self.timer = self.window.after(100, self.poll)

    def privacy_apply_dialog(self):
        if self.job or self.pending:
            return
        directory = filedialog.askdirectory(parent=self.window, title='Completed private boot mount preparation')
        if not directory:
            return
        try:
            import base64
            from forge_boot_privacy_setup import inputs
            pin = policy_pin(Path(directory)/'acceptance.json')
            frozen = inputs(directory, pin)
            plan = frozen['plan']
            self.show(dict(host=plan['host'], boot=frozen['boot'], preparation_sha256=pin,
                before=base64.b64decode(plan['before']['files'][0]['data']).decode(),
                after=base64.b64decode(plan['after']['files'][0]['data']).decode()))
            if not messagebox.askyesno('Apply private boot mount policy',
                    f'Apply the displayed fstab-only change on {plan["host"]}?\n\n'
                    'The original fstab is retained for restoration. This does not remount or reboot, and it does not '
                    'make a currently public mount private. A separate reboot and effective permission check are required '
                    'before publication. Retain uncertain attempts; do not automatically retry.', parent=self.window):
                return
            if inputs(directory, pin) != frozen:
                raise ValueError('Privacy plan changed during confirmation')
            self.apply_privacy(frozen)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Privacy application not submitted: ' + str(exc))

    def apply_privacy(self, frozen):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before applying privacy')
        submitted = self.controller.apply_boot_privacy(self.workspace, frozen)
        self.job, self.job_kind = submitted['job_id'], 'apply-privacy'
        self.preparation_output = Path(frozen['directory'])/'apply-attempt'
        self.refresh()
        self.status.set('Applying the fstab-only policy. Keep Workbench open; no reboot or publication authorized.')
        self.timer = self.window.after(100, self.poll)

    def privacy_verify_dialog(self, *, reboot=False):
        if self.job or self.pending:
            return
        directory = filedialog.askdirectory(parent=self.window, title='Acknowledged private mount policy preparation')
        if not directory:
            return
        try:
            from forge_boot_privacy_verify import inputs
            pin = policy_pin(Path(directory)/'acceptance.json')
            frozen = inputs(directory, pin)
            self.show(dict(host=frozen['plan']['host'], previous_boot=frozen['boot'],
                           preparation_sha256=pin, reboot_requested=reboot))
            effect = ('Send one guarded normal reboot and wait up to ten minutes for fresh private-mount evidence? '
                      'This interrupts running target applications. Save work first. The reboot request is never retried.'
                      if reboot else 'Read the existing new normal boot and effective private mount? No reboot will be sent.')
            if not messagebox.askyesno('Verify fresh private boot mount',
                    f'Target: {frozen["plan"]["host"]}\n\n{effect}\n\n'
                    'Both paths require unchanged applied fstab, a different normal boot, and verified non-root read denial. '
                    'This does not publish an image or qualify recovery fallback.', parent=self.window):
                return
            if inputs(directory, pin) != frozen:
                raise ValueError('Applied privacy evidence changed during confirmation')
            self.verify_privacy(frozen, reboot=reboot)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Private mount verification not submitted: ' + str(exc))

    def verify_privacy(self, frozen, *, reboot=False):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before private mount verification')
        submitted = self.controller.verify_boot_privacy(self.workspace, frozen, reboot=reboot)
        self.job, self.job_kind = submitted['job_id'], 'verify-privacy'
        self.preparation_output = Path(frozen['directory'])
        self.refresh()
        self.status.set('Verifying fresh private mount. Keep Workbench open; no automatic reboot retry.')
        self.timer = self.window.after(100, self.poll)

    def publish_image(self, frozen):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before publishing')
        submitted = self.controller.publish_recovery_image(self.workspace, frozen)
        self.job, self.job_kind = submitted['job_id'], 'publish-image'
        self.preparation_output = Path(frozen['directory'])/'publish-attempt'
        self.refresh()
        self.status.set('Publication accepted. Keep Workbench open; do not resubmit after a status failure.')
        self.timer = self.window.after(100, self.poll)

    def prepare_publication(self, directory, pin, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before preparing publication')
        submitted = self.controller.prepare_recovery_publication(self.workspace, directory, pin, output)
        self.job, self.job_kind = submitted['job_id'], 'prepare-publication'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Checking private publication prerequisites. No publication authorized; keep Workbench open.')
        self.timer = self.window.after(100, self.poll)

    def discover_builder(self, host):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before discovery')
        submitted = self.controller.discover_recovery_builder(self.workspace, host)
        self.build_host, self.build_candidate = host, None
        self.job, self.job_kind = submitted['job_id'], 'discover-builder'
        self.refresh()
        self.status.set('Reading normal target identity. Keep Workbench open; no build has been requested.')
        self.timer = self.window.after(100, self.poll)

    def build_dialog(self):
        if self.job or self.pending or not self.build_candidate:
            return
        import copy
        candidate = copy.deepcopy(self.build_candidate)
        credentials = filedialog.askdirectory(parent=self.window,
            title='Private credentials: wpa.conf, authorized_keys, ssh_host_ed25519_key')
        if not credentials:
            return
        parent = filedialog.askdirectory(parent=self.window, title='Parent directory for private recovery image and evidence')
        if not parent:
            return
        self.show(candidate)
        if not messagebox.askyesno('Build private recovery image',
                f'Build on {candidate["host"]} using kernel {candidate["kernel"]}?\n'
                f'Confirmed boot: {candidate["boot"]["boot_id"]}\n\n'
                'This sends the selected credentials and builder sources over SSH, uses sudo, and creates private target scratch '
                'and host output. Requires installed native build dependencies. It does not publish, alter boot files, reboot, '
                'or qualify fallback. Keep Workbench open. On failure retain both journals; do not automatically retry.', parent=self.window):
            return
        try:
            if self.build_candidate != candidate:
                raise ValueError('Discovery changed during confirmation; review again')
            import uuid
            self.build_image(candidate, Path(credentials), Path(parent)/('recovery-build-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Build not submitted: ' + str(exc))

    def build_image(self, candidate, credentials, output):
        if self.job or self.pending or candidate != self.build_candidate:
            raise ValueError('Build requires the reviewed idle discovery')
        submitted = self.controller.build_recovery_image(self.workspace, candidate, credentials, output)
        self.build_candidate = None
        self.job, self.job_kind = submitted['job_id'], 'build-image'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Native build accepted. Keep Workbench open; no publication or reboot is authorized.')
        self.timer = self.window.after(100, self.poll)

    def prepare_dialog(self):
        if self.job or self.pending:
            return
        publication = filedialog.askdirectory(parent=self.window, title='Acknowledged private recovery-image publication journal')
        if not publication:
            return
        publication_pin = simpledialog.askstring('Publication approval', 'Approved publication plan SHA-256:', parent=self.window)
        if not publication_pin:
            return
        bundle = filedialog.askopenfilename(parent=self.window, title='Owner-reviewed matched firmware bundle JSON')
        if not bundle:
            return
        bundle_pin = simpledialog.askstring('Firmware approval', 'Approved canonical firmware bundle SHA-256:', parent=self.window)
        if not bundle_pin:
            return
        parent = filedialog.askdirectory(parent=self.window, title='Parent directory for private preimage backups and staging drafts')
        if not parent:
            return
        try:
            from forge_recovery_stage_prepare import inputs
            frozen = inputs(publication, publication_pin, bundle, bundle_pin)
            plan = frozen['image_plan']
            self.show(dict(host=plan['host'], machine_id=plan['machine_id'],
                           image_sha256=plan['sha256'], publication_sha256=publication_pin,
                           firmware_revision=frozen['firmware_bundle'].get('revision'), firmware_sha256=bundle_pin))
            if not messagebox.askyesno('Prepare recovery boot drafts',
                    f'Read boot preimages and recovery-image state from {plan["host"]}?\n\n'
                    'This performs read-only SSH checks and creates private host backups/plans. It does not stage firmware, approve jobs, '
                    'reboot, or replace the required whole-card backup and fallback qualification.', parent=self.window):
                return
            import uuid
            self.prepare_staging(publication, publication_pin, bundle, bundle_pin,
                                 Path(parent)/('recovery-staging-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Preparation not submitted: ' + str(exc))

    def prepare_staging(self, publication, publication_pin, bundle, bundle_pin, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or policy review before preparing staging')
        submitted = self.controller.prepare_recovery_staging(self.workspace, publication, publication_pin,
                                                            bundle, bundle_pin, output)
        self.job, self.job_kind = submitted['job_id'], 'prepare-staging'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Reading normal-SSH preimages and authoring private drafts. No target writes or reboot.')
        self.timer = self.window.after(100, self.poll)

    def enroll_dialog(self, *, tryboot=False):
        if self.job or self.pending:
            return
        try:
            staging = filedialog.askdirectory(parent=self.window, title='Sealed boot staging preparation')
            if not staging: return
            fields = {}
            for name, prompt in (
                    ('pin', 'Owner-recorded staging acceptance SHA-256:'),
                    ('host', 'Pinned recovery hostname or IP (port 2222, no username):'),
                    ('kernel', 'Expected native kernel release:'),
                    ('serial', 'Expected 16-digit hexadecimal hardware serial:')):
                fields[name] = simpledialog.askstring('Fresh recovery session', prompt, parent=self.window)
                if not fields[name]: return
            selected = 1 if tryboot else simpledialog.askinteger('Recovery boot selection',
                'Expected firmware selection: 1 = one-shot tryboot; 0 = persistent recovery.',
                minvalue=0, maxvalue=1, parent=self.window)
            if selected is None: return
            key = filedialog.askopenfilename(parent=self.window, title='Private recovery client key')
            if not key: return
            known = filedialog.askopenfilename(parent=self.window, title='Private pinned recovery known_hosts')
            if not known: return
            parent = filedialog.askdirectory(parent=self.window, title='Parent for new private enrollment evidence')
            if not parent: return
            from forge_session_enrollment import inputs, summary
            value = inputs(staging, fields['pin'], fields['host'], key, known,
                           fields['kernel'], fields['serial'], None, selected)
            self.show(summary(value))
            if tryboot:
                from forge_recovery_tryboot import reviewed
                draft, _ = reviewed(value)
                self.show(dict(summary(value), normal_host=draft['request']['image_plan']['host']))
            question = (f'Reboot {draft["request"]["image_plan"]["host"]} once into recovery and enroll the new RAM boot?\n\n'
                    'Save target work first. All four staging phases must be acknowledged. The private image and all nine '
                    'boot files are checked again before one tryboot request. Keep physical power-cycle access available '
                    'for an unqualified image. This does not prove fallback, acquire a lease or authorize root writes. '
                    'Prepare and run a backup promptly after enrollment; an unleased recovery boot may expire back to normal. '
                    'No automatic reboot retry will occur.' if tryboot else
                    f'Read recovery identity from {value.probe.host}:2222 and pin its new RAM boot UUID?\n\n'
                    'Use this only for a fresh boot with no existing host lease owner. This is not a way to '
                    'recover a lost session. The sealed staging supplies the owner; it will not be guessed. '
                    'No lease, reboot, disk write or agent grant is performed. Failed enrollment retains its '
                    'claim and evidence; do not retry or delete an uncertain session.')
            if not messagebox.askyesno('Boot and enroll recovery' if tryboot else 'Verify and enroll fresh recovery boot',
                                      question, parent=self.window):
                return
            import uuid
            output = Path(parent)/(('recovery-tryboot-' if tryboot else 'recovery-enrollment-')+uuid.uuid4().hex)
            if tryboot:
                self.boot_recovery(value, output)
            else:
                self.enroll_session(value, output)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Enrollment not submitted: ' + str(exc))

    def open_enrollment_dialog(self):
        if self.job or self.pending: return
        try:
            directory = filedialog.askdirectory(parent=self.window, title='Retained recovery enrollment directory')
            if not directory: return
            pin = simpledialog.askstring('Enrollment pin', 'Canonical SHA-256 of enrollment acceptance.json:', parent=self.window)
            if not pin: return
            key = filedialog.askopenfilename(parent=self.window, title='Original private recovery key')
            if not key: return
            known = filedialog.askopenfilename(parent=self.window, title='Original pinned recovery known_hosts')
            if not known: return
            if not messagebox.askyesno('Open retained enrollment',
                    'Load this exact enrollment and its existing lease history for further plan preparation?\n\n'
                    'This is offline: no target contact, renewal, sequence reset, pending-request retry or permission grant. '
                    'A session held by another live owner cannot be opened. The target boot and lease may have expired; '
                    'later jobs must revalidate them. A pending renewal stays pending.', parent=self.window): return
            self.open_enrollment(directory, pin.strip(), key, known)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Retained enrollment not opened: '+str(exc))

    def open_enrollment(self, directory, pin, key, known):
        if self.job or self.pending: raise ValueError('Finish the current job or policy review before opening enrollment')
        from forge_backup_policy import inputs
        from forge_recovery_stage_prepare import pinned_record
        from forge_recovery_session import Session
        source = inputs(directory, pin, key, known)
        with Session(source.directory/'session', source.session_pin, source.probe, source.boot_id, source.owner) as session:
            accepted = pinned_record(source.directory/'acceptance.json', pin)
            summary = dict(host=source.probe.host, boot_id=source.boot_id,
                last_acknowledged_sequence=session.accepted['sequence'] if session.accepted else 0,
                pending_renewal=session.unresolved, target_contacted=False, lease_renewed=False,
                target_boot_verified=False, policy_approved=False)
        self.enrollment_ready = ((source.directory, source.probe.key, source.probe.known_hosts), accepted)
        self.refresh()
        self.show(summary)
        self.status.set('Retained enrollment loaded offline. No lease renewed or grants added; target state is not verified. '
                        + ('Pending renewal remains unresolved; use an explicitly reviewed retry-lease job.' if summary['pending_renewal'] else
                           'Later jobs recheck the pinned boot and existing lease.'))

    def enroll_session(self, value, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or policy review before enrollment')
        submitted = self.controller.enroll_recovery_session(self.workspace, value, output)
        self.job, self.job_kind = submitted['job_id'], 'enroll-session'
        self.preparation_output = Path(output)
        self.enrollment_candidate = (Path(output), value.probe.key, value.probe.known_hosts)
        self.enrollment_ready = None
        self.refresh()
        self.status.set('Verifying fresh RAM boot and recording a local session. No lease or target mutation.')
        self.timer = self.window.after(100, self.poll)

    def boot_recovery(self, value, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before recovery boot')
        submitted = self.controller.boot_recovery_session(self.workspace, value, output)
        self.job, self.job_kind = submitted['job_id'], 'tryboot-enroll'
        self.preparation_output = Path(output)
        self.enrollment_candidate = (Path(output)/'enrollment', value.probe.key, value.probe.known_hosts)
        self.enrollment_ready = None
        self.refresh()
        self.status.set('Guarded one-shot recovery boot accepted. Keep Workbench open; no reboot retry or root-write authority.')
        self.timer = self.window.after(100, self.poll)

    def backup_dialog(self, *, hash_only=False):
        if self.job or self.pending or not self.enrollment_ready:
            return
        try:
            from forge_backup_policy import inputs
            from forge_recovery_bootplan import digest
            (directory, key, known), accepted = self.enrollment_ready
            source = inputs(directory, digest(accepted), key, known)
            kind = 'hash' if hash_only else 'backup'
            parent = filedialog.askdirectory(parent=self.window,
                title='Storage parent for current-card hash evidence' if hash_only else
                      'Storage parent for backup evidence and the later full-card archive',
                initialdir=str(directory.parent))
            if not parent: return
            if not messagebox.askyesno(f'Prepare {kind}-only job policy',
                    f'Read offline SD identity from {source.probe.host}?\n\nBoot: {source.boot_id}\n\n'
                    f'This creates a private {kind}-only policy draft. It does not acquire a lease, '
                    'create an archive, capture full-card hashes, approve policy, reboot, or authorize card writes. '
                    'Review the observed card and destination before separately approving and running the job.',
                    parent=self.window):
                return
            import uuid
            self.prepare_backup(source, Path(parent)/(f'recovery-{kind}-'+uuid.uuid4().hex), hash_only=hash_only)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Card policy draft not submitted: ' + str(exc))

    def prepare_backup(self, source, output, *, hash_only=False):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before preparing a backup')
        action = self.controller.prepare_recovery_hash if hash_only else self.controller.prepare_recovery_backup
        submitted = action(self.workspace, source, output)
        self.job, self.job_kind = submitted['job_id'], 'prepare-hash' if hash_only else 'prepare-backup'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Reading offline SD identity and preparing a ' + ('hash' if hash_only else 'backup') +
                        '-only policy. No lease, full-card hashes or backup yet.')
        self.timer = self.window.after(100, self.poll)

    def hold_dialog(self):
        if self.job or self.pending or not self.enrollment_ready:
            return
        try:
            from forge_backup_policy import inputs as enrolled
            from forge_hold_policy import inputs
            from forge_recovery_bootplan import digest
            from forge_recovery_source_contract import BACKUP, DERIVATIVE
            (directory, key, known), accepted = self.enrollment_ready
            source = enrolled(directory, digest(accepted), key, known)
            staging = filedialog.askdirectory(parent=self.window, title='Sealed staging directory used for this enrollment')
            if not staging: return
            hashes = filedialog.askdirectory(parent=self.window, title='Completed current-card hash evidence directory')
            if not hashes: return
            hashes_pin = simpledialog.askstring('Current-card hash pin', 'Canonical SHA-256 of the hash acceptance.json:',
                                               parent=self.window)
            if not hashes_pin: return
            kind = simpledialog.askstring('Current root guard', 'Use an original backup or verified export? Enter backup or export:',
                                         initialvalue='backup', parent=self.window)
            if not kind: return
            kinds = {'backup': BACKUP, 'export': DERIVATIVE}
            if kind.strip() not in kinds: raise ValueError('Select backup or export explicitly')
            manifest = filedialog.askdirectory(parent=self.window, title='Selected root source directory (contains manifest.json)')
            if not manifest: return
            pin = simpledialog.askstring('Root source pin', 'Owner-recorded canonical SHA-256 of manifest.json:', parent=self.window)
            if not pin: return
            reviewed = inputs(source, staging, accepted['staging_sha256'], hashes, hashes_pin.strip(),
                              manifest, pin.strip(), kinds[kind.strip()])
            parent = filedialog.askdirectory(parent=self.window, title='Storage parent for hold plan and unapproved policy')
            if not parent: return
            if not messagebox.askyesno('Prepare install-hold draft only',
                    f"Boot: {source.boot_id}\nStaging: {reviewed['staging_sha256']}\n"
                    f"Root: {reviewed['source_manifest']['root']['sha256']}\n\n"
                    'Compile a selector-only install-hold plan and unapproved job policy. No target contact, '
                    'lease renewal, boot change, reboot or root write occurs now. Later approval and execution '
                    'will persist recovery boot selection; this draft does not release it or authorize deployment.',
                    parent=self.window):
                return
            import uuid
            self.prepare_hold(source, reviewed, Path(parent)/('recovery-hold-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Hold draft not submitted: ' + str(exc))

    def prepare_hold(self, source, reviewed, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before preparing a hold')
        submitted = self.controller.prepare_recovery_hold(self.workspace, source, reviewed, output)
        self.job, self.job_kind = submitted['job_id'], 'prepare-hold'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Compiling pinned hold evidence offline. No target contact, lease renewal or policy approval.')
        self.timer = self.window.after(100, self.poll)

    def held_reboot_dialog(self):
        if self.job or self.pending or not self.enrollment_ready:
            return
        try:
            from forge_backup_policy import inputs as enrolled
            from forge_recovery_bootplan import digest
            from forge_session_enrollment import inputs
            from forge_held_reboot import reviewed
            (directory, key, known), accepted = self.enrollment_ready
            source = enrolled(directory, digest(accepted), key, known)
            staging = filedialog.askdirectory(parent=self.window, title='Original sealed boot staging')
            if not staging: return
            hold = filedialog.askdirectory(parent=self.window, title='Acknowledged install-hold journal (contains plan.json)')
            if not hold: return
            pin = simpledialog.askstring('Held recovery reboot', 'Owner-recorded install-hold plan SHA-256:', parent=self.window)
            if not pin: return
            value = inputs(staging, accepted['staging_sha256'], source.probe.host, key, known,
                           source.probe.kernel, source.probe.serial, None, 0)
            reviewed(source, value, hold, pin.strip())
            parent = filedialog.askdirectory(parent=self.window, title='Parent for new private held-reboot evidence')
            if not parent: return
            if not messagebox.askyesno('Reboot once into persistent recovery',
                    f'Host: {source.probe.host}\nCurrent RAM boot: {source.boot_id}\nHold plan: {pin.strip()}\n\n'
                    'Renew the current session lease, verify held boot files and the recovery image read-only, '
                    'then reboot once and enroll a new persistent RAM boot. Keep physical power-cycle access available. '
                    'The new session is NOT leased; promptly approve its next job. Independent hold reconciliation '
                    'is still required before deployment. No root write or normal-boot release is authorized. '
                    'An uncertain reboot is never repeated; retain its evidence.', parent=self.window):
                return
            import uuid
            self.reboot_held(source, value, hold, pin.strip(), Path(parent)/('held-reboot-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Held reboot not submitted: ' + str(exc))

    def reboot_held(self, source, value, directory, pin, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before held reboot')
        submitted = self.controller.reboot_held_recovery(self.workspace, source, value, directory, pin, output)
        self.job, self.job_kind = submitted['job_id'], 'held-reboot-enroll'
        self.preparation_output = Path(output)
        self.enrollment_candidate = (Path(output)/'enrollment', value.probe.key, value.probe.known_hosts)
        self.enrollment_ready = None
        self.refresh()
        self.status.set('Guarded held reboot accepted. Keep Workbench open; no retry or root-write authority.')
        self.timer = self.window.after(100, self.poll)

    def reconcile_dialog(self):
        if self.job or self.pending or not self.enrollment_ready:
            return
        try:
            from forge_backup_policy import inputs as enrolled
            from forge_recovery_bootplan import digest
            from forge_reconcile_policy import inputs
            (directory, key, known), accepted = self.enrollment_ready
            source = enrolled(directory, digest(accepted), key, known)
            kind = simpledialog.askstring('Reconciliation kind',
                'Enter hold for a selector attempt, or root for deployment/restoration. Neither retries a write.',
                parent=self.window)
            if not kind: return
            journal = filedialog.askdirectory(parent=self.window, title='Original attempted plan journal')
            if not journal: return
            pin = simpledialog.askstring('Original plan pin', 'Owner-recorded plan.json SHA-256:', parent=self.window)
            if not pin: return
            reviewed = inputs(source, journal, pin.strip(), kind.strip())
            parent = filedialog.askdirectory(parent=self.window, title='Parent for unapproved reconciliation policy')
            if not parent: return
            if not messagebox.askyesno('Prepare observation policy only',
                    f"Original boot: {reviewed['original_boot_id']}\nObserved session: {source.boot_id}\n"
                    f"Plan: {pin.strip()}\n\n"
                    'Prepare an unapproved reconciliation policy without contacting the device or renewing a lease. '
                    'Later, separately approved execution fences the old worker, inspects held files and hashes the card. '
                    'Hold reconciliation may unmount an exact stale boot mount and flush prior pending writes. '
                    'It never retries a root write, repairs filesystems, reboots or releases normal boot. '
                    'Keep the original attempt and its uncertainty intact.', parent=self.window):
                return
            import uuid
            self.prepare_reconciliation(source, reviewed, Path(parent)/('reconciliation-policy-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Reconciliation draft not submitted: ' + str(exc))

    def prepare_reconciliation(self, source, reviewed, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before preparing reconciliation')
        submitted = self.controller.prepare_recovery_reconciliation(self.workspace, source, reviewed, output)
        self.job, self.job_kind = submitted['job_id'], 'prepare-reconciliation'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Preparing an observation-only policy offline. No target contact, lease renewal or write retry.')
        self.timer = self.window.after(100, self.poll)

    def root_dialog(self, operation):
        if self.job or self.pending or not self.enrollment_ready:
            return
        try:
            from forge_backup_policy import inputs as enrolled
            from forge_recovery_bootplan import digest
            from forge_root_policy import inputs
            (directory, key, known), accepted = self.enrollment_ready
            source = enrolled(directory, digest(accepted), key, known)
            choices = []
            for title, filename in (
                    ('Original install-hold plan journal', 'plan.json'),
                    ('Completed hold reconciliation journal', 'acceptance.json'),
                    ('Verified enhanced derivative' if operation == 'deploy-root' else 'Verified original backup source', 'manifest.json'),
                    ('Completed source filesystem-health evidence', 'acceptance.json')):
                path = filedialog.askdirectory(parent=self.window, title=title)
                if not path: return
                pin = simpledialog.askstring(title, f'Owner-recorded canonical SHA-256 of {filename}:', parent=self.window)
                if not pin: return
                choices.extend((path, pin.strip()))
            reviewed = inputs(source, *choices, operation)
            accept_errors = False
            if reviewed['source_filesystem_errors']:
                accept_errors = messagebox.askyesno('Original backup has filesystem errors',
                    'The retained original backup has recorded filesystem errors. Restore those exact original bytes '
                    'without repair? This is not a healthy-filesystem claim. Choose No to stop preparing this policy.',
                    default='no', parent=self.window)
                if not accept_errors: return
            parent = filedialog.askdirectory(parent=self.window, title='Parent for unapproved root-transfer plan and policy')
            if not parent: return
            if not messagebox.askyesno('Prepare root-transfer draft only',
                    f"Operation: {operation}\nBoot: {source.boot_id}\n"
                    f"Current root: {reviewed['root_before']['sha256']}\n"
                    f"Selected root: {reviewed['root_after']['sha256']}\n\n"
                    'Prepare a pinned plan and unapproved policy offline. No lease renewal, target write, reboot or '
                    'normal-boot release occurs now. After separate review/approval, running this job replaces root '
                    'partition bytes; keep the original backup and physical recovery access available. '
                    'Source, hold, protected ranges and target identity are independently rechecked during execution. '
                    'A failed attempt must be reconciled, never retried.', parent=self.window):
                return
            import uuid
            self.prepare_root(source, reviewed, Path(parent)/('root-policy-'+uuid.uuid4().hex),
                              accept_filesystem_errors=accept_errors)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Root-transfer draft not submitted: ' + str(exc))

    def prepare_root(self, source, reviewed, output, *, accept_filesystem_errors=False):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before preparing root transfer')
        submitted = self.controller.prepare_recovery_root(self.workspace, source, reviewed, output,
                                                          accept_filesystem_errors=accept_filesystem_errors)
        self.job, self.job_kind = submitted['job_id'], 'prepare-root'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Compiling pinned root-transfer evidence offline. No target writes, lease renewal or policy approval.')
        self.timer = self.window.after(100, self.poll)

    def release_dialog(self):
        if self.job or self.pending or not self.enrollment_ready:
            return
        try:
            from forge_backup_policy import inputs as enrolled
            from forge_recovery_bootplan import digest
            from forge_release_policy import inputs
            (directory, key, known), accepted = self.enrollment_ready
            source = enrolled(directory, digest(accepted), key, known)
            choices = []
            for title, filename in (('Original root deployment/restoration journal', 'plan.json'),
                    ('Completed independent root reconciliation', 'acceptance.json'),
                    ('Original hold review directory (normally sealed staging)', 'hold-review.json')):
                path = filedialog.askdirectory(parent=self.window, title=title)
                if not path: return
                pin = simpledialog.askstring(title, f'Owner-recorded canonical SHA-256 of {filename}:', parent=self.window)
                if not pin: return
                choices.extend((path, pin.strip()))
            backup = filedialog.askdirectory(parent=self.window, title='Retained original whole-card backup directory')
            if not backup: return
            reviewed = inputs(source, *choices, backup)
            accept_errors = False
            if reviewed['source_filesystem_errors']:
                accept_errors = messagebox.askyesno('Normal boot retains original filesystem errors',
                    'The independently matched original root has known source filesystem errors. Prepare a release '
                    'for those exact original bytes without repair? This does not certify filesystem health.',
                    default='no', parent=self.window)
                if not accept_errors: return
            parent = filedialog.askdirectory(parent=self.window, title='Parent for unapproved normal-release policy')
            if not parent: return
            if not messagebox.askyesno('Prepare normal-selector release draft only',
                    f"Boot: {source.boot_id}\nVerified root: {reviewed['root']['sha256']}\n"
                    f"Original attempt completion verified: {reviewed['original_attempt_completion_verified']}\n\n"
                    'Independent current-root and protected-range evidence is required even after a successful write. '
                    'This offline action creates only an unapproved selector-release plan. Running it after separate '
                    'review/approval changes normal boot selection, but does not reboot or write root bytes. '
                    'Native return and staged/private-artifact cleanup remain separate. Keep the original backup '
                    'and all uncertain-attempt evidence.', parent=self.window):
                return
            import uuid
            self.prepare_release(source, reviewed, Path(parent)/('normal-release-'+uuid.uuid4().hex),
                                 accept_filesystem_errors=accept_errors)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Release draft not submitted: ' + str(exc))

    def prepare_release(self, source, reviewed, output, *, accept_filesystem_errors=False):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before preparing release')
        submitted = self.controller.prepare_recovery_release(self.workspace, source, reviewed, output,
                                                             accept_filesystem_errors=accept_filesystem_errors)
        self.job, self.job_kind = submitted['job_id'], 'prepare-release'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Preparing normal-selector draft from independent root evidence. No lease renewal, selector write or reboot.')
        self.timer = self.window.after(100, self.poll)

    def normal_return_dialog(self, *, reboot=False):
        if self.job or self.pending:
            return
        try:
            from forge_backup_policy import inputs as enrolled
            from forge_recovery_bootplan import digest
            from forge_normal_return import inputs
            if self.enrollment_ready:
                (directory, key, known), accepted = self.enrollment_ready
                source = enrolled(directory, digest(accepted), key, known)
            elif self.normal_return_source:
                source = self.normal_return_source
            else:
                directory = filedialog.askdirectory(parent=self.window, title='Retained recovery enrollment directory')
                if not directory: return
                pin = simpledialog.askstring('Enrollment pin', 'Canonical SHA-256 of enrollment acceptance.json:', parent=self.window)
                if not pin: return
                key = filedialog.askopenfilename(parent=self.window, title='Private recovery key')
                if not key: return
                known = filedialog.askopenfilename(parent=self.window, title='Pinned recovery known_hosts')
                if not known: return
                source = enrolled(directory, pin.strip(), key, known)
            from forge_root_policy import record
            staging_pin = record(source.directory, 'acceptance.json')['staging_sha256']
            staging = filedialog.askdirectory(parent=self.window, title='Original sealed staging directory')
            if not staging: return
            release = filedialog.askdirectory(parent=self.window, title='Acknowledged release-hold plan journal')
            if not release: return
            pin = simpledialog.askstring('Selector release pin', 'Canonical SHA-256 of release plan.json:', parent=self.window)
            if not pin: return
            reviewed = inputs(source, staging, staging_pin, release, pin.strip())
            parent = filedialog.askdirectory(parent=self.window, title='Parent for new normal-return evidence')
            if not parent: return
            question = (f"Reboot {source.probe.host} once, then verify native return at {reviewed['normal_host']}?\n\n"
                'The current lease is renewed; the released boot files and private image are checked read-only and '
                'unmounted before reboot. A timeout never permits a second reboot. Keep physical recovery access available. '
                if reboot else f"Read-only verification of native return at {reviewed['normal_host']}?\n\n"
                'No reboot, lease renewal, target write or replay will occur. ')
            question += ('A fresh boot, normal root, machine identity, kernel and serial must match. '
                         'This does not prove application behavior or filesystem health, and does not clean up boot artifacts.')
            if not messagebox.askyesno('Normal return', question, parent=self.window): return
            import uuid
            self.return_normal(source, reviewed, Path(parent)/('normal-return-'+uuid.uuid4().hex), reboot=reboot)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Normal return not submitted: ' + str(exc))

    def return_normal(self, source, reviewed, output, *, reboot=False):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before normal return')
        submitted = self.controller.return_recovery_to_normal(self.workspace, source, reviewed, output, reboot=reboot)
        self.job, self.job_kind = submitted['job_id'], 'normal-return'
        self.preparation_output = Path(output)
        self.normal_return_source = source
        if reboot: self.enrollment_ready = None
        self.refresh()
        self.status.set('Verifying normal return. No automatic reboot retry; retain private boot artifacts until ordered cleanup.')
        self.timer = self.window.after(100, self.poll)

    def cleanup_dialog(self):
        if self.job or self.pending: return
        try:
            from forge_recovery_cleanup import inputs
            staging = filedialog.askdirectory(parent=self.window, title='Original sealed staging directory')
            if not staging: return
            pin = simpledialog.askstring('Staging pin', 'Canonical SHA-256 of staging acceptance.json:', parent=self.window)
            if not pin: return
            boot = simpledialog.askstring('Verified normal boot', 'Boot UUID from successful normal-return evidence:', parent=self.window)
            if not boot: return
            reviewed = inputs(staging, pin.strip(), boot.strip())
            parent = filedialog.askdirectory(parent=self.window, title='Parent for new cleanup evidence')
            if not parent: return
            if not messagebox.askyesno('Ordered recovery cleanup',
                    f"Restore staged boot files and remove the owned recovery image at {reviewed['host']}?\n\n"
                    'All four phases are restored in reverse order. Previously confirmed restores are skipped; '
                    'uncertain operations must be reconciled, never retried. All nine original preimages and '
                    'the exact normal boot are checked before removing the journal-owned image. '
                    'No root writes or reboot. Boot-mount permissions remain private; this does not restore public access.',
                    parent=self.window): return
            import uuid
            self.cleanup_recovery(reviewed, Path(parent)/('recovery-cleanup-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Cleanup not submitted: ' + str(exc))

    def cleanup_recovery(self, reviewed, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before cleanup')
        submitted = self.controller.cleanup_recovery(self.workspace, reviewed, output)
        self.job, self.job_kind = submitted['job_id'], 'recovery-cleanup'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Restoring staging, then removing the owned private image. Boot permissions remain private.')
        self.timer = self.window.after(100, self.poll)

    def privacy_restore_dialog(self):
        if self.job or self.pending: return
        try:
            from forge_boot_privacy_restore import inputs
            staging = filedialog.askdirectory(parent=self.window, title='Original sealed staging directory')
            if not staging: return
            stage_pin = simpledialog.askstring('Staging pin', 'Canonical SHA-256 of staging acceptance.json:', parent=self.window)
            if not stage_pin: return
            boot = simpledialog.askstring('Verified normal boot', 'Boot UUID from completed cleanup:', parent=self.window)
            if not boot: return
            journal = filedialog.askdirectory(parent=self.window, title='Original privacy transaction directory (contains plan.json)')
            if not journal: return
            pin = simpledialog.askstring('Original privacy plan pin', 'Canonical SHA-256 of original privacy plan.json:', parent=self.window)
            if not pin: return
            reviewed = inputs(staging, stage_pin.strip(), boot.strip(), journal, pin.strip())
            if not messagebox.askyesno('Restore original boot policy',
                    f"Restore the original /etc/fstab on {reviewed['plan']['host']}?\n\n"
                    'The original journals must confirm all staging restores and image removal. '
                    'The target checks boot preimages and inventories the private boot filesystem before and after this change. '
                    'Forge/recovery-named artifacts, links and renamed copies of the known credential image stop restoration. '
                    'This is not a general secret scan of unrelated user files. No files are deleted and no reboot or remount '
                    'occurs. Current permissions remain private until a subsequent fresh mount is verified. '
                    'Uncertain restoration must be inspected, never replayed.', parent=self.window, default='no'): return
            self.restore_privacy(reviewed)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Original boot policy restoration not submitted: '+str(exc))

    def restore_privacy(self, reviewed):
        if self.job or self.pending: raise ValueError('Finish the current job or review before privacy restoration')
        submitted = self.controller.restore_boot_privacy(self.workspace, reviewed)
        self.job, self.job_kind = submitted['job_id'], 'restore-privacy'
        self.preparation_output = Path(reviewed['journal'])
        self.refresh()
        self.status.set('Checking boot artifacts and restoring original fstab. No reboot or remount.')
        self.timer = self.window.after(100, self.poll)

    def original_mount_dialog(self, *, reboot=False):
        if self.job or self.pending: return
        try:
            from forge_boot_mount_restore import inputs
            directory = filedialog.askdirectory(parent=self.window, title='Original privacy transaction journal')
            if not directory: return
            pin = simpledialog.askstring('Original privacy plan pin', 'Canonical SHA-256 of original privacy plan.json:', parent=self.window)
            if not pin: return
            reviewed = inputs(directory, pin.strip())
            question = (f"Reboot {reviewed['plan']['host']} once to restore original boot permissions?\n\n"
                        'The worker rechecks original fstab, boot preimages, private mount and artifact inventory before reboot. '
                        'A timeout never permits another reboot. Keep physical recovery access available. '
                        if reboot else f"Read-only verification of original permissions at {reviewed['plan']['host']}?\n\n"
                        'No reboot, remount or file writes will occur. ')
            question += ('Verification requires a new normal boot, effective original FAT permissions and non-root access, '
                         'unchanged boot inventory and restored preimages. This does not qualify filesystem health or application behavior.')
            if not messagebox.askyesno('Original boot permissions', question, parent=self.window, default='no'): return
            self.verify_original_mount(reviewed, reboot=reboot)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Original permissions verification not submitted: '+str(exc))

    def verify_original_mount(self, reviewed, *, reboot=False):
        if self.job or self.pending: raise ValueError('Finish the current job or review before mount verification')
        submitted = self.controller.verify_original_boot_mount(self.workspace, reviewed, reboot=reboot)
        self.job, self.job_kind = submitted['job_id'], 'verify-original-mount'
        self.preparation_output = Path(reviewed['journal'])
        self.refresh()
        self.status.set('Verifying a fresh normal boot and original mount permissions. No automatic reboot retry.')
        self.timer = self.window.after(100, self.poll)

    def source_dialog(self, *, health=False):
        if self.job or self.pending:
            return
        try:
            from forge_backup_source import inputs
            directory = filedialog.askdirectory(parent=self.window, title='Completed retained card backup')
            if not directory: return
            pin = simpledialog.askstring('Backup receipt pin', 'Owner-recorded canonical SHA-256 of acceptance.json:',
                                         parent=self.window)
            if not pin: return
            reviewed = inputs(directory, pin.strip())
            parent = filedialog.askdirectory(parent=self.window, title='Storage parent for offline source evidence')
            if not parent: return
            if health:
                from forge_recovery_archive import RESERVE_BYTES
                effect = (f"Needs at least {2 * reviewed['card']['bytes'] + RESERVE_BYTES:,} free bytes. "
                          'Retains a complete image plus partition copies and read-only FAT/ext4 checker logs. '
                          'No repairs, mounts or deployment approval. Nonzero checker results remain visible as errors. ')
            else:
                effect = ('Creates range hashes and a root-chunk manifest, not another card image. '
                          'Filesystem health and deployment approval remain separate. ')
            if not messagebox.askyesno('Check backup filesystems' if health else 'Verify retained backup source',
                    f"Read this retained backup?\n\n{directory}\n"
                    f"Card bytes: {reviewed['card']['bytes']}\nCard SHA-256: {reviewed['card']['sha256']}\n"
                    f"Plan SHA-256: {reviewed['plan_sha256']}\n\n"
                    + effect + 'No target contact, lease renewal, filesystem repair or write approval occurs. '
                    'This does not keep a recovery boot alive; manage that session separately.', parent=self.window):
                return
            import uuid
            prefix = 'backup-health-' if health else 'backup-source-'
            self.prepare_source(reviewed, Path(parent)/(prefix+uuid.uuid4().hex), health=health)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Backup source not submitted: ' + str(exc))

    def prepare_source(self, reviewed, output, *, health=False):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before preparing a source')
        action = self.controller.check_backup_filesystems if health else self.controller.prepare_backup_source
        submitted = action(self.workspace, reviewed, output)
        self.job, self.job_kind = submitted['job_id'], 'check-health' if health else 'prepare-source'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set(('Checking private filesystem copies without repair. ' if health else
                         'Verifying retained backup bytes and root chunks. ') + 'No target contact or lease renewal.')
        self.timer = self.window.after(100, self.poll)

    def derivative_dialog(self):
        if self.job or self.pending:
            return
        try:
            from forge_derivative_prepare import inputs
            backup = filedialog.askdirectory(parent=self.window, title='Original retained card backup')
            if not backup: return
            source = filedialog.askdirectory(parent=self.window, title='Original root source directory (contains manifest.json)')
            if not source: return
            pin = simpledialog.askstring('Original source pin', 'Owner-recorded canonical SHA-256 of manifest.json:',
                                         parent=self.window)
            if not pin: return
            image = filedialog.askopenfilename(parent=self.window, title='Private exported edited card image')
            if not image: return
            reviewed = inputs(backup, source, pin.strip(), image)
            parent = filedialog.askdirectory(parent=self.window, title='Storage parent for export-lineage evidence')
            if not parent: return
            if not messagebox.askyesno('Verify export against rollback source',
                    f"Export: {image}\nBackup: {backup}\nOriginal manifest: {reviewed['source_sha256']}\n\n"
                    'Read the full export and original archive. Only root-partition changes are accepted; '
                    'partition layout and all protected bytes must match. No image copy, filesystem check, '
                    'target contact, lease renewal or deployment approval occurs. Keep the export stopped '
                    'and unchanged; retain the original backup for rollback.', parent=self.window):
                return
            import uuid
            self.prepare_derivative(reviewed, Path(parent)/('export-lineage-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Export verification not submitted: ' + str(exc))

    def prepare_derivative(self, reviewed, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before verifying an export')
        submitted = self.controller.prepare_export_derivative(self.workspace, reviewed, output)
        self.job, self.job_kind = submitted['job_id'], 'prepare-derivative'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Verifying export lineage against retained rollback bytes. No target contact or lease renewal.')
        self.timer = self.window.after(100, self.poll)

    def export_health_dialog(self):
        if self.job or self.pending:
            return
        try:
            from forge_recovery_derivative import load
            from forge_recovery_archive import RESERVE_BYTES
            directory = filedialog.askdirectory(parent=self.window, title='Verified derivative directory (contains manifest.json)')
            if not directory: return
            pin = simpledialog.askstring('Derivative pin', 'Owner-recorded canonical SHA-256 of manifest.json:',
                                         parent=self.window)
            if not pin: return
            pin = pin.strip()
            manifest, _ = load(directory, pin)
            parent = filedialog.askdirectory(parent=self.window, title='Storage parent for retained root copy and checker evidence')
            if not parent: return
            if not messagebox.askyesno('Check exported root without repair',
                    f"Derivative: {pin}\nRoot SHA-256: {manifest['root']['sha256']}\n"
                    f"Required free bytes: {manifest['root']['bytes'] + RESERVE_BYTES:,}\n\n"
                    'Reverify the export and rollback lineage, retain a private root copy, and run e2fsck '
                    'read-only. No mount, repair, boot-filesystem check, target contact or lease renewal. '
                    'Nonzero checker results remain unqualified. This grants no deployment authority.',
                    parent=self.window):
                return
            import uuid
            self.check_export_health(directory, pin, Path(parent)/('export-health-'+uuid.uuid4().hex))
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Export filesystem check not submitted: ' + str(exc))

    def check_export_health(self, directory, pin, output):
        if self.job or self.pending:
            raise ValueError('Finish the current job or review before checking export health')
        submitted = self.controller.check_export_root(self.workspace, directory, pin, output)
        self.job, self.job_kind = submitted['job_id'], 'check-export-health'
        self.preparation_output = Path(output)
        self.refresh()
        self.status.set('Reverifying export and checking its private root copy without repair or target contact.')
        self.timer = self.window.after(100, self.poll)

    def load_policy(self, filename):
        if self.job:
            raise ValueError('Wait for the recovery job before reviewing another policy')
        pin = policy_pin(filename)
        approved = self.controller.load_recovery_policy(filename, pin)
        self.pending = (Path(filename), pin)
        self.refresh()
        # Review all jobs, including other registered workspaces, before granting
        # the owner authority. Existing clients never gain a new grant implicitly.
        self.show(dict(policy_sha256=pin, jobs=[summary(item) for item in approved.jobs.values()]))
        self.status.set('Review every host, identity, operation, path and pin. Nothing has been approved or contacted.')

    def approve(self):
        if not self.pending or self.job:
            return
        filename, pin = self.pending
        if not messagebox.askyesno('Approve recovery policy',
                'Approve exactly the displayed policy?\n\nSHA-256: ' + pin +
                '\n\nThis enables later execution, but starts no action. Already authorized target-recovery clients can select its jobs.',
                parent=self.window):
            return
        try:
            self.controller.approve_recovery_policy(filename, pin)
            self.pending = None
            self.refresh()
            self.status.set('Policy approved. Select a job and review its effect before execution.')
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Approval failed; no action started: ' + str(exc))

    def start(self):
        if self.job or self.pending:
            return
        try:
            registry = self.controller.recovery_jobs
            job = registry.get(self.selected.get(), self.workspace)
            if not messagebox.askyesno('Confirm physical recovery action',
                    f'{job.operation} on {job.probe.host} (serial {job.probe.serial})?\n\n' + WARNINGS[job.operation] +
                    '\n\nRunning jobs cannot be cancelled. Failure can leave uncertain disk state. No automatic retry, rollback or reboot will occur.',
                    parent=self.window):
                return
            # A modal dialog processes GUI events. Reject a policy replacement
            # rather than execute a different definition under the same name.
            if self.controller.recovery_jobs is not registry:
                raise ValueError('Recovery policy changed during confirmation; review it again')
            submitted = self.controller.submit_recovery(self.workspace, job.name)
            self.job_kind = 'recovery'
            self.job = submitted['job_id']
            self.refresh()
            self.status.set(f'{job.operation}: waiting for job {self.job}. Keep Workbench open.')
            self.timer = self.window.after(100, self.poll)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Job not submitted: ' + str(exc))

    def poll(self):
        if self.timer:
            self.window.after_cancel(self.timer)
            self.timer = None
        if not self.job:
            return
        try:
            result = self.controller.job(self.job)
            report_job_error(self.window, result)
        except Exception as exc:
            report_ui_error(self.window, 'forge_recovery_gui', exc)
            self.status.set('Status unavailable; retain this job and recheck. Do not resubmit: ' + str(exc))
            return
        if result['status'] not in ('completed', 'failed', 'cancelled'):
            self.timer = self.window.after(250, self.poll)
            return
        self.job = None
        self.refresh()
        self.show(result)
        if self.job_kind == 'verify-original-mount':
            self.status.set(result['status'] + f': final mount evidence under {self.preparation_output}. '
                            'After uncertain reboot use read-only Verify original permissions; never repeat reboot. '
                            'Mount verification does not qualify filesystem health or application behavior.')
            return
        if self.job_kind == 'restore-privacy':
            self.status.set(result['status'] + f': original-policy evidence at {self.preparation_output}. '
                            'Current mount remains private; original permissions need fresh-mount verification. '
                            'Never replay an uncertain restoration.')
            return
        if self.job_kind == 'recovery-cleanup':
            self.status.set(result['status'] + f': cleanup evidence at {self.preparation_output}. '
                            'Boot-mount permissions remain private. Uncertain operations require reconciliation, not retry.')
            return
        if self.job_kind == 'normal-return':
            if result['status'] == 'completed' and result['result'].get('status') == 'verified-normal-return':
                self.enrollment_ready = None
                self.refresh()
            self.status.set(result['status'] + f': native-return evidence at {self.preparation_output}. '
                            'No application, filesystem-health or cleanup qualification is implied. '
                            'After uncertain reboot, use Verify normal return; never repeat the reboot. '
                            'Keep private mount policy until staged and credential-bearing artifacts are removed.')
            return
        if self.job_kind == 'check-export-health':
            checked = result.get('result') or {}
            outcome = ('Root filesystem check passed.' if checked.get('root_filesystem_consistency_qualified') is True else
                       'Root filesystem health is NOT qualified; inspect retained checker evidence.')
            self.status.set(result['status'] + f': export-root evidence at {self.preparation_output}. '
                            + outcome + ' No boot-filesystem check, repair, lease renewal or deployment approval occurred.')
            return
        if self.job_kind == 'prepare-derivative':
            self.status.set(result['status'] + f': export-lineage evidence at {self.preparation_output}. '
                            'Only root-partition changes qualify. Filesystem health and physical boot remain unqualified; '
                            'no target contact, lease renewal or deployment approval occurred.')
            return
        if self.job_kind == 'check-health':
            checked = result.get('result') or {}
            outcome = ('Filesystem checks passed.' if checked.get('filesystem_consistency_qualified') is True else
                       'Filesystem health is NOT qualified; inspect retained errors and checker logs.')
            self.status.set(result['status'] + f': backup filesystem evidence at {self.preparation_output}. '
                            + outcome + ' No repair, target contact, lease renewal or restore approval occurred.')
            return
        if self.job_kind == 'prepare-source':
            self.status.set(result['status'] + f': backup-source evidence at {self.preparation_output}. '
                            'Byte verification is not filesystem health or restore approval. '
                            'No target contact or lease renewal occurred; retain all failure evidence.')
            return
        if self.job_kind in ('tryboot-enroll', 'held-reboot-enroll'):
            expected = ('held-boot-enrolled-not-leased' if self.job_kind == 'held-reboot-enroll'
                        else 'recovery-boot-enrolled-not-leased')
            if result['status'] == 'completed' and result['result'].get('status') == expected:
                self.enrollment_ready = (self.enrollment_candidate, result['result']['enrollment'])
                self.backup_button.state(['!disabled'])
                self.hash_button.state(['!disabled'])
                self.hold_button.state(['!disabled'])
                self.held_reboot_button.state(['!disabled'])
                self.reconcile_prepare_button.state(['!disabled'])
                self.deploy_prepare_button.state(['!disabled'])
                self.restore_prepare_button.state(['!disabled'])
                self.release_prepare_button.state(['!disabled'])
            self.status.set(result['status'] + f': recovery boot evidence at {self.preparation_output}. '
                            'New session is not leased; no root writes authorized. Promptly approve its next job; '
                            'held boots require independent hold reconciliation before deployment. '
                            'Unleased recovery can expire; retain any failed attempt and never automatically reboot again.')
            return
        if self.job_kind == 'verify-privacy':
            self.status.set(result['status'] + f': private-mount evidence at {self.preparation_output}. '
                            'Only verified-private-normal-boot qualifies effective privacy, not recovery fallback. '
                            'After uncertain reboot, use read-only Verify private mount; never resubmit the reboot.')
            return
        if self.job_kind in ('prepare-privacy', 'apply-privacy'):
            self.status.set(result['status'] + f': privacy evidence at {self.preparation_output}. '
                            'No remount, reboot or publication performed. Applied policy is not proof of effective private permissions; '
                            'retain the original fstab and verify a later fresh mount before publishing credentials.')
            return
        if self.job_kind == 'discover-builder':
            if result['status'] == 'completed':
                self.build_candidate = dict(result['result'], host=self.build_host)
                self.build_button.state(['!disabled'])
                self.show(self.build_candidate)
            self.status.set(result['status'] + ': read-only discovery. Review identity, then separately choose Build private recovery image.')
            return
        if self.job_kind == 'build-image':
            self.status.set(result['status'] + f': private build evidence at {self.preparation_output}. '
                            'Not published or boot-qualified. Retain failed host/target journals; do not automatically retry.')
            return
        if self.job_kind == 'prepare-publication':
            self.status.set(result['status'] + f': publication draft/evidence at {self.preparation_output}. '
                            'Not published or approved. Review separately with Publish reviewed image.')
            return
        if self.job_kind == 'publish-image':
            self.status.set(result['status'] + f': publication evidence at {self.preparation_output}. '
                            'No reboot or root-write authority. Inspect uncertain publication; never automatically retry. '
                            'Only published-not-boot-qualified confirms this publication; fallback remains separate.')
            return
        if self.job_kind == 'enroll-session':
            if result['status'] == 'completed' and result['result'].get('status') == 'enrolled-not-leased':
                self.enrollment_ready = (self.enrollment_candidate, result['result'])
                self.backup_button.state(['!disabled'])
                self.hash_button.state(['!disabled'])
                self.hold_button.state(['!disabled'])
                self.held_reboot_button.state(['!disabled'])
                self.reconcile_prepare_button.state(['!disabled'])
                self.deploy_prepare_button.state(['!disabled'])
                self.restore_prepare_button.state(['!disabled'])
                self.release_prepare_button.state(['!disabled'])
            self.status.set(result['status'] + f': enrollment evidence at {self.preparation_output}. '
                            'Only enrolled-not-leased is a completed binding; use its session directory and pin '
                            'in a separately reviewed job policy, or choose Prepare backup job. No lease, reboot or grant was acquired.')
            return
        if self.job_kind in ('prepare-backup', 'prepare-hash', 'prepare-hold', 'prepare-reconciliation', 'prepare-root', 'prepare-release'):
            kind = {'prepare-backup': 'Backup', 'prepare-hash': 'Hash', 'prepare-hold': 'Hold',
                    'prepare-reconciliation': 'Reconciliation', 'prepare-root': 'Root transfer',
                    'prepare-release': 'Normal release'}[self.job_kind]
            if result['status'] == 'completed':
                try:
                    filename = self.preparation_output/'policy.json'
                    if policy_pin(filename) != result['result']['policy_sha256']:
                        raise ValueError(kind + ' draft changed after preparation')
                    self.load_policy(filename)
                    self.status.set(kind + '-only draft ready for review. Approve separately, then run the selected job. '
                                    + {'Backup': 'No backup exists yet.', 'Hash': 'No full-card hashes captured yet.',
                                       'Hold': 'No hold installed, lease renewed or deployment approved.',
                                       'Reconciliation': 'No observation run, lease renewed, write retried or hold released.',
                                       'Root transfer': 'No root bytes written, lease renewed or hold released.',
                                       'Normal release': 'No selector changed, lease renewed or reboot performed.'}[kind])
                except Exception as exc:
                    report_ui_error(self.window, 'forge_recovery_gui', exc)
                    self.status.set(kind + ' draft review failed: ' + str(exc))
            else:
                self.status.set(kind + ' draft failed; evidence retained. No policy approved, lease renewed or card operation run.')
            return
        if self.job_kind == 'prepare-staging':
            self.status.set(result['status'] + f': retained preparation artifacts at {self.preparation_output}. '
                            'Drafts are not approved, staged, or boot-qualified. No automatic retry or deployment.')
            return
        self.status.set(result['status'] + ': evidence retained. No rollback or reboot is implied. '
                        'Read the recorded selector result; reconcile uncertain writes and never automatically retry a failed action.')

    def close(self):
        if self.job:
            self.status.set('Keep this panel open until the accepted job reaches a known terminal state.')
            return False
        if self.timer:
            self.window.after_cancel(self.timer)
        self.window.destroy()
        return True
