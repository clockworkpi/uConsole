import gc
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipUnless(tk and (os.environ.get('DISPLAY') or sys.platform == 'darwin'), 'needs Tk display')
class TargetPanelTests(unittest.TestCase):
    def setUp(self):
        from forge_target_gui import TargetPanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = patch('workbench_targets.default_path', return_value=Path(self.temp.name) / 'hosts.json')
        config.start()
        self.addCleanup(config.stop)
        self.owner = MagicMock()
        self.owner.call.return_value = {'execution_granted': True, 'transactions': [{'name': 'proof'}]}
        with patch.object(TargetPanel, 'review'):
            self.panel = TargetPanel(self.root, self.owner, 'gui')
        self.summary = patch.object(self.panel, 'plan_summary', return_value={
            'host': 'clockworkpi.local', 'paths': ['/usr/local/bin/proof'], 'plan_sha256': 'a' * 64})
        self.summary.start()
        self.addCleanup(self.summary.stop)
        self.owner.submit_target.return_value = {'job_id': 'job'}

    def tearDown(self):
        if self.panel.timer:
            self.panel.window.after_cancel(self.panel.timer)
        self.root.destroy()
        self.panel = self.root = None
        gc.collect()

    def test_decline_confirmation_never_submits(self):
        with patch('forge_target_gui.messagebox.askyesno', return_value=False) as confirm:
            self.panel.start('apply')
        self.owner.submit_target.assert_not_called()
        self.assertIn('clockworkpi.local', confirm.call_args.args[1])
        self.assertIn('/usr/local/bin/proof', confirm.call_args.args[1])

    def test_recovery_inspection_uses_job_and_never_claims_readiness(self):
        self.assertEqual([b.cget('text') for b in self.panel.buttons[:2]],
                         ['Apply to hardware…', 'Restore hardware…'])
        self.owner.submit_target_recovery.return_value = {'job_id': 'inspect'}
        self.panel.inspect_button.invoke()
        self.owner.submit_target_recovery.assert_called_once_with('gui', 'proof')
        self.assertFalse(self.panel.close())
        self.panel.window.after_cancel(self.panel.timer)
        self.owner.job.return_value = {'status': 'completed', 'result': {'recovery_qualified': False}}
        self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertIn('independent boot recovery remain required', self.panel.status.get())
        self.owner.submit_target.assert_not_called()

    def test_confirmed_job_uses_shared_controller_and_blocks_close(self):
        with patch('forge_target_gui.messagebox.askyesno', return_value=True):
            self.panel.start('restore')
        self.owner.submit_target.assert_called_once_with('gui', 'proof', 'restore')
        self.assertFalse(self.panel.close())
        self.assertTrue(self.panel.window.winfo_exists())
        self.assertTrue(all('disabled' in button.state() for button in self.panel.buttons))

    def test_terminal_failure_does_not_claim_rollback(self):
        self.panel.job = 'job'
        self.owner.job.return_value = {'status': 'failed', 'error': 'SSH timeout'}
        self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertIn('no rollback is implied', self.panel.status.get())

    def test_success_reports_acknowledgement(self):
        self.panel.job = 'job'
        self.owner.job.return_value = {'status': 'completed'}
        self.panel.poll()
        self.assertIn('Target acknowledged', self.panel.status.get())

    def test_service_review_checks_authorization_and_summarizes_scoped_objects(self):
        from forge_target_gui import TargetPanel
        from forge_target_service_dispatch import digest
        binding = {'host': 'clockworkpi.local', 'transaction_sha256': 'a' * 64}
        approved = self.owner.targets.get.return_value
        approved.kind = 'service'
        approved.authorization_sha256 = digest(binding)
        approved.journal = Path('/private/service-transaction')
        pair = {'apply': {'scope': {'services': ['proof.service'],
                                   'files': ['/etc/systemd/system/proof.service'], 'links': []}}}
        with patch('forge_target_service_dispatch.locked') as locked, \
                patch('forge_target_journal.read_record', return_value=binding), \
                patch('forge_target_service_dispatch.transaction', return_value=pair) as transaction:
            locked.return_value.__enter__.return_value = 42
            summary = TargetPanel.plan_summary(self.panel)
            self.assertEqual(summary['services'], ['proof.service'])
            self.assertEqual(summary['host'], 'clockworkpi.local')
            transaction.assert_called_once_with(42, 'a' * 64)
            approved.authorization_sha256 = '0' * 64
            with self.assertRaisesRegex(ValueError, 'differs from owner'):
                TargetPanel.plan_summary(self.panel)

    def test_declined_service_preparation_does_not_submit(self):
        self.panel.host_name.set('target')
        with patch('forge_target_gui.simpledialog.askstring', return_value='proof.service'), \
                patch('forge_target_gui.filedialog.askopenfilename', return_value='/tmp/proof.service'), \
                patch('forge_target_gui.filedialog.askdirectory', return_value='/tmp'), \
                patch('forge_target_gui.messagebox.askyesno', return_value=False):
            self.panel.service_prepare_button.invoke()
        self.owner.submit.assert_not_called()

    def test_declined_service_approval_never_provisions_or_submits(self):
        self.panel.pending = {'kind': 'service', 'journal': '/private/transaction',
                              'transaction_sha256': 'a' * 64, 'unit': 'proof.service', 'host': 'target'}
        with patch('forge_target_service_dispatch.locked'), \
                patch('forge_target_service_dispatch.transaction'), \
                patch('forge_target_gui.messagebox.askyesno', return_value=False), \
                patch('forge_target_service_dispatch.provision_and_authorize') as provision:
            self.panel.approve_button.invoke()  # Initially disabled; explicit method below tests the decision.
            self.panel.approve()
            provision.assert_not_called()
        self.owner.submit.assert_not_called()

    def test_changed_plan_never_prompts_or_submits(self):
        self.panel.plan_summary.side_effect = ValueError('changed plan')
        with patch('forge_target_gui.messagebox.askyesno') as confirm:
            self.panel.start('apply')
        confirm.assert_not_called()
        self.owner.submit_target.assert_not_called()

    def real_owner_and_review(self, root):
        from forge_controller import Controller
        from forge_target_journal import prepare
        owner = Controller({'gui': root})
        self.addCleanup(owner.close)
        self.panel.controller = owner
        before = {'schema': 1, 'machine_id': 'a' * 32,
                  'files': [{'path': '/usr/local/bin/proof', 'kind': 'absent'}]}
        prepared = prepare(root / 'transaction', 'clockworkpi.local', before, before)
        return owner, dict(prepared, host='clockworkpi.local', files=[], deployment_performed=False)

    def test_preparation_never_grants_write_or_dispatches(self):
        with tempfile.TemporaryDirectory() as directory:
            owner, review = self.real_owner_and_review(Path(directory))
            with patch('forge_target_prepare.author', return_value=review), \
                    patch('forge_target_ssh.dispatch') as dispatch:
                self.panel.prepare('clockworkpi.local', [], Path(directory) / 'output')
                owner.jobs[self.panel.job][2].result(timeout=5)
                self.panel.window.after_cancel(self.panel.timer)
                self.panel.poll()
                dispatch.assert_not_called()
            self.assertNotIn('target-write', owner.grants)
            self.assertEqual(self.panel.pending, review)
            self.assertNotIn('disabled', self.panel.approve_button.state())

    def test_owner_approval_enables_named_plan_without_deployment_or_client_escalation(self):
        from forge_client import ClientSession
        with tempfile.TemporaryDirectory() as directory:
            owner, review = self.real_owner_and_review(Path(directory))
            client = ClientSession(owner, ['gui'])
            self.panel.pending = review
            with patch('forge_target_gui.messagebox.askyesno', return_value=True), \
                    patch('forge_target_ssh.dispatch') as dispatch:
                self.panel.approve()
                dispatch.assert_not_called()
            self.assertIn('target-write', owner.grants)
            self.assertIsNone(self.panel.pending)
            name = self.panel.selected.get()
            self.assertEqual(owner.targets.get(name, 'gui').plan_sha256, review['plan_sha256'])
            self.assertFalse(client.call('target_transactions', {'workspace': 'gui'})['execution_granted'])
            with self.assertRaises(PermissionError):
                client.call('target_transition', {'workspace': 'gui', 'transaction': name, 'direction': 'apply'})

    def test_declining_owner_approval_preserves_review_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            owner, review = self.real_owner_and_review(Path(directory))
            self.panel.pending = review
            with patch('forge_target_gui.messagebox.askyesno', return_value=False):
                self.panel.approve()
            self.assertNotIn('target-write', owner.grants)
            self.assertEqual(self.panel.pending, review)
            self.assertEqual(list(Path(directory).glob('approved-policy-*')), [])

    def test_changed_prepared_plan_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            owner, review = self.real_owner_and_review(Path(directory))
            review['plan_sha256'] = '0' * 64
            self.panel.pending = review
            with patch('forge_target_gui.messagebox.askyesno') as confirm:
                self.panel.approve()
                confirm.assert_not_called()
            self.assertNotIn('target-write', owner.grants)
            self.assertIn('changed since review', self.panel.status.get())
