"""Owner review of sealed staging; policy approval is separate from execution."""
import json
import os
from pathlib import Path
import re
from tkinter import filedialog, messagebox, simpledialog
import uuid

from forge_recovery_stage_review import load
from forge_target_journal import private_directory, write_record


def summary(approved):
    reviewed = load(approved.journal, approved.authorization_sha256)
    plan = reviewed['plans'][reviewed['review']['apply_order'].index(approved.phase)]
    return dict(kind='recovery-stage', host=plan['host'], journal=str(approved.journal),
                authorization_sha256=approved.authorization_sha256, phase=approved.phase,
                boot_id=approved.boot_id, paths=[plan['before']['files'][-1]['path']],
                guarded_paths=reviewed['review']['guarded_paths'],
                apply_order=reviewed['review']['apply_order'], restore_order=reviewed['review']['restore_order'],
                reboot_authorized=False, root_write_authorized=False)


def review_dialog(panel):
    if panel.job is not None:
        panel.status.set('Wait for the current target job.')
        return
    try:
        directory = filedialog.askdirectory(parent=panel.window, title='Sealed recovery staging preparation')
        if not directory: return
        pin = simpledialog.askstring('Preparation pin', 'Owner-recorded canonical SHA-256 of acceptance.json:', parent=panel.window)
        if not pin: return
        directory = Path(directory).absolute()
        reviewed = load(directory, pin)
        boot = simpledialog.askstring('Approved normal boot',
            'Exact normal boot UUID to approve for later Apply/Restore.\n'
            'After a reboot, independently verify the new normal boot before approving it:',
            initialvalue=reviewed['original_boot']['boot_id'], parent=panel.window)
        if not boot: return
        if not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot):
            raise ValueError('An explicit normal boot UUID is required')
        panel.pending = dict(kind='recovery-stage', journal=str(directory), authorization_sha256=pin,
            host=reviewed['request']['image_plan']['host'], boot_id=boot,
            apply_order=reviewed['review']['apply_order'], restore_order=reviewed['review']['restore_order'],
            guarded_paths=reviewed['review']['guarded_paths'], deployment_authorized=False)
        panel.show_details(json.dumps(panel.pending, indent=2))
        panel.approve_button.state(['!disabled'])
        panel.status.set('Sealed staging reviewed locally. Separate approval and per-phase confirmation are required.')
    except Exception as exc:
        from workbench_diagnostics import report_ui_error
        report_ui_error(panel.window, 'Review recovery staging', exc)
        panel.pending = None
        panel.approve_button.state(['disabled'])
        panel.status.set(str(exc))


def approve(panel, review):
    review = json.loads(json.dumps(review))
    reviewed = load(review['journal'], review['authorization_sha256'])
    host = reviewed['request']['image_plan']['host']
    if host != review['host']:
        raise ValueError('Staging target changed after review')
    previous = panel.controller.targets
    if not messagebox.askyesno('Approve recovery staging phases',
            f'Approve four staging phases on {host}?\n\nNormal boot: {review["boot_id"]}\n'
            f'Preparation SHA-256: {review["authorization_sha256"]}\n\n'
            'This enables later Apply/Restore and explicit reconciliation, but writes no target files now. '
            'Existing target-write clients may invoke these phases. No reboot, hold release or root deployment '
            'is authorized. Apply requires the prepared boot; restore after reboot needs new owner approval.',
            parent=panel.window):
        return
    if panel.controller.targets is not previous:
        raise ValueError('Target policy changed during confirmation; review again')
    load(review['journal'], review['authorization_sha256'])
    transactions = {item.name: item.definition() for item in previous.transactions.values()} if previous else {}
    prefix = 'staging-'+uuid.uuid4().hex+'-'
    for phase in reviewed['review']['apply_order']:
        transactions[prefix+phase] = dict(kind='recovery-stage', workspace=panel.workspace,
            journal=review['journal'], authorization_sha256=review['authorization_sha256'],
            boot_id=review['boot_id'], phase=phase)
    directory = Path(review['journal'])
    filename = 'approved-staging-policy-'+uuid.uuid4().hex+'.json'
    fd = private_directory(directory)
    try: pin = write_record(fd, filename, dict(schema=1, transactions=transactions))
    finally: os.close(fd)
    panel.controller.approve_target_policy(directory/filename, pin)
    panel.choice.configure(values=list(transactions))
    panel.selected.set(prefix+reviewed['review']['apply_order'][0])
    panel.pending = None
    panel.approve_button.state(['disabled'])
    for button in panel.buttons: button.state(['!disabled'])
    panel.review()
    panel.status.set(f'Staging approved, not executed. Retain {directory/filename}; SHA-256 {pin}')


def reconcile(panel, direction):
    if panel.job is not None:
        panel.status.set('Wait for the current target job; do not resubmit it.')
        return
    try:
        selected = panel.selected.get()
        details = panel.plan_summary()
        if details.get('kind') != 'recovery-stage':
            raise ValueError('Select a recovery staging phase to reconcile')
        if not messagebox.askyesno('Reconcile staging attempt',
                f'Fence and inspect the attempted {direction} of {details["phase"]} on {details["host"]}?\n\n'
                'This does not retry a write or reboot. Inspect conflicts and requires_new_boot in the result.',
                parent=panel.window):
            return
        if panel.selected.get() != selected or panel.plan_summary() != details:
            raise ValueError('Selected staging approval changed during confirmation')
        submitted = panel.controller.submit_target_staging_reconcile(panel.workspace, selected, direction)
        panel.job, panel.job_kind = submitted['job_id'], 'staging-reconcile'
        panel.choice.configure(state='disabled')
        for button in panel.buttons: button.state(['disabled'])
        panel.status.set('Reconciling the retained staging attempt; no retry or reboot.')
        panel.timer = panel.window.after(100, panel.poll)
    except Exception as exc:
        from workbench_diagnostics import report_ui_error
        report_ui_error(panel.window, 'Reconcile recovery staging', exc)
        panel.status.set(str(exc))
