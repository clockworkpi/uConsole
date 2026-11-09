import copy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import forge_normal_return as normal
import forge_normal_reboot_worker as worker
import forge_held_reboot_worker as held_worker
from forge_recovery_bootplan import digest
from forge_recovery_commit_reconcile import expected_receipt
from forge_recovery_session import Session
import test_held_reboot


class NormalReturnTests(unittest.TestCase):
    def setUp(self):
        self.f = test_held_reboot.HeldRebootTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.source, self.root = self.f.source, self.f.f.root
        self.plan = copy.deepcopy(self.f.plan)
        self.plan.update(operation='release-hold', before=self.f.plan['after'], after=self.f.plan['before'])
        self.pin = digest(self.plan)
        self.directory = self.f.directory
        self.save(self.directory/'plan.json', self.plan)
        self.save(self.directory/'commit-attempt/dispatch.json', dict(plan_sha256=self.pin, attempt='a'*32,
            binding=self.plan['binding'], lease_owner=self.source.owner, worker_protocol=2))
        self.save(self.directory/'commit-attempt/acceptance.json', dict(status='acknowledged', plan_sha256=self.pin,
            attempt='a'*32, root_written=False, receipt=expected_receipt(self.plan, self.pin)))
        self.frozen = self.inputs()
        self.output = self.root/'normal-return'
        self.native = dict(self.frozen['original_boot'], boot_id='99999999-2222-3333-4444-555555555555')
        self.identity = dict(boot_id=self.native['boot_id'], kernel=self.source.probe.kernel, serial=self.source.probe.serial)
        self.observed = dict(before=self.native, after=self.native, identity=self.identity,
                            verification=dict(status='verified-normal', boot_id=self.native['boot_id'], recovery_qualified=False))

    def save(self, path, value): self.f.f.save(path, value)

    def inputs(self):
        return normal.inputs(self.source, self.f.value.staging, self.f.value.staging_pin, self.directory, self.pin)

    def test_one_reboot_and_fresh_normal_boot_not_app_or_cleanup_qualification(self):
        with patch.object(type(self.source.probe), 'inspect'), patch.object(Session, 'renew', return_value={}), \
                patch.object(normal, 'reboot_transport', return_value={'transport_timeout': True}) as reboot, \
                patch.object(normal, 'observe', return_value=self.observed):
            result = normal.return_to_normal(self.output, self.source, self.frozen, reboot=True)
            reboot.assert_called_once()
            with self.assertRaisesRegex(ValueError, 'already attempted'):
                normal.return_to_normal(self.root/'retry', self.source, self.frozen, reboot=True)
        self.assertEqual(result['status'], 'verified-normal-return')
        self.assertTrue(result['reboot_request_dispatched'])
        for key in ('cleanup_performed', 'filesystem_health_qualified', 'native_application_qualified',
                    'root_write_authorized', 'automatic_retry_performed'):
            self.assertIs(result[key], False)

    def test_uncertain_return_preserves_claim_and_can_be_verified_without_lease_or_reboot(self):
        with patch.object(type(self.source.probe), 'inspect'), patch.object(Session, 'renew', return_value={}), \
                patch.object(normal, 'reboot_transport', return_value={'transport_timeout': True}), \
                patch.object(normal, 'observe', side_effect=ValueError('not ready')), \
                patch.object(normal.time, 'monotonic', side_effect=[0, 1]):
            with self.assertRaises(RuntimeError):
                normal.return_to_normal(self.output, self.source, self.frozen, reboot=True, timeout=1)
        failure = (self.output/'failure.json').read_bytes()
        with patch.object(normal, 'reboot_transport') as reboot, patch.object(normal, 'Session') as lease, \
                patch.object(normal, 'observe', return_value=self.observed):
            result = normal.return_to_normal(self.root/'verification', self.source, self.frozen)
        reboot.assert_not_called()
        lease.assert_not_called()
        self.assertTrue(result['read_only'])
        self.assertFalse(result['previous_session_lease_renewed'])
        self.assertEqual((self.output/'failure.json').read_bytes(), failure)

    def test_acknowledged_release_not_uncertain_or_install_is_required(self):
        self.save(self.directory/'commit-attempt/acceptance.json', dict(status='uncertain'))
        with self.assertRaises(ValueError): self.inputs()
        self.save(self.directory/'plan.json', self.f.plan)
        self.pin = self.f.pin
        with self.assertRaisesRegex(ValueError, 'Normal return differs'): self.inputs()

    def test_busy_session_stops_before_claim(self):
        with Session(self.source.directory/'session', self.source.session_pin, self.source.probe,
                     self.source.boot_id, self.source.owner):
            with self.assertRaises(BlockingIOError):
                normal.return_to_normal(self.output, self.source, self.frozen, reboot=True)
        self.assertFalse((self.directory/'normal-reboot-attempt.json').exists())
        self.assertFalse(self.output.exists())

    def test_native_bookends_kernel_and_hardware_serial_are_checked(self):
        with patch.object(normal, 'capture', return_value=self.native), patch.object(normal, 'native_identity', return_value=self.identity):
            self.assertEqual(normal.observe(self.source, self.frozen), self.observed)
        for identity in (dict(self.identity, kernel='wrong'), dict(self.identity, serial='0'*16)):
            with patch.object(normal, 'capture', return_value=self.native), patch.object(normal, 'native_identity', return_value=identity):
                with self.assertRaises(ValueError): normal.observe(self.source, self.frozen)
        with patch.object(normal, 'capture', side_effect=[self.native, dict(self.native, boot_id=self.source.boot_id)]), \
                patch.object(normal, 'native_identity', return_value=self.identity):
            with self.assertRaisesRegex(ValueError, 'changed'): normal.observe(self.source, self.frozen)

    def test_original_normal_boot_is_not_a_new_return(self):
        with patch.object(normal, 'capture', return_value=self.frozen['original_boot']):
            with self.assertRaisesRegex(ValueError, 'previous boot'): normal.observe(self.source, self.frozen)

    def test_normal_worker_uses_same_unmount_and_writer_exclusion_gate(self):
        self.f.plan, self.f.pin = self.plan, self.pin
        with patch.object(held_worker, 'run', worker.run):
            self.f.worker()
            self.f.worker(state='conflict')
            self.f.worker(unmount_error=True)
            self.f.worker(budgets=(200, 29))

    def test_normal_worker_cannot_accept_install_plan(self):
        with patch.object(held_worker, 'ensure_lock_directory') as locks, patch.object(held_worker.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'selector-transition'):
                worker.run(dict(plan=self.f.plan, pin=self.f.pin, owner=self.source.owner, lease={}))
            locks.assert_not_called()

    def test_fixed_bundle_imports_and_explicit_transport_stdin(self):
        with patch.object(normal.subprocess, 'run') as remote:
            remote.return_value.returncode = 255
            normal.reboot_transport(self.source, dict(self.frozen, plan_sha256='0'*64), {}, 'b'*32)
        request = remote.call_args.kwargs['input']
        bootstrap = normal.BOOTSTRAP.replace('from forge_recovery_commit_worker import run', 'from forge_normal_reboot_worker import run')
        result = subprocess.run([sys.executable, '-I', '-S', '-c', bootstrap], input=request,
                                text=True, capture_output=True, timeout=10)
        self.assertIn('Boot commit plan digest differs', result.stderr)
        with patch.object(normal.subprocess, 'run') as capture:
            capture.return_value.stdout = json.dumps(self.identity)
            normal.native_identity('fixture')
        self.assertIs(capture.call_args.kwargs['stdin'], subprocess.DEVNULL)
