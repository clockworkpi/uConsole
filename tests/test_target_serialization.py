import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from forge_controller import Controller
from forge_target_journal import prepare
from target_test_support import file_backup


class TargetSerializationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        transactions = {}
        for name, host, machine in (('first', 'clockworkpi.local', 'a'*32),
                                    ('alias', '10.11.100.223', 'a'*32),
                                    ('independent', 'another.local', 'b'*32)):
            before = file_backup('/var/tmp/forge-fixture')
            before['machine_id'] = machine
            plan = prepare(self.root/name, host, before, copy.deepcopy(before))
            transactions[name] = dict(workspace=name, journal=plan['journal'], plan_sha256=plan['plan_sha256'])
        policy = self.root/'policy.json'
        policy.write_text(json.dumps(dict(schema=1, transactions=transactions)))
        self.owner = Controller({name: self.root/('workspace-'+name) for name in transactions},
                                ('target-write',), target_policy=policy,
                                target_policy_sha256=hashlib.sha256(policy.read_bytes()).hexdigest())
        self.addCleanup(self.owner.close)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def test_aliases_excluded_across_workspaces_then_released(self):
        started = threading.Event()
        def dispatch(*args, **kwargs):
            started.set()
            if not self.release.wait(5):
                raise TimeoutError('test worker release')
            return {'status': 'fixture'}
        with patch('forge_target_ssh.dispatch', side_effect=dispatch) as execute:
            first = self.owner.submit_target('first', 'first', 'apply')
            self.assertTrue(started.wait(2))
            with self.assertRaisesRegex(ValueError, 'physical target'):
                self.owner.submit_target('alias', 'alias', 'restore')
            with self.assertRaisesRegex(ValueError, 'physical target'):
                self.owner.submit_target_recovery('alias', 'alias')
            self.assertEqual(execute.call_count, 1)
            independent = self.owner.submit_target('independent', 'independent', 'apply')
            self.assertEqual(set(self.owner.target_busy), {'a'*32, 'b'*32})
            self.release.set()
            self.owner.jobs[first['job_id']][2].result(timeout=3)
            self.owner.jobs[independent['job_id']][2].result(timeout=3)
            self.assertEqual(self.owner.target_busy, {})
            next_job = self.owner.submit_target('alias', 'alias', 'restore')
            self.owner.jobs[next_job['job_id']][2].result(timeout=3)
            self.assertEqual(execute.call_count, 3)

    def test_failed_execution_and_submission_release_target(self):
        with patch('forge_target_ssh.dispatch', side_effect=RuntimeError('fixture failure')):
            job = self.owner.submit_target('first', 'first', 'apply')
            with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                self.owner.jobs[job['job_id']][2].result(timeout=3)
        self.assertEqual(self.owner.target_busy, {})
        self.assertEqual(self.owner.busy, {})
        with patch.object(self.owner.executor, 'submit', side_effect=RuntimeError('executor rejected')):
            with self.assertRaisesRegex(RuntimeError, 'executor rejected'):
                self.owner.submit_target('alias', 'alias', 'apply')
        self.assertEqual(self.owner.target_busy, {})
        self.assertEqual(self.owner.busy, {})

    def test_queued_cancellation_releases_identity_without_dispatch(self):
        # Occupy this owner's executor without physical jobs, leaving a known
        # queued target future that can be cancelled before any remote effect.
        blockers = [self.owner.executor.submit(self.release.wait, 5) for _ in range(4)]
        with patch('forge_target_ssh.dispatch') as execute:
            job = self.owner.submit_target('first', 'first', 'apply')
            self.assertTrue(self.owner.cancel(job['job_id'])['cancelled'])
            self.assertEqual(self.owner.target_busy, {})
            self.assertEqual(self.owner.busy, {})
            execute.assert_not_called()
        self.release.set()
        for future in blockers:
            future.result(timeout=3)

    def test_stale_finish_does_not_release_later_owner(self):
        self.owner.target_busy['a'*32] = 'new-job'
        self.owner.finished('first', 'old-job')
        self.assertEqual(self.owner.target_busy['a'*32], 'new-job')
        self.owner.target_busy.clear()

    def test_changed_owner_pin_is_rejected_before_job_creation(self):
        path = self.root/'first/plan.json'
        record = json.loads(path.read_text())
        record['host'] = 'unexpected.local'
        path.write_text(json.dumps(record))
        with self.assertRaises(PermissionError):
            self.owner.submit_target('first', 'first', 'apply')
        self.assertEqual(self.owner.jobs, {})
        self.assertEqual(self.owner.target_busy, {})
