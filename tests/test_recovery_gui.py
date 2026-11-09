import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import tkinter as tk
from forge_client import ClientSession
from forge_controller import Controller
from forge_ram_transport import RecoveryProbe
from forge_recovery_gui import RecoveryPanel, policy_pin
from forge_recovery_session import prepare


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'darwin', 'needs native Tk or Xvfb')
class RecoveryPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        for name in ('key', 'known_hosts'):
            (self.path/name).write_text('private credential canary ' + name)
            (self.path/name).chmod(0o600)
        probe = RecoveryProbe('fixture', self.path/'key', self.path/'known_hosts',
                              'a'*32, '6.12.62-v8+', '100000007b961d25')
        session = self.path/'session'
        record = prepare(session, probe, '11111111-2222-3333-4444-555555555555', 'b'*64)
        self.definition = dict(schema=1, jobs=dict(proof=dict(workspace='gui', machine_id='c'*32,
            session=str(session), session_sha256=record['binding_sha256'], key=str(probe.key),
            known_hosts=str(probe.known_hosts), operation='reconcile-root',
            arguments=dict(journal=str(self.path/'journal'), plan_sha256='d'*64))))
        self.policy = self.path/'policy.json'
        self.policy.write_text(json.dumps(self.definition))
        self.owner = Controller({'gui': self.path/'workspace'})
        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = RecoveryPanel(self.root, self.owner, 'gui')

    def tearDown(self):
        self.owner.close()
        self.root.destroy()
        self.panel = self.root = self.owner = None
        gc.collect()
        self.temp.cleanup()

    def approve(self):
        self.panel.load_policy(self.policy)
        with patch('forge_recovery_gui.messagebox.askyesno', return_value=True):
            self.panel.approve()
        self.assertIsNone(self.panel.pending)

    def test_unconfigured_panel_does_not_enable_actions(self):
        self.assertIn('disabled', self.panel.run_button.state())
        self.assertIn('disabled', self.panel.approve_button.state())
        self.assertIn('disabled', self.panel.backup_button.state())
        self.assertIn('--recovery-policy', self.panel.details.get('1.0', 'end'))
        self.assertEqual(self.owner.jobs, {})

    def test_offline_source_job_is_owner_only_and_retained_on_status_error(self):
        entered, finish = threading.Event(), threading.Event()
        reviewed = dict(acceptance_sha256='a'*64)
        def prepare(output, frozen):
            entered.set()
            if not finish.wait(5): raise TimeoutError('fixture blocked')
            return dict(status='prepared-backup-root-source', target_contacted=False)
        with patch('forge_backup_source.prepare', side_effect=prepare) as worker:
            self.panel.prepare_source(reviewed, self.path/'source-output')
            job = self.panel.job
            try:
                self.assertTrue(entered.wait(2))
                self.assertIn('disabled', self.panel.source_button.state())
                self.assertFalse(self.panel.close())
                with self.assertRaises(ValueError):
                    self.panel.prepare_source(reviewed, self.path/'other-output')
                with patch.object(self.owner, 'job', side_effect=ConnectionError('status unavailable')):
                    self.panel.poll()
                self.assertEqual(self.panel.job, job)
            finally:
                finish.set()
            self.owner.jobs[job][2].result(timeout=5)
            worker.assert_called_once()
        self.panel.poll()
        self.assertIn('not filesystem health', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError):
            self.owner.call('recovery_prepare_backup_source', {'workspace': 'gui'})

    def test_declined_offline_source_review_does_not_submit(self):
        reviewed = dict(card=dict(bytes=512, sha256='a'*64), plan_sha256='b'*64)
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(self.path)), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value='c'*64), \
                patch('forge_backup_source.inputs', return_value=reviewed), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'prepare_backup_source') as submit:
            self.panel.source_dialog()
            submit.assert_not_called()

    def test_backup_health_errors_are_not_displayed_as_healthy(self):
        reviewed = dict(acceptance_sha256='a'*64)
        for qualified in (False, True):
            with self.subTest(qualified=qualified), patch('forge_backup_source.check_filesystems', return_value=dict(
                    status='checked-backup-filesystems', filesystem_consistency_qualified=qualified)) as worker:
                self.panel.prepare_source(reviewed, self.path/'health-output', health=True)
                self.owner.jobs[self.panel.job][2].result(timeout=5)
                self.panel.poll()
                worker.assert_called_once()
                self.assertIn('checks passed' if qualified else 'NOT qualified', self.panel.status.get())
                self.assertIn('No repair', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError):
            self.owner.call('recovery_check_backup_filesystems', {'workspace': 'gui'})

    def test_export_lineage_is_owner_only_and_does_not_grant_deployment(self):
        reviewed = dict(source_sha256='a'*64)
        with patch('forge_derivative_prepare.prepare', return_value=dict(
                status='prepared-export-derivative', target_contacted=False)) as worker:
            self.panel.prepare_derivative(reviewed, self.path/'derivative')
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            worker.assert_called_once()
        self.assertIn('physical boot remain unqualified', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError):
            self.owner.call('recovery_prepare_export_derivative', {'workspace': 'gui'})

    def test_declined_export_review_does_not_submit(self):
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(self.path)), \
                patch('forge_recovery_gui.filedialog.askopenfilename', return_value=str(self.path/'image.img')), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value='c'*64), \
                patch('forge_derivative_prepare.inputs', return_value=dict(source_sha256='c'*64)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'prepare_export_derivative') as submit:
            self.panel.derivative_dialog()
            submit.assert_not_called()

    def test_export_health_uses_distinct_root_health_flag(self):
        for qualified in (False, True):
            with self.subTest(qualified=qualified), patch('forge_recovery_derivative.load'), \
                    patch('forge_recovery_derivative_health.inspect_root', return_value=dict(
                        status='checked-derivative-root', root_filesystem_consistency_qualified=qualified)) as worker:
                self.panel.check_export_health(self.path/'derivative', 'a'*64, self.path/'health')
                self.owner.jobs[self.panel.job][2].result(timeout=5)
                self.panel.poll()
                worker.assert_called_once()
                self.assertIn('check passed' if qualified else 'NOT qualified', self.panel.status.get())
                self.assertIn('No boot-filesystem check', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())

    def test_declined_export_health_does_not_submit(self):
        manifest = dict(root=dict(bytes=4096, sha256='b'*64))
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(self.path)), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value='c'*64), \
                patch('forge_recovery_derivative.load', return_value=(manifest, {})), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'check_export_root') as submit:
            self.panel.export_health_dialog()
            submit.assert_not_called()

    def discover_builder(self):
        self.discovery = dict(host='fixture', kernel='6.12.62-v8+', boot=dict(
            machine_id='c'*32, boot_id='11111111-2222-3333-4444-555555555555',
            cmdline='root=PARTUUID=21965b0c-02 rw', tryboot=0, partition=1))
        with patch('forge_recovery_build.discover', return_value=self.discovery):
            self.panel.discover_builder('fixture')
            job = self.panel.job
            result = self.owner.jobs[job][2].result(timeout=5)
            self.assertNotIn('host', result)
        self.panel.poll()
        self.assertEqual(self.panel.build_candidate, self.discovery)

    def test_builder_discovery_and_build_are_separate_owner_jobs(self):
        self.assertIn('disabled', self.panel.build_button.state())
        self.discover_builder()
        self.assertNotIn('disabled', self.panel.build_button.state())
        entered, finish = threading.Event(), threading.Event()
        def build(output, discovered, credentials):
            entered.set()
            if not finish.wait(5): raise TimeoutError('fixture blocked')
            return dict(status='built-not-published', publication_performed=False)
        with patch('forge_recovery_build.build', side_effect=build) as worker:
            self.panel.build_image(self.discovery, self.path/'credentials', self.path/'output')
            job = self.panel.job
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.panel.close())
                self.assertIsNone(self.panel.build_candidate)
                self.assertIn('disabled', self.panel.discover_build_button.state())
                with self.assertRaises(ValueError):
                    self.panel.build_image(self.discovery, self.path/'credentials', self.path/'another')
                with patch.object(self.owner, 'job', side_effect=ConnectionError('status lost')):
                    self.panel.poll()
                self.assertEqual(self.panel.job, job)
            finally:
                finish.set()
            self.owner.jobs[job][2].result(timeout=5)
            worker.assert_called_once()
        self.panel.poll()
        self.assertIn('Not published or boot-qualified', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('disabled', self.panel.build_button.state())

    def test_failed_native_build_does_not_enable_replay(self):
        self.discover_builder()
        with patch('forge_recovery_build.build', side_effect=RuntimeError('native failure')) as worker:
            self.panel.build_image(self.discovery, self.path/'credentials', self.path/'output')
            with self.assertRaises(RuntimeError): self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            self.panel.build_dialog()
            worker.assert_called_once()
        self.assertIsNone(self.panel.build_candidate)
        self.assertIn('failed', self.panel.status.get())

    def test_declined_builder_discovery_and_build_do_not_submit(self):
        with patch('forge_recovery_gui.simpledialog.askstring', return_value='fixture'), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'discover_recovery_builder') as submit:
            self.panel.discover_build_dialog()
            submit.assert_not_called()
        self.discover_builder()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(self.path)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'build_recovery_image') as submit:
            self.panel.build_dialog()
            submit.assert_not_called()

    def test_changed_discovery_during_build_confirmation_refused(self):
        self.discover_builder()
        def change(*args, **kwargs):
            self.panel.build_candidate = None
            return True
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(self.path)), \
                patch('forge_recovery_gui.messagebox.askyesno', side_effect=change), \
                patch.object(self.owner, 'build_recovery_image') as submit:
            self.panel.build_dialog()
            submit.assert_not_called()
        self.assertIn('changed during confirmation', self.panel.status.get())

    def test_build_controls_blocked_by_pending_policy_and_absent_from_clients(self):
        self.panel.load_policy(self.policy)
        with patch.object(self.owner, 'discover_recovery_builder') as submit:
            self.panel.discover_build_dialog()
            with self.assertRaises(ValueError): self.panel.discover_builder('fixture')
            submit.assert_not_called()
        client = ClientSession(self.owner, ['gui'])
        for name in ('discover_recovery_builder', 'build_recovery_image', 'prepare_recovery_publication', 'publish_recovery_image',
                     'prepare_boot_privacy', 'apply_boot_privacy', 'verify_boot_privacy', 'boot_recovery_session'):
            with self.assertRaises((ValueError, PermissionError)):
                client.call(name, {'workspace': 'gui'})

    def test_publication_preparation_is_separate_owner_draft_job(self):
        import test_recovery_publication_prepare
        fixture = test_recovery_publication_prepare.PublicationPreparationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch('forge_recovery_publication_prepare.prepare', return_value=dict(status='prepared-not-published')) as prepare:
            self.panel.prepare_publication(fixture.build, fixture.pin, fixture.output)
            job = self.panel.job
            self.assertFalse(self.panel.close())
            self.owner.jobs[job][2].result(timeout=5)
            self.panel.poll()
            prepare.assert_called_once_with(fixture.output, fixture.frozen)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIsNone(self.panel.pending)
        self.assertIn('Not published or approved', self.panel.status.get())

    def test_declined_publication_preparation_does_not_submit(self):
        import test_recovery_publication_prepare
        fixture = test_recovery_publication_prepare.PublicationPreparationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(fixture.build), str(fixture.output.parent)]), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value=fixture.pin), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'prepare_recovery_publication') as submit:
            self.panel.publication_dialog()
            submit.assert_not_called()
        self.assertEqual(self.owner.jobs, {})

    def publication_fixture(self):
        import test_recovery_publication_dispatch
        fixture = test_recovery_publication_dispatch.PublicationDispatchTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_publication_is_explicit_owner_job_without_agent_grants(self):
        fixture = self.publication_fixture()
        with patch('forge_recovery_publication_dispatch.publish', return_value=dict(status='published-not-boot-qualified')) as publish:
            self.panel.publish_image(fixture.frozen)
            job = self.panel.job
            self.assertFalse(self.panel.close())
            self.assertIn('disabled', self.panel.publish_button.state())
            self.owner.jobs[job][2].result(timeout=5)
            self.panel.poll()
            publish.assert_called_once_with(fixture.frozen)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('No reboot or root-write authority', self.panel.status.get())

    def test_declined_publication_never_submits(self):
        fixture = self.publication_fixture()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.directory)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'publish_recovery_image') as submit:
            self.panel.publish_dialog()
            submit.assert_not_called()
        self.assertEqual(self.owner.jobs, {})

    def test_changed_publication_during_confirmation_refused(self):
        fixture = self.publication_fixture()
        def change(*args, **kwargs):
            (fixture.directory/'publish-attempt').mkdir(mode=0o700)
            return True
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.directory)), \
                patch('forge_recovery_gui.messagebox.askyesno', side_effect=change), \
                patch.object(self.owner, 'publish_recovery_image') as submit:
            self.panel.publish_dialog()
            submit.assert_not_called()
        self.assertIn('not submitted', self.panel.status.get())

    def test_privacy_preparation_and_apply_are_owner_jobs_without_reboot_grant(self):
        import test_boot_privacy_setup
        fixture = test_boot_privacy_setup.PrivacySetupTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        accepted = fixture.prepare()
        with patch('forge_boot_privacy_setup.prepare', return_value=accepted) as prepare:
            self.panel.prepare_privacy('fixture', fixture.output)
            self.assertFalse(self.panel.close())
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            prepare.assert_called_once_with(fixture.output, 'fixture')
        with patch('forge_boot_privacy_setup.apply', return_value=dict(status='applied-awaiting-private-mount')) as apply:
            self.panel.apply_privacy(fixture.frozen)
            self.assertFalse(self.panel.close())
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            apply.assert_called_once_with(fixture.frozen)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('not proof of effective private permissions', self.panel.status.get())

    def test_declined_privacy_preparation_does_not_submit(self):
        with patch('forge_recovery_gui.simpledialog.askstring', return_value='fixture'), \
                patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(self.path)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'prepare_boot_privacy') as submit:
            self.panel.privacy_prepare_dialog()
            submit.assert_not_called()

    def test_declined_privacy_application_displays_plain_text_and_does_not_submit(self):
        import test_boot_privacy_setup
        fixture = test_boot_privacy_setup.PrivacySetupTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.output)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'apply_boot_privacy') as submit:
            self.panel.privacy_apply_dialog()
            submit.assert_not_called()
        displayed = self.panel.details.get('1.0', 'end')
        self.assertIn('fmask=0077', displayed)
        self.assertIn('PARTUUID=21965b0c-01', displayed)

    def privacy_verification_fixture(self):
        import test_boot_privacy_verify
        fixture = test_boot_privacy_verify.PrivacyVerificationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_private_mount_verification_keeps_reboot_explicit_and_owner_only(self):
        fixture = self.privacy_verification_fixture()
        for reboot in (False, True):
            with patch('forge_boot_privacy_verify.verify', return_value=dict(status='verified-private-normal-boot')) as verify:
                self.panel.verify_privacy(fixture.frozen, reboot=reboot)
                self.assertFalse(self.panel.close())
                self.owner.jobs[self.panel.job][2].result(timeout=5)
                self.panel.poll()
                verify.assert_called_once_with(fixture.frozen, reboot=reboot)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('never resubmit the reboot', self.panel.status.get())

    def test_declined_private_mount_reboot_does_not_submit(self):
        fixture = self.privacy_verification_fixture()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.directory)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'verify_boot_privacy') as submit:
            self.panel.privacy_verify_dialog(reboot=True)
            submit.assert_not_called()

    def test_changed_privacy_history_during_reboot_confirmation_rejected(self):
        fixture = self.privacy_verification_fixture()
        def change(*args, **kwargs):
            (fixture.directory/'apply-attempt/failure.json').write_text('{}')
            return True
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.directory)), \
                patch('forge_recovery_gui.messagebox.askyesno', side_effect=change), \
                patch.object(self.owner, 'verify_boot_privacy') as submit:
            self.panel.privacy_verify_dialog(reboot=True)
            submit.assert_not_called()
        self.assertIn('not submitted', self.panel.status.get())

    def tryboot_fixture(self):
        import test_recovery_tryboot
        fixture = test_recovery_tryboot.RecoveryTrybootTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.stage()
        return fixture

    def test_tryboot_enrollment_enables_backup_without_lease_or_grants(self):
        fixture = self.tryboot_fixture()
        enrolled = dict(status='enrolled-not-leased', boot_id=fixture.boot_id)
        with patch('forge_recovery_tryboot.boot_and_enroll', return_value=dict(
                status='recovery-boot-enrolled-not-leased', enrollment=enrolled)) as boot:
            self.panel.boot_recovery(fixture.value, fixture.output)
            self.assertFalse(self.panel.close())
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            boot.assert_called_once_with(fixture.output, fixture.value)
        self.assertEqual(self.panel.enrollment_ready[0][0], fixture.output/'enrollment')
        self.assertEqual(self.panel.enrollment_ready[1], enrolled)
        self.assertNotIn('disabled', self.panel.backup_button.state())
        self.assertEqual(self.owner.grants, frozenset())

    def held_fixture(self):
        import test_held_reboot
        fixture = test_held_reboot.HeldRebootTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_held_reboot_enrolls_without_grants_or_new_lease(self):
        fixture = self.held_fixture()
        enrolled = dict(status='enrolled-not-leased', boot_id=fixture.new_boot)
        with patch('forge_held_reboot.boot_and_enroll', return_value=dict(
                status='held-boot-enrolled-not-leased', enrollment=enrolled)) as boot:
            self.panel.reboot_held(fixture.source, fixture.value, fixture.directory, fixture.pin, fixture.output)
            self.assertFalse(self.panel.close())
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            boot.assert_called_once_with(fixture.output, fixture.source, fixture.value, fixture.directory, fixture.pin)
        self.assertEqual(self.panel.enrollment_ready[0][0], fixture.output/'enrollment')
        self.assertEqual(self.panel.enrollment_ready[1], enrolled)
        self.assertNotIn('disabled', self.panel.hash_button.state())
        self.assertIn('independent hold reconciliation', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError): self.owner.call('reboot_held_recovery', {'workspace': 'gui'})

    def test_declined_held_reboot_never_contacts_target(self):
        fixture = self.held_fixture()
        self.panel.enrollment_ready = ((fixture.source.directory, fixture.source.probe.key,
                                       fixture.source.probe.known_hosts), fixture.f.enrollment.accepted)
        self.panel.refresh()
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(fixture.value.staging),
                   str(fixture.directory), str(fixture.f.root)]), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value=fixture.pin), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote, \
                patch.object(self.owner, 'reboot_held_recovery') as submit:
            self.panel.held_reboot_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()

    def test_declined_tryboot_does_not_submit(self):
        fixture = self.tryboot_fixture()
        value = fixture.value
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(value.staging), str(fixture.root)]), \
                patch('forge_recovery_gui.filedialog.askopenfilename', side_effect=[str(value.probe.key), str(value.probe.known_hosts)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=[value.staging_pin, value.probe.host,
                    value.probe.kernel, value.probe.serial]), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'boot_recovery_session') as submit:
            self.panel.enroll_dialog(tryboot=True)
            submit.assert_not_called()

    def test_review_is_local_and_never_displays_credential_contents(self):
        with patch.object(RecoveryProbe, '_observe', side_effect=AssertionError('unexpected SSH')):
            self.panel.load_policy(self.policy)
        self.assertNotIn('target-recovery', self.owner.grants)
        text = self.panel.details.get('1.0', 'end')
        self.assertIn('fixture', text)
        self.assertIn('d'*64, text)
        self.assertNotIn('private credential canary', text)
        self.assertIn('disabled', self.panel.run_button.state())
        self.assertNotIn('disabled', self.panel.approve_button.state())

    def test_approval_does_not_execute_or_expand_existing_client_grants(self):
        client = ClientSession(self.owner, ['gui'])
        self.approve()
        self.assertIn('target-recovery', self.owner.grants)
        self.assertFalse(client.call('recovery_jobs', {'workspace': 'gui'})['execution_granted'])
        self.assertEqual(self.owner.jobs, {})
        self.assertNotIn('disabled', self.panel.run_button.state())

    def test_changed_policy_bytes_refused_at_approval(self):
        self.panel.load_policy(self.policy)
        self.policy.write_text('{}')
        with patch('forge_recovery_gui.messagebox.askyesno', return_value=True):
            self.panel.approve()
        self.assertIn('Approval failed', self.panel.status.get())
        self.assertNotIn('target-recovery', self.owner.grants)
        self.assertIsNotNone(self.panel.pending)
        self.assertEqual(self.owner.jobs, {})

    def test_cancelled_approval_and_execution_have_no_effect(self):
        self.panel.load_policy(self.policy)
        with patch('forge_recovery_gui.messagebox.askyesno', return_value=False):
            self.panel.approve()
        self.assertNotIn('target-recovery', self.owner.grants)
        self.approve()
        with patch('forge_recovery_gui.messagebox.askyesno', return_value=False):
            self.panel.start()
        self.assertIsNone(self.panel.job)
        self.assertEqual(self.owner.jobs, {})

    def test_changed_policy_during_modal_confirmation_refused(self):
        self.approve()
        def change(*args, **kwargs):
            self.owner.approve_recovery_policy(self.policy, policy_pin(self.policy))
            return True
        with patch('forge_recovery_gui.messagebox.askyesno', side_effect=change):
            self.panel.start()
        self.assertIsNone(self.panel.job)
        self.assertEqual(self.owner.jobs, {})
        self.assertIn('changed during confirmation', self.panel.status.get())

    def test_active_job_blocks_close_reload_resubmit_and_survives_status_error(self):
        self.approve()
        entered, finish = threading.Event(), threading.Event()
        def execute(job):
            entered.set()
            if not finish.wait(5): raise TimeoutError('fixture not released')
            return dict(root_written=False, normal_boot_release_authorized=False)
        with patch('forge_recovery_jobs.execute', side_effect=execute) as worker, \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=True):
            self.panel.start()
            job = self.panel.job
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.panel.close())
                with self.assertRaises(ValueError): self.panel.load_policy(self.policy)
                self.panel.start()
                worker.assert_called_once()
                with patch.object(self.owner, 'job', side_effect=ConnectionError('status lost')):
                    self.panel.poll()
                self.assertEqual(self.panel.job, job)
                self.assertIn('Do not resubmit', self.panel.status.get())
                self.assertNotIn('disabled', self.panel.recheck_button.state())
                self.assertIn('disabled', self.panel.run_button.state())
            finally:
                finish.set()
                self.owner.jobs[job][2].result(timeout=5)
        self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertIn('completed', self.panel.status.get())
        self.assertIn('No rollback', self.panel.status.get())
        self.assertIn('false', self.panel.details.get('1.0', 'end'))

    def test_failed_worker_retains_evidence_without_retry(self):
        self.approve()
        with patch('forge_recovery_jobs.execute', side_effect=RuntimeError('uncertain write')) as worker, \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=True):
            self.panel.start()
            with self.assertRaises(RuntimeError):
                self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            worker.assert_called_once()
        self.assertIn('uncertain write', self.panel.details.get('1.0', 'end'))
        self.assertIn('reconcile uncertain writes', self.panel.status.get())

    def test_nonregular_or_oversized_policy_refused(self):
        fifo = self.path/'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError): policy_pin(fifo)
        self.policy.write_bytes(b' '*(1024*1024+1))
        with self.assertRaises(ValueError): policy_pin(self.policy)

    def test_normal_ssh_preparation_uses_shared_job_without_approving_staging(self):
        import test_recovery_stage_prepare
        fixture = test_recovery_stage_prepare.RecoveryStagePreparationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.panel.prepare_staging(fixture.publication, fixture.frozen['publication_sha256'],
                                   fixture.root/'firmware.json', fixture.frozen['firmware_sha256'], fixture.output)
        job = self.panel.job
        self.assertFalse(self.panel.close())
        self.owner.jobs[job][2].result(timeout=5)
        self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertIsNone(self.panel.pending)
        self.assertIsNone(self.owner.targets)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('not approved, staged, or boot-qualified', self.panel.status.get())
        self.assertIn('prepared-not-approved', self.panel.details.get('1.0', 'end'))

    def test_fresh_session_enrollment_is_owner_job_and_never_approves_policy(self):
        import test_session_enrollment
        fixture = test_session_enrollment.SessionEnrollmentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.observe():
            self.panel.enroll_session(fixture.value, fixture.output)
            job = self.panel.job
            self.assertFalse(self.panel.close())
            self.assertIn('disabled', self.panel.enroll_button.state())
            self.owner.jobs[job][2].result(timeout=5)
        self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertIsNone(self.panel.pending)
        self.assertIsNone(self.owner.recovery_jobs)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('enrolled-not-leased', self.panel.details.get('1.0', 'end'))
        self.assertNotIn(fixture.value.owner, self.panel.details.get('1.0', 'end'))
        self.assertIn('separately reviewed job policy', self.panel.status.get())

    def test_pending_policy_blocks_session_enrollment(self):
        self.panel.load_policy(self.policy)
        self.assertIn('disabled', self.panel.enroll_button.state())
        with patch.object(self.owner, 'enroll_recovery_session') as submit:
            with self.assertRaises(ValueError): self.panel.enroll_session(None, self.path/'new')
            self.panel.enroll_dialog()
        submit.assert_not_called()

    def test_declined_enrollment_dialog_never_contacts_or_submits(self):
        import test_session_enrollment
        fixture = test_session_enrollment.SessionEnrollmentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        value = fixture.value
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(fixture.stage), str(fixture.root)]), \
                patch('forge_recovery_gui.filedialog.askopenfilename', side_effect=[str(value.probe.key), str(value.probe.known_hosts)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=[fixture.pin, value.probe.host,
                    value.probe.kernel, value.probe.serial]), \
                patch('forge_recovery_gui.simpledialog.askinteger', return_value=1), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote, \
                patch.object(self.owner, 'enroll_recovery_session') as submit:
            self.panel.enroll_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(self.owner.jobs, {})

    def test_backup_draft_enters_review_without_approval_or_backup(self):
        import test_backup_policy
        fixture = test_backup_policy.BackupPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.observe():
            self.panel.prepare_backup(fixture.source, fixture.output)
            job = self.panel.job
            self.assertFalse(self.panel.close())
            self.owner.jobs[job][2].result(timeout=5)
        self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertIsNotNone(self.panel.pending)
        self.assertIsNone(self.owner.recovery_jobs)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('backup-card', self.panel.details.get('1.0', 'end'))
        self.assertIn('No backup exists yet', self.panel.status.get())
        self.assertFalse((fixture.output/'card-backup').exists())
        client = ClientSession(self.owner, ['gui'])
        with self.assertRaises((ValueError, PermissionError)):
            client.call('prepare_recovery_backup', {'workspace': 'gui'})

    def test_hash_draft_enters_review_without_capture_or_approval(self):
        import test_backup_policy
        fixture = test_backup_policy.BackupPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.observe():
            self.panel.prepare_backup(fixture.source, fixture.output, hash_only=True)
            self.owner.jobs[self.panel.job][2].result(timeout=5)
        self.panel.poll()
        self.assertIsNotNone(self.panel.pending)
        self.assertIsNone(self.owner.recovery_jobs)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('hash-card', self.panel.details.get('1.0', 'end'))
        self.assertNotIn('backup-card', self.panel.details.get('1.0', 'end'))
        self.assertIn('No full-card hashes captured yet', self.panel.status.get())
        self.assertFalse((fixture.output/'card-hashes').exists())
        with self.assertRaises(ValueError): self.owner.call('prepare_recovery_hash', {'workspace': 'gui'})

    def test_declined_hash_draft_has_no_target_contact(self):
        import test_backup_policy
        fixture = test_backup_policy.BackupPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.panel.enrollment_ready = ((fixture.source.directory, fixture.source.probe.key,
                                       fixture.source.probe.known_hosts), fixture.accepted)
        self.panel.refresh()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.root)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote, \
                patch.object(self.owner, 'prepare_recovery_hash') as submit:
            self.panel.hash_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()

    def test_hold_draft_enters_review_without_installation_or_approval(self):
        import test_hold_policy
        fixture = test_hold_policy.HoldPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.object(RecoveryProbe, '_observe') as remote:
            self.panel.prepare_hold(fixture.source, fixture.reviewed(), fixture.output)
            self.owner.jobs[self.panel.job][2].result(timeout=5)
        remote.assert_not_called()
        self.panel.poll()
        self.assertIsNotNone(self.panel.pending)
        self.assertIsNone(self.owner.recovery_jobs)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('install-hold', self.panel.details.get('1.0', 'end'))
        self.assertNotIn('release-hold', self.panel.details.get('1.0', 'end'))
        self.assertIn('No hold installed', self.panel.status.get())
        self.assertFalse((fixture.output/'hold/commit-attempt').exists())
        with self.assertRaises(ValueError): self.owner.call('prepare_recovery_hold', {'workspace': 'gui'})

    def test_declined_hold_draft_does_not_submit_or_contact_target(self):
        import test_hold_policy
        fixture = test_hold_policy.HoldPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.panel.enrollment_ready = ((fixture.source.directory, fixture.source.probe.key,
                                       fixture.source.probe.known_hosts), fixture.enrollment.accepted)
        self.panel.refresh()
        reviewed = fixture.reviewed()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.root)), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=['a'*64, 'backup', 'b'*64]), \
                patch('forge_hold_policy.inputs', return_value=reviewed), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote, \
                patch.object(self.owner, 'prepare_recovery_hold') as submit:
            self.panel.hold_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()

    def test_reconciliation_draft_enters_separate_review_without_grants(self):
        import test_reconcile_policy
        fixture = test_reconcile_policy.ReconcilePolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.object(RecoveryProbe, '_observe') as remote:
            self.panel.prepare_reconciliation(fixture.source, fixture.reviewed(), fixture.output)
            self.owner.jobs[self.panel.job][2].result(timeout=5)
        remote.assert_not_called()
        self.panel.poll()
        self.assertIsNotNone(self.panel.pending)
        self.assertIsNone(self.owner.recovery_jobs)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('reconcile-hold', self.panel.details.get('1.0', 'end'))
        self.assertIn('No observation run', self.panel.status.get())
        with self.assertRaises(ValueError): self.owner.call('prepare_recovery_reconciliation', {'workspace': 'gui'})

    def test_declined_reconciliation_draft_does_not_submit(self):
        import test_reconcile_policy
        fixture = test_reconcile_policy.ReconcilePolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.panel.enrollment_ready = ((fixture.source.directory, fixture.source.probe.key,
                                       fixture.source.probe.known_hosts), fixture.f.f.enrollment.accepted)
        self.panel.refresh()
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(fixture.journal), str(fixture.f.f.root)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=['hold', fixture.pin]), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote, \
                patch.object(self.owner, 'prepare_recovery_reconciliation') as submit:
            self.panel.reconcile_prepare_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()

    def test_root_draft_requires_separate_review_and_cannot_be_called_by_mcp(self):
        import test_root_policy
        fixture = test_root_policy.RootPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.object(RecoveryProbe, '_observe') as remote:
            self.panel.prepare_root(fixture.source, fixture.reviewed(), fixture.output, accept_filesystem_errors=True)
            self.owner.jobs[self.panel.job][2].result(timeout=5)
        remote.assert_not_called()
        self.panel.poll()
        self.assertIsNotNone(self.panel.pending)
        self.assertIsNone(self.owner.recovery_jobs)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('restore-root', self.panel.details.get('1.0', 'end'))
        self.assertIn('accept_filesystem_errors', self.panel.details.get('1.0', 'end'))
        self.assertIn('No root bytes written', self.panel.status.get())
        with self.assertRaises(ValueError): self.owner.call('prepare_recovery_root', {'workspace': 'gui'})

    def root_dialog_fixture(self, derived=False):
        import test_root_policy
        fixture = test_root_policy.RootPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if derived: fixture.setup_case(True)
        accepted = json.loads((fixture.enrollment/'acceptance.json').read_text())
        self.panel.enrollment_ready = ((fixture.source.directory, fixture.source.probe.key,
                                       fixture.source.probe.known_hosts), accepted)
        self.panel.refresh()
        return fixture

    def test_declined_source_errors_do_not_draft_restore(self):
        from forge_recovery_bootplan import digest
        f = self.root_dialog_fixture()
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(f.hold), str(f.reconciliation),
                   str(f.manifest), str(f.health)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=[f.f.hold_pin, digest(f.accepted),
                      f.manifest_pin, f.health_pin]), patch('forge_recovery_gui.messagebox.askyesno', return_value=False) as confirm, \
                patch.object(RecoveryProbe, '_observe') as remote, patch.object(self.owner, 'prepare_recovery_root') as submit:
            self.panel.restore_prepare_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(confirm.call_args.args[0], 'Original backup has filesystem errors')
        self.assertEqual(confirm.call_args.kwargs['default'], 'no')

    def test_declined_healthy_deployment_does_not_draft(self):
        from forge_recovery_bootplan import digest
        f = self.root_dialog_fixture(True)
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(f.hold), str(f.reconciliation),
                   str(f.manifest), str(f.health), str(f.root)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=[f.f.hold_pin, digest(f.accepted),
                      f.manifest_pin, f.health_pin]), patch('forge_recovery_gui.messagebox.askyesno', return_value=False) as confirm, \
                patch.object(RecoveryProbe, '_observe') as remote, patch.object(self.owner, 'prepare_recovery_root') as submit:
            self.panel.deploy_prepare_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(confirm.call_args.args[0], 'Prepare root-transfer draft only')

    def test_release_draft_is_separately_reviewed_without_reboot_or_grants(self):
        import test_release_policy
        fixture = test_release_policy.ReleasePolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.object(RecoveryProbe, '_observe') as remote:
            self.panel.prepare_release(fixture.source, fixture.reviewed(), fixture.output, accept_filesystem_errors=True)
            self.owner.jobs[self.panel.job][2].result(timeout=5)
        remote.assert_not_called()
        self.panel.poll()
        self.assertIsNotNone(self.panel.pending)
        self.assertIsNone(self.owner.recovery_jobs)
        self.assertEqual(self.owner.grants, frozenset())
        self.assertIn('release-hold', self.panel.details.get('1.0', 'end'))
        self.assertIn('No selector changed', self.panel.status.get())
        with self.assertRaises(ValueError): self.owner.call('prepare_recovery_release', {'workspace': 'gui'})

    def test_declined_original_errors_prevent_release_draft(self):
        import test_release_policy
        from forge_recovery_bootplan import digest
        f = test_release_policy.ReleasePolicyTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        accepted = json.loads((f.source.directory/'acceptance.json').read_text())
        self.panel.enrollment_ready = ((f.source.directory, f.source.probe.key, f.source.probe.known_hosts), accepted)
        self.panel.refresh()
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(f.journal), str(f.reconciliation),
                   str(f.review_directory), str(f.f.source.source)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=[f.pin, digest(f.accepted), f.review_pin]), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False) as confirm, \
                patch.object(RecoveryProbe, '_observe') as remote, patch.object(self.owner, 'prepare_recovery_release') as submit:
            self.panel.release_prepare_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(confirm.call_args.args[0], 'Normal boot retains original filesystem errors')
        self.assertEqual(confirm.call_args.kwargs['default'], 'no')

    def test_normal_return_clears_stale_ram_controls_without_grants(self):
        import test_normal_return
        f = test_normal_return.NormalReturnTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.panel.enrollment_ready = ((f.source.directory, f.source.probe.key, f.source.probe.known_hosts), {})
        with patch('forge_normal_return.return_to_normal', return_value=dict(status='verified-normal-return')) as returned:
            self.panel.return_normal(f.source, f.frozen, f.output, reboot=True)
            self.assertFalse(self.panel.close())
            self.assertIsNone(self.panel.enrollment_ready)
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            returned.assert_called_once_with(f.output, f.source, f.frozen, reboot=True)
        self.assertIs(self.panel.normal_return_source, f.source)
        self.assertIn('disabled', self.panel.deploy_prepare_button.state())
        self.assertNotIn('disabled', self.panel.normal_verify_button.state())
        self.assertIn('No application, filesystem-health or cleanup qualification', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError): self.owner.call('return_recovery_to_normal', {'workspace': 'gui'})

    def test_cleanup_is_owner_only_and_retains_private_mount_policy(self):
        import test_recovery_cleanup
        f = test_recovery_cleanup.RecoveryCleanupTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        with patch('forge_recovery_cleanup.cleanup', return_value=dict(status='cleaned-staging-and-owned-image')) as cleaned:
            self.panel.cleanup_recovery(f.frozen, f.output)
            self.assertFalse(self.panel.close())
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            cleaned.assert_called_once_with(f.output, f.frozen)
        self.assertIn('permissions remain private', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError): self.owner.call('cleanup_recovery', {'workspace': 'gui'})

    def test_declined_cleanup_does_not_submit(self):
        import test_recovery_cleanup
        f = test_recovery_cleanup.RecoveryCleanupTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(f.f.directory), str(f.output.parent)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=[f.f.pin, f.f.boot['boot_id']]), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(self.owner, 'cleanup_recovery') as submit:
            self.panel.cleanup_button.invoke()
        submit.assert_not_called()

    def test_original_privacy_restoration_is_owner_only_without_reboot(self):
        import test_boot_privacy_restore
        f = test_boot_privacy_restore.PrivacyRestoreTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        with patch('forge_boot_privacy_restore.restore', return_value=dict(status='original-fstab-restored-awaiting-fresh-mount')) as restored:
            self.panel.restore_privacy(f.frozen)
            self.assertFalse(self.panel.close())
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            restored.assert_called_once_with(f.frozen)
        self.assertIn('need fresh-mount verification', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError): self.owner.call('restore_boot_privacy', {'workspace': 'gui'})

    def test_declined_privacy_restoration_never_contacts_target(self):
        import test_boot_privacy_restore
        f = test_boot_privacy_restore.PrivacyRestoreTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[f.clean['staging'], str(f.journal)]), \
                patch('forge_recovery_gui.simpledialog.askstring', side_effect=[f.clean['staging_sha256'], f.boot, f.pin]), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False) as confirm, \
                patch.object(self.owner, 'restore_boot_privacy') as submit:
            self.panel.privacy_restore_button.invoke()
        submit.assert_not_called()
        self.assertEqual(confirm.call_args.kwargs['default'], 'no')

    def test_original_mount_verification_is_owner_only(self):
        import test_boot_mount_restore
        f = test_boot_mount_restore.OriginalMountTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        with patch('forge_boot_mount_restore.verify', return_value=dict(status='verified-original-boot-mount')) as verified:
            self.panel.verify_original_mount(f.frozen)
            self.assertFalse(self.panel.close())
            self.owner.jobs[self.panel.job][2].result(timeout=5)
            self.panel.poll()
            verified.assert_called_once_with(f.frozen, reboot=False)
        self.assertIn('never repeat reboot', self.panel.status.get())
        self.assertEqual(self.owner.grants, frozenset())
        with self.assertRaises(ValueError): self.owner.call('verify_original_boot_mount', {'workspace': 'gui'})

    def test_declined_final_reboot_does_not_submit(self):
        import test_boot_mount_restore
        f = test_boot_mount_restore.OriginalMountTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(f.f.journal)), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value=f.f.pin), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False) as confirm, \
                patch.object(self.owner, 'verify_original_boot_mount') as submit:
            self.panel.original_mount_reboot_button.invoke()
        submit.assert_not_called()
        self.assertEqual(confirm.call_args.kwargs['default'], 'no')

    def retained_fixture(self):
        import test_backup_policy
        f = test_backup_policy.BackupPolicyTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f.source

    def test_open_retained_enrollment_is_offline_and_adds_no_grants(self):
        source = self.retained_fixture()
        from forge_recovery_session import Session
        before = {p.name: p.read_bytes() for p in (source.directory/'session').iterdir()}
        with patch.object(RecoveryProbe, '_observe') as remote, patch.object(Session, 'renew') as renew:
            self.panel.open_enrollment(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts)
        remote.assert_not_called(); renew.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in (source.directory/'session').iterdir()})
        self.assertNotIn('disabled', self.panel.backup_button.state())
        self.assertNotIn('disabled', self.panel.reconcile_prepare_button.state())
        self.assertEqual(self.owner.grants, frozenset())
        self.assertEqual(self.owner.jobs, {})
        self.assertIn('target state is not verified', self.panel.status.get())

    def test_retained_enrollment_busy_or_wrong_pin_keeps_existing_selection(self):
        source = self.retained_fixture()
        from forge_recovery_session import Session
        with Session(source.directory/'session', source.session_pin, source.probe, source.boot_id, source.owner):
            with self.assertRaises(BlockingIOError):
                self.panel.open_enrollment(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts)
        with self.assertRaises(ValueError):
            self.panel.open_enrollment(source.directory, 'f'*64, source.probe.key, source.probe.known_hosts)
        self.assertIsNone(self.panel.enrollment_ready)

    def test_retained_pending_renewal_is_never_reset_or_retried(self):
        source = self.retained_fixture()
        from forge_recovery_session import Session
        from forge_target_journal import write_record
        with Session(source.directory/'session', source.session_pin, source.probe, source.boot_id, source.owner) as session:
            write_record(session.fd, Session.names(1)[0], session.request(1, 300))
        before = {p.name: p.read_bytes() for p in (source.directory/'session').iterdir()}
        with patch.object(RecoveryProbe, '_observe') as remote:
            self.panel.open_enrollment(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts)
        remote.assert_not_called()
        self.assertIn('Pending renewal remains unresolved', self.panel.status.get())
        self.assertEqual(before, {p.name: p.read_bytes() for p in (source.directory/'session').iterdir()})

    def test_declined_retained_enrollment_keeps_panel_unconfigured(self):
        source = self.retained_fixture()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(source.directory)), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value=source.acceptance_pin), \
                patch('forge_recovery_gui.filedialog.askopenfilename', side_effect=[str(source.probe.key), str(source.probe.known_hosts)]), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote:
            self.panel.open_enrollment_button.invoke()
        remote.assert_not_called()
        self.assertIsNone(self.panel.enrollment_ready)

    def test_declined_normal_reboot_does_not_submit(self):
        import test_normal_return
        f = test_normal_return.NormalReturnTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.panel.normal_return_source = f.source
        with patch('forge_recovery_gui.filedialog.askdirectory', side_effect=[str(f.f.value.staging),
                   str(f.directory), str(f.root)]), \
                patch('forge_recovery_gui.simpledialog.askstring', return_value=f.pin), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote, patch.object(self.owner, 'return_recovery_to_normal') as submit:
            self.panel.normal_return_dialog(reboot=True)
        remote.assert_not_called()
        submit.assert_not_called()

    def test_declined_backup_draft_has_no_target_contact(self):
        import test_backup_policy
        fixture = test_backup_policy.BackupPolicyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.panel.enrollment_ready = ((fixture.source.directory, fixture.source.probe.key,
                                       fixture.source.probe.known_hosts), fixture.accepted)
        self.panel.refresh()
        with patch('forge_recovery_gui.filedialog.askdirectory', return_value=str(fixture.root)), \
                patch('forge_recovery_gui.messagebox.askyesno', return_value=False), \
                patch.object(RecoveryProbe, '_observe') as remote, \
                patch.object(self.owner, 'prepare_recovery_backup') as submit:
            self.panel.backup_button.invoke()
        remote.assert_not_called()
        submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
