"""Actual Tk-button qualification for a preapproved inert target fixture."""
import hashlib
import json
from pathlib import Path
import threading
import time
import tkinter as tk
from unittest.mock import patch

from uconsole_workbench import Workbench


class GUITransitions:
    def __init__(self, output, prepared, *, agent_socket=None, agent_grants=()):
        self.output, self.prepared = Path(output), prepared
        self.agent_socket, self.agent_grants = agent_socket, agent_grants
        self.root = self.app = self.panel = None
        self.history_patch = None

    @property
    def pin(self):
        return 'authorization_sha256' if self.prepared.get('kind') == 'service' else 'plan_sha256'

    def start(self):
        policy = self.output / 'target-gui-policy.json'
        with policy.open('x') as stream:
            entry = {'workspace': 'gui', 'journal': self.prepared['journal'], self.pin: self.prepared[self.pin]}
            if self.pin == 'authorization_sha256':
                entry['kind'] = 'service'
            json.dump({'schema': 1, 'transactions': {'proof': entry}}, stream)
        self.policy_sha256 = hashlib.sha256(policy.read_bytes()).hexdigest()
        self.history_patch = patch('uconsole_workbench.history_default_path',
                                   return_value=self.output / '.gui-history/jobs.sqlite3')
        self.history_patch.start()
        try:
            self.root = tk.Tk()
            self.app = Workbench(self.root, self.output, target_policy=policy,
                                 target_policy_sha256=self.policy_sha256,
                                 agent_socket=self.agent_socket, agent_grants=self.agent_grants)
            self.panel = self.app.physical_target()
            self.root.update()
            return self
        except BaseException:
            self.close()
            raise

    def transition(self, direction):
        # This validator only receives the prevalidated inert proof fixture.
        # Record and approve its real dialog rather than bypassing panel.start.
        confirmations = []

        def confirm(title, message, **kwargs):
            confirmations.append({'title': title, 'message': message})
            return True

        with patch('forge_target_gui.messagebox.askyesno', side_effect=confirm):
            self.panel.buttons[0 if direction == 'apply' else 1].invoke()
        job_id = self.panel.job
        if not job_id or len(confirmations) != 1:
            raise ValueError('Physical GUI did not submit the confirmed transaction: ' + self.panel.status.get())
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self.root.update()
            result = self.app.controller.job(job_id)
            if result['status'] in ('completed', 'failed', 'cancelled') and self.panel.job is None:
                evidence = {'job': result, 'confirmation': confirmations[0],
                            'panel_status': self.panel.status.get(),
                            'plan_summary': self.panel.details.get('1.0', 'end')}
                with (self.output / ('gui-' + direction + '.json')).open('x') as stream:
                    json.dump(evidence, stream, indent=2)
                if result['status'] != 'completed':
                    raise ValueError('GUI hardware job failed; inspect journal: ' + str(result))
                if (result['context'][self.pin] != self.prepared[self.pin] or
                        result['context']['policy_sha256'] != self.policy_sha256):
                    raise ValueError('GUI job approval digest mismatch')
                return evidence
            threading.Event().wait(0.05)
        raise TimeoutError('GUI target job deadline; retain journal, no rollback implied')

    def close(self):
        try:
            if self.app is not None and self.app.controller is not None:
                self.app.close_attachment()
                # Drain accepted jobs; do not force-cancel a hardware operation.
                self.app.controller.close()
                self.app.controller = None
        finally:
            if self.root is not None:
                self.root.destroy()
                self.root = None
            if self.history_patch is not None:
                self.history_patch.stop()
                self.history_patch = None


class GUIAuthorTransitions(GUITransitions):
    """Prepare and approve via real GUI controls, without a preloaded policy."""
    def __init__(self, output, host, source, proof, digest):
        super().__init__(output, None)
        self.host, self.source, self.proof, self.digest = host, Path(source).resolve(), proof, digest

    def start(self):
        self.history_patch = patch('uconsole_workbench.history_default_path',
                                   return_value=self.output / '.gui-history/jobs.sqlite3')
        self.history_patch.start()
        evidence = {'status': 'failed', 'preloaded_policy': False, 'dialogs': []}
        try:
            self.root = tk.Tk()
            self.app = Workbench(self.root, self.output)
            self.panel = self.app.physical_target()
            self.root.update()
            path = '/usr/local/bin/' + self.proof

            def confirm(title, message, **kwargs):
                answer = title != 'Another artifact'
                evidence['dialogs'].append({'title': title, 'message': message, 'answer': answer})
                return answer

            with patch('forge_target_gui.simpledialog.askstring', side_effect=[self.host, path, '0755']), \
                    patch('forge_target_gui.filedialog.askopenfilename', return_value=str(self.source)), \
                    patch('forge_target_gui.filedialog.askdirectory', return_value=str(self.output)), \
                    patch('forge_target_gui.messagebox.askyesno', side_effect=confirm):
                self.panel.prepare_button.invoke()
            job_id = self.panel.job
            if not job_id:
                raise ValueError('GUI preparation did not start: ' + self.panel.status.get())
            deadline = time.monotonic() + 120
            while self.panel.job is not None and time.monotonic() < deadline:
                self.root.update()
                threading.Event().wait(0.05)
            if self.panel.job is not None or self.panel.pending is None:
                raise ValueError('GUI preparation failed or timed out; inspect retained controller history')
            evidence['preparation_job'] = self.app.controller.job(job_id)
            self.prepared = dict(self.panel.pending)
            evidence['review'] = self.prepared
            if (self.prepared['host'] != self.host or self.prepared['deployment_performed'] or
                    self.prepared['files'][0]['before'] != {'kind': 'absent'} or
                    self.prepared['files'][0]['after']['sha256'] != self.digest or
                    len(self.prepared['files']) != 1 or self.prepared['files'][0]['path'] != path):
                raise ValueError('Prepared review differs from the inert fixture')
            if 'target-write' in self.app.controller.grants:
                raise ValueError('Preparation unexpectedly granted physical writes')
            with patch('forge_target_gui.messagebox.askyesno', side_effect=confirm):
                self.panel.approve_button.invoke()
            if self.panel.pending is not None or 'target-write' not in self.app.controller.grants:
                raise ValueError('GUI approval failed: ' + self.panel.status.get())
            self.policy_sha256 = self.app.controller.targets.sha256
            evidence['policy_sha256'] = self.policy_sha256
            evidence['transaction_name'] = self.panel.selected.get()
            evidence['status'] = 'prepared-and-approved-not-deployed'
            return self
        except BaseException as exc:
            evidence['error'] = str(exc)
            self.close()
            raise
        finally:
            with (self.output / 'gui-author.json').open('x') as stream:
                json.dump(evidence, stream, indent=2)


class GUIServiceAuthorTransitions(GUITransitions):
    """Exercise service preparation and approval through real GUI buttons."""
    def __init__(self, output, host, source, unit, digest):
        super().__init__(output, None)
        self.host, self.source, self.unit, self.digest = host, Path(source).resolve(), unit, digest

    def wait_panel(self):
        job_id = self.panel.job
        if not job_id:
            raise ValueError('GUI job was not submitted: ' + self.panel.status.get())
        deadline = time.monotonic() + 120
        while self.panel.job is not None and time.monotonic() < deadline:
            self.root.update()
            threading.Event().wait(0.05)
        result = self.app.controller.job(job_id)
        if self.panel.job is not None or result['status'] != 'completed':
            raise ValueError('GUI author job did not complete; retain history: ' + str(result))
        return result

    def start(self):
        if hashlib.sha256(self.source.read_bytes()).hexdigest() != self.digest:
            raise ValueError('Qualification unit source changed')
        self.history_patch = patch('uconsole_workbench.history_default_path',
                                   return_value=self.output / '.gui-history/jobs.sqlite3')
        self.history_patch.start()
        evidence = {'status': 'failed', 'preloaded_policy': False, 'dialogs': []}
        try:
            self.root = tk.Tk()
            self.app = Workbench(self.root, self.output)
            self.panel = self.app.physical_target()
            self.root.update()

            def confirm(title, message, **kwargs):
                evidence['dialogs'].append({'title': title, 'message': message})
                return True

            with patch('forge_target_gui.simpledialog.askstring', side_effect=[self.host, self.unit]), \
                    patch('forge_target_gui.filedialog.askopenfilename', return_value=str(self.source)), \
                    patch('forge_target_gui.filedialog.askdirectory', return_value=str(self.output)), \
                    patch('forge_target_gui.messagebox.askyesno', side_effect=confirm):
                self.panel.service_prepare_button.invoke()
            evidence['preparation_job'] = self.wait_panel()
            review = self.panel.pending
            if (review is None or review['kind'] != 'service' or review['unit'] != self.unit or
                    review['host'] != self.host or review['before'] != {'kind': 'absent'} or
                    review['after']['sha256'] != self.digest or review['deployment_performed'] or
                    review['authorization_performed'] or 'target-write' in self.app.controller.grants):
                raise ValueError('GUI service preparation differs from inert fixture or grants writes')
            evidence['review'] = dict(review)
            with patch('forge_target_gui.messagebox.askyesno', side_effect=confirm):
                self.panel.approve_button.invoke()
            evidence['approval_job'] = self.wait_panel()
            if self.panel.pending is not None or 'target-write' not in self.app.controller.grants:
                raise ValueError('GUI service registration failed: ' + self.panel.status.get())
            approved = self.app.controller.targets.get(self.panel.selected.get(), 'gui')
            self.prepared = dict(review, authorization_sha256=approved.authorization_sha256)
            self.policy_sha256 = self.app.controller.targets.sha256
            evidence.update(status='prepared-and-approved-not-deployed', policy_sha256=self.policy_sha256,
                            transaction_name=self.panel.selected.get(), authorization_sha256=approved.authorization_sha256)
            return self
        except BaseException as exc:
            evidence['error'] = str(exc)
            self.close()
            raise
        finally:
            with (self.output / 'gui-service-author.json').open('x') as stream:
                json.dump(evidence, stream, indent=2)
