from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import forge_held_reboot as reboot
import forge_held_reboot_worker as worker
from forge_hold_policy import prepare
from forge_recovery_commit_reconcile import expected_receipt
from forge_recovery_session import Session
from forge_session_enrollment import inputs
from forge_target_journal import write_record
import test_hold_policy


class HeldRebootTests(unittest.TestCase):
    def setUp(self):
        self.f = test_hold_policy.HoldPolicyTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        result = prepare(f.output, f.source, f.reviewed(), 'fixture')
        self.directory, self.pin = f.output/'hold', result['plan_sha256']
        self.plan = json.loads((self.directory/'plan.json').read_text())
        self.source = f.source
        self.value = inputs(f.staging, f.staging_pin, f.source.probe.host, f.source.probe.key,
                            f.source.probe.known_hosts, f.source.probe.kernel, f.source.probe.serial, None, 0)
        attempt = self.directory/'commit-attempt'
        attempt.mkdir(mode=0o700)
        f.save(attempt/'dispatch.json', dict(plan_sha256=self.pin, attempt='a'*32,
            binding=self.plan['binding'], lease_owner=f.source.owner, worker_protocol=2))
        f.save(attempt/'acceptance.json', dict(status='acknowledged', plan_sha256=self.pin,
            attempt='a'*32, root_written=False, receipt=expected_receipt(self.plan, self.pin)))
        self.output = f.root/'held-reboot'
        self.new_boot = '99999999-2222-3333-4444-555555555555'

    def execute(self):
        return reboot.boot_and_enroll(self.output, self.source, self.value, self.directory, self.pin, timeout=1)

    def mocked(self, *, enroll_error=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(Session, 'renew', return_value={'sequence': 8}))
        stack.enter_context(patch.object(type(self.source.probe), 'inspect', side_effect=[
            {'verification': {'boot_id': self.source.boot_id}},
            {'verification': {'boot_id': self.source.boot_id}},
            {'verification': {'boot_id': self.new_boot}}]))
        self.transport = stack.enter_context(patch.object(reboot, 'reboot_transport', return_value={'transport_timeout': True}))
        self.enroll = stack.enter_context(patch.object(reboot, 'enroll',
            side_effect=ValueError('wrong selection') if enroll_error else None,
            return_value={'status': 'enrolled-not-leased'}))
        stack.enter_context(patch.object(reboot.time, 'sleep'))

    def test_fresh_boot_pinned_persistent_selection_no_new_lease(self):
        self.mocked()
        result = self.execute()
        self.transport.assert_called_once()
        value = self.enroll.call_args.args[1]
        self.assertEqual(value.boot_id, self.new_boot)
        self.assertEqual(value.tryboot, 0)
        self.assertTrue(result['previous_session_lease_renewed'])
        for field in ('lease_acquired', 'root_write_authorized', 'normal_boot_release_authorized',
                      'automatic_retry_performed', 'recovery_fallback_qualified'):
            self.assertIs(result[field], False)
        with self.assertRaisesRegex(ValueError, 'already attempted'): self.execute()
        self.assertEqual(self.transport.call_count, 1)

    def test_failed_enrollment_retains_one_use_claim(self):
        self.mocked(enroll_error=True)
        with self.assertRaisesRegex(ValueError, 'wrong selection'): self.execute()
        self.assertTrue(json.loads((self.output/'failure.json').read_text())['reboot_may_have_started'])
        with self.assertRaisesRegex(ValueError, 'already attempted'): self.execute()
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_uncertain_commit_cannot_reboot(self):
        path = self.directory/'commit-attempt/acceptance.json'
        accepted = json.loads(path.read_text())
        self.f.save(path, dict(accepted, status='uncertain'))
        with patch.object(reboot, 'reboot_transport') as remote:
            with self.assertRaisesRegex(ValueError, 'acknowledged exact'): self.execute()
            remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_wrong_selection_or_existing_boot_rejected(self):
        for value in (replace(self.value, tryboot=1), replace(self.value, boot_id=self.new_boot)):
            with self.assertRaises(ValueError): reboot.reviewed(self.source, value, self.directory, self.pin)

    def test_busy_or_pending_lease_is_not_replaced(self):
        with Session(self.source.directory/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner) as session:
            with self.assertRaises(BlockingIOError): self.execute()
            write_record(session.fd, Session.names(1)[0], session.request(1, 300))
        with self.assertRaisesRegex(ValueError, 'uncertain lease'): self.execute()
        self.assertFalse(self.output.exists())

    def test_failed_renewal_retains_claim_without_dispatch(self):
        with patch.object(type(self.source.probe), 'inspect'), \
                patch.object(Session, 'renew', side_effect=OSError('disconnected')), \
                patch.object(reboot, 'reboot_transport') as remote:
            with self.assertRaises(OSError): self.execute()
            remote.assert_not_called()
        failure = json.loads((self.output/'failure.json').read_text())
        self.assertFalse(failure['reboot_may_have_started'])
        self.assertFalse(failure['previous_session_lease_renewed'])
        with self.assertRaisesRegex(ValueError, 'already attempted'): self.execute()

    def test_payload_explicit_stdin_and_fixed_worker(self):
        from types import SimpleNamespace
        with patch.object(reboot.subprocess, 'run', return_value=SimpleNamespace(returncode=255)) as remote:
            result = reboot.reboot_transport(self.source, self.plan, self.pin, {'sequence': 8}, 'b'*32)
        data = json.loads(remote.call_args.kwargs['input'])
        self.assertEqual(data['plan'], self.plan)
        self.assertEqual(data['lease'], {'sequence': 8})
        self.assertEqual(data['modules'][-1][0], 'forge_held_reboot_worker')
        self.assertFalse(result['reboot_verified'])
        self.assertEqual(result['returncode'], 255)

    def test_real_isolated_bootstrap_imports_before_rejecting_invalid_pin(self):
        import subprocess
        import sys
        # Run the actual shipped source bundle in isolated Python. Invalid
        # approval must fail at validation, before any target observation.
        with patch.object(reboot.subprocess, 'run') as remote:
            remote.return_value.returncode = 255
            reboot.reboot_transport(self.source, self.plan, '0'*64, {}, 'b'*32)
        request = remote.call_args.kwargs['input']
        bootstrap = reboot.BOOTSTRAP.replace('from forge_recovery_commit_worker import run',
                                             'from forge_held_reboot_worker import run')
        result = subprocess.run([sys.executable, '-I', '-S', '-c', bootstrap], input=request,
                                text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Boot commit plan digest differs', result.stderr)
        self.assertNotIn('ModuleNotFoundError', result.stderr)

    def test_different_sealed_hold_review_cannot_reboot(self):
        from forge_recovery_bootplan import digest
        self.plan['hold_review_sha256'] = 'f'*64
        self.pin = digest(self.plan)
        self.f.save(self.directory/'plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'Hold differs'): self.execute()
        self.assertFalse(self.output.exists())

    def worker(self, *, state='after', budgets=(200, 100), layout_error=False, unmount_error=False):
        events = []
        @contextmanager
        def mounted(*args, **kwargs):
            self.assertIs(kwargs['read_only'], True)
            events.append('mounted')
            yield '/fixture'
            if unmount_error: raise RuntimeError('unmount failed')
            events.append('unmounted')
        @contextmanager
        def locked(*args, **kwargs):
            events.append('locked')
            yield
            events.append('unlocked')
        def dispatch(*args, **kwargs):
            self.assertEqual(events, ['locked', 'mounted', 'unmounted'])
        with patch.object(worker.os, 'geteuid', return_value=0), \
                patch.object(worker, 'layout', side_effect=ValueError('wrong boot') if layout_error else None), \
                patch.object(worker, 'ensure_lock_directory'), patch.object(worker, 'target_lock', side_effect=locked), \
                patch.object(worker, 'claim_root', side_effect=lambda *a: nullcontext()), \
                patch.object(worker, 'commit_timer', side_effect=lambda *a: nullcontext()), \
                patch.object(worker, 'mounted_boot', side_effect=mounted), \
                patch.object(worker, 'inspect_files', return_value={'status': state}), \
                patch.object(worker, 'budget', side_effect=budgets), \
                patch.object(worker.subprocess, 'run', side_effect=dispatch) as command:
            request = dict(plan=self.plan, pin=self.pin, owner=self.source.owner, lease={}, query='b'*32)
            if state != 'after' or min(budgets) < 30 or budgets[0] < 180 or layout_error or unmount_error:
                with self.assertRaises((ValueError, RuntimeError)): worker.run(request)
                command.assert_not_called()
            else:
                worker.run(request)
                command.assert_called_once_with(['/sbin/reboot', '-f'], check=True, timeout=15,
                                                stdin=worker.subprocess.DEVNULL)

    def test_worker_unmounts_before_single_fixed_reboot_under_lock(self): self.worker()

    def test_worker_refuses_changed_files_image_boot_and_unmount_failure(self):
        self.worker(state='conflict')
        self.worker(state='before')
        self.worker(layout_error=True)
        self.worker(unmount_error=True)

    def test_worker_requires_budget_before_and_after_inspection(self):
        self.worker(budgets=(179, 100))
        self.worker(budgets=(200, 29))
