import gc
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

import test_recovery_stage_dispatch as fixtures
from forge_client import ClientSession
from forge_controller import Controller
from forge_recovery_stage_dispatch import PHASES
from forge_target_journal import private_directory, write_record
from uconsole_mcp import BY_NAME, validate


class StagingControlsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecoveryStageDispatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.fixture.root
        self.owner = Controller({'gui': self.root/'workspace', 'other': self.root/'other'})
        self.addCleanup(self.owner.close)

    def approve(self, boot=None):
        definition = dict(schema=1, transactions={phase: dict(kind='recovery-stage', workspace='gui',
            journal=str(self.fixture.directory), authorization_sha256=self.fixture.pin, phase=phase,
            boot_id=boot or self.fixture.boot['boot_id']) for phase in PHASES})
        fd = private_directory(self.root)
        name = 'policy-'+uuid.uuid4().hex+'.json'
        try: pin = write_record(fd, name, definition)
        finally: os.close(fd)
        self.owner.approve_target_policy(self.root/name, pin)

    def complete(self, job):
        self.owner.jobs[job['job_id']][2].result(timeout=5)
        self.assertTrue(self.owner.job_finalized[job['job_id']].wait(5))
        return self.owner.job(job['job_id'])

    def test_listing_is_redacted_and_clients_cannot_inherit_owner_approval(self):
        reader = ClientSession(self.owner, ['gui'])
        self.approve()
        listing = reader.call('target_transactions', dict(workspace='gui'))
        self.assertFalse(listing['execution_granted'])
        self.assertEqual(len(listing['transactions']), 4)
        self.assertEqual({item['kind'] for item in listing['transactions']}, {'recovery-stage'})
        self.assertNotIn(str(self.root), json.dumps(listing))
        self.assertNotIn(self.fixture.reviewed['request']['lease_owner'], json.dumps(listing))
        for tool in ('target_transition', 'target_staging_reconcile'):
            with self.assertRaises(PermissionError):
                reader.call(tool, dict(workspace='gui', transaction='firmware-start', direction='apply'))
        self.assertEqual(self.fixture.writes, [])

    def test_client_executes_real_dispatch_and_reconciles_lost_reply_without_retry(self):
        self.approve()
        client = ClientSession(self.owner, ['gui'], ['target-write'])
        args = dict(workspace='gui', transaction='firmware-start', direction='apply')
        self.fixture.lose_reply = True
        submitted = client.call('target_transition', args)
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.complete(submitted)
        self.assertTrue(self.owner.job_finalized[submitted['job_id']].wait(5))
        self.fixture.lose_reply = False
        reconciled = self.complete(client.call('target_staging_reconcile', args))
        self.assertEqual(reconciled['result']['outcome']['status'], 'completed')
        self.assertEqual(len(self.fixture.writes), 1)
        self.assertNotIn(str(self.root), json.dumps(reconciled))
        result = self.complete(client.call('target_transition', dict(args, transaction='firmware-fixup')))
        self.assertEqual(result['context']['kind'], 'recovery-stage')
        self.assertEqual(result['context']['phase'], 'firmware-fixup')

    def test_restore_after_reboot_requires_new_owner_boot_approval(self):
        self.approve()
        self.complete(self.owner.submit_target('gui', 'firmware-start', 'apply'))
        self.fixture.boot['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        job = self.owner.submit_target('gui', 'firmware-start', 'restore')
        with self.assertRaises(PermissionError): self.complete(job)
        self.assertTrue(self.owner.job_finalized[job['job_id']].wait(5))
        self.assertFalse((self.fixture.directory/'stage-attempt-firmware-start-restore.json').exists())
        self.approve()
        restored = self.complete(self.owner.submit_target('gui', 'firmware-start', 'restore'))
        self.assertEqual(restored['result']['status'], 'acknowledged')

    def test_schema_forbids_boot_phase_host_and_authority_overrides(self):
        args = dict(workspace='gui', transaction='firmware-start', direction='apply')
        schema = BY_NAME['target_staging_reconcile']['inputSchema']
        validate(schema, args)
        for field in ('host', 'phase', 'boot_id', 'journal', 'authorization_sha256', 'command'):
            with self.assertRaises(ValueError): validate(schema, dict(args, **{field: 'override'}))
        self.approve()
        with self.assertRaises(ValueError): self.owner.submit_target_staging_reconcile('other', 'firmware-start', 'apply')
        with self.assertRaises(ValueError): self.owner.submit_target_staging_reconcile('gui', 'firmware-start', 'retry')

    def test_staging_policy_requires_explicit_boot_and_fixed_phase(self):
        from forge_targets import TargetTransactions
        self.approve()
        definition = self.owner.targets.get('firmware-start', 'gui').definition()
        self.assertEqual(definition['phase'], 'firmware-start')
        for changes in (dict(boot_id='current'), dict(phase='root'), dict(boot_id=None)):
            fd = private_directory(self.root)
            name = uuid.uuid4().hex+'.json'
            try: pin = write_record(fd, name, dict(schema=1, transactions=dict(bad=dict(definition, **changes))))
            finally: os.close(fd)
            with self.assertRaises(ValueError): TargetTransactions(self.root/name, pin, self.owner.workspaces)

    def test_reconciliation_is_non_cancellable_and_excludes_other_target_jobs(self):
        self.approve()
        entered, release = threading.Event(), threading.Event()
        def held(*args):
            entered.set()
            if not release.wait(5): raise TimeoutError('fixture not released')
            return dict(status='inspected')
        with patch('forge_recovery_stage_dispatch.reconcile', side_effect=held):
            job = self.owner.submit_target_staging_reconcile('gui', 'firmware-start', 'apply')
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.owner.cancel(job['job_id'])['requested'])
                with self.assertRaisesRegex(ValueError, 'physical target'):
                    self.owner.submit('other', 'competing', lambda: None,
                                      target_identity=self.fixture.boot['machine_id'])
            finally: release.set()
            self.complete(job)


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'darwin', 'needs native Tk or Xvfb')
class StagingGuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from forge_target_gui import TargetPanel
        self.control = StagingControlsTests()
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.owner, self.fixture = self.control.owner, self.control.fixture
        self.window = tk.Tk()
        self.window.withdraw()
        self.panel = TargetPanel(self.window, self.owner, 'gui')

    def tearDown(self):
        if self.panel.timer: self.panel.window.after_cancel(self.panel.timer)
        self.window.destroy()
        self.window = self.panel = None
        gc.collect()

    def review(self):
        with patch('forge_recovery_stage_gui.filedialog.askdirectory', return_value=str(self.fixture.directory)), \
                patch('forge_recovery_stage_gui.simpledialog.askstring', side_effect=[self.fixture.pin, self.fixture.boot['boot_id']]):
            self.panel.review_staging()

    def approve(self):
        self.review()
        with patch('forge_recovery_stage_gui.messagebox.askyesno', return_value=True): self.panel.approve()

    def test_review_and_approval_have_no_target_effect_or_client_escalation(self):
        reader = ClientSession(self.owner, ['gui'])
        self.review()
        self.assertNotIn('target-write', self.owner.grants)
        self.assertNotIn(self.fixture.reviewed['request']['lease_owner'], self.panel.details.get('1.0', 'end'))
        with patch('forge_recovery_stage_gui.messagebox.askyesno', return_value=False): self.panel.approve()
        self.assertNotIn('target-write', self.owner.grants)
        with patch('forge_recovery_stage_gui.messagebox.askyesno', return_value=True): self.panel.approve()
        self.assertEqual(len(self.owner.targets.transactions), 4)
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.fixture.requests, [])
        self.assertEqual(self.owner.jobs, {})
        self.assertFalse(reader.call('target_transactions', dict(workspace='gui'))['execution_granted'])

    def test_changed_sealed_draft_during_confirmation_never_approves(self):
        self.review()
        def change(*args, **kwargs):
            (self.fixture.directory/'before.json').write_text('{}')
            return True
        with patch('forge_recovery_stage_gui.messagebox.askyesno', side_effect=change): self.panel.approve()
        self.assertNotIn('target-write', self.owner.grants)
        self.assertEqual(self.owner.jobs, {})

    def test_gui_apply_failure_then_explicit_reconciliation_uses_shared_jobs(self):
        self.approve()
        self.fixture.lose_reply = True
        with patch('forge_target_gui.messagebox.askyesno', return_value=True): self.panel.start('apply')
        job = self.panel.job
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.control.complete(dict(job_id=job))
        self.assertTrue(self.owner.job_finalized[job].wait(5))
        self.panel.window.after_cancel(self.panel.timer)
        self.panel.poll()
        self.fixture.lose_reply = False
        with patch('forge_recovery_stage_gui.messagebox.askyesno', return_value=True): self.panel.reconcile_staging('apply')
        self.assertFalse(self.panel.close())
        self.control.complete(dict(job_id=self.panel.job))
        self.panel.window.after_cancel(self.panel.timer)
        self.panel.poll()
        self.assertIn('completed', self.panel.status.get())
        self.assertIn('requires_new_boot', self.panel.details.get('1.0', 'end'))
        self.assertEqual(len(self.fixture.writes), 1)

    def test_changed_selection_during_execution_confirmation_is_refused(self):
        self.approve()
        def change(*args, **kwargs):
            self.panel.selected.set(list(self.owner.targets.transactions)[1])
            return True
        with patch('forge_target_gui.messagebox.askyesno', side_effect=change): self.panel.start('apply')
        self.assertEqual(self.owner.jobs, {})
        self.assertIn('changed during confirmation', self.panel.status.get())

    def test_failed_status_query_retains_job_and_rechecks_without_resubmission(self):
        self.panel.job = 'retained-job'
        with patch.object(self.owner, 'job', side_effect=OSError('temporary observation failure')):
            self.panel.poll()
        self.assertEqual(self.panel.job, 'retained-job')
        self.assertIn('do not resubmit', self.panel.status.get())
        self.assertFalse(self.panel.close())
        with patch.object(self.owner, 'job', return_value=dict(status='failed', error='retained failure')):
            self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertEqual(self.owner.jobs, {})
