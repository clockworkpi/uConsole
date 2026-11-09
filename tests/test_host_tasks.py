"""Host execution requires owner-pinned definitions and an explicit grant."""
import hashlib
import json
import os
import select
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_controller import Controller, JobCancelled
from forge_host_tasks import HostTasks
from forge_workspace import WorkspaceLock
from uconsole_mcp import BY_NAME, validate


class HostTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / 'policy.json'
        self.workspaces = {'test': self.root / 'image', 'other': self.root / 'other-image'}
        self.definition = {'schema': 1, 'tasks': {'check': {
            'workspace': 'test', 'cwd': str(self.root), 'timeout': 10,
            'argv': [str(Path(sys.executable).resolve()), '-c',
                     'import sys; print(sys.argv[1]); print("error", file=sys.stderr); sys.exit(7)',
                     '$(touch forbidden)']}}}
        self.digest = self.save()

    def save(self):
        payload = json.dumps(self.definition).encode()
        self.policy.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def controller(self, granted=True):
        controller = Controller(self.workspaces, grants=('host-task',) if granted else (),
                                host_task_policy=self.policy, host_task_sha256=self.digest)
        self.addCleanup(controller.close)
        return controller

    def test_hash_mismatch_and_unpinned_grant_rejected(self):
        with self.assertRaisesRegex(ValueError, 'approved SHA-256'):
            HostTasks(self.policy, '0' * 64, self.workspaces)
        with self.assertRaisesRegex(ValueError, 'approved policy'):
            Controller(self.workspaces, grants=('host-task',))

    def test_unapproved_workspace_and_argument_overrides_rejected(self):
        controller = self.controller()
        with self.assertRaisesRegex(ValueError, 'not approved'):
            controller.call('host_task', {'workspace': 'other', 'task': 'check'})
        with self.assertRaises(ValueError):
            validate(BY_NAME['host_task']['inputSchema'],
                     {'workspace': 'test', 'task': 'check', 'argv': ['/bin/sh']})
        self.assertEqual(controller.jobs, {})

    def test_readonly_can_inspect_but_cannot_execute_policy(self):
        controller = self.controller(granted=False)
        listing = controller.call('host_tasks', {'workspace': 'test'})
        self.assertFalse(listing['execution_granted'])
        self.assertEqual(listing['tasks'][0]['policy_sha256'], self.digest)
        with self.assertRaises(PermissionError):
            controller.call('host_task', {'workspace': 'test', 'task': 'check'})

    def test_policy_snapshot_is_not_changed_by_later_file_edits(self):
        controller = self.controller()
        self.definition['tasks']['check']['argv'] = ['/bin/false']
        self.save()
        task = controller.host_tasks.get('check', 'test')
        self.assertEqual(task.argv[-1], '$(touch forbidden)')
        self.assertEqual(controller.host_tasks.sha256, self.digest)

    @unittest.skipUnless(os.name == 'posix', 'POSIX host process ownership')
    def test_fixed_argv_runs_without_shell_expansion_and_returns_exit_code(self):
        with patch.dict(os.environ, {'FORGE_TEST_SECRET': 'not-forwarded'}):
            controller = self.controller()
        self.assertNotIn('FORGE_TEST_SECRET', controller.host_tasks.environment)
        job = controller.call('host_task', {'workspace': 'test', 'task': 'check'})
        result = controller.jobs[job['job_id']][2].result(timeout=5)
        self.assertEqual(result['exit_code'], 7)
        self.assertEqual(result['stdout'], '$(touch forbidden)\n')
        self.assertEqual(result['stderr'], 'error\n')
        self.assertFalse((self.root / 'forbidden').exists())
        self.assertEqual(result['policy_sha256'], self.digest)
        self.assertEqual(controller.job(job['job_id'])['context']['policy_sha256'], self.digest)
        with WorkspaceLock(self.root):
            pass

    def test_busy_task_directory_prevents_execution(self):
        controller = self.controller()
        with WorkspaceLock(self.root), patch('forge_controller.subprocess.Popen') as launch:
            job = controller.call('host_task', {'workspace': 'test', 'task': 'check'})
            with self.assertRaisesRegex(ValueError, 'busy'):
                controller.jobs[job['job_id']][2].result(timeout=5)
            launch.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX host process ownership')
    def test_running_host_task_can_be_cancelled_without_a_vm(self):
        self.definition['tasks']['check']['argv'] = [str(Path(sys.executable).resolve()), '-c',
                                                    'import signal; signal.pause()']
        self.digest = self.save()
        controller = self.controller()
        started = threading.Event()
        children = []
        popen = subprocess.Popen

        def launch(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            started.set()
            return child

        with patch('forge_controller.subprocess.Popen', side_effect=launch):
            job = controller.call('host_task', {'workspace': 'test', 'task': 'check'})
            self.assertTrue(started.wait(5))
            self.assertTrue(controller.cancel(job['job_id'])['requested'])
            with self.assertRaises(JobCancelled):
                controller.jobs[job['job_id']][2].result(timeout=15)
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(controller.runtimes, {})
        with WorkspaceLock(self.root):
            pass

    def test_policy_rejects_duplicate_keys_and_relative_executables(self):
        self.policy.write_text('{"schema": 1, "schema": 1, "tasks": {}}')
        digest = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            HostTasks(self.policy, digest, self.workspaces)
        self.definition['tasks']['check']['argv'] = ['make', 'check']
        with self.assertRaisesRegex(ValueError, 'absolute executable'):
            HostTasks(self.policy, self.save(), self.workspaces)

    @unittest.skipUnless(os.name == 'posix', 'POSIX host process ownership')
    def test_approved_deadline_stops_host_task_and_releases_directory(self):
        self.definition['tasks']['check']['argv'] = [str(Path(sys.executable).resolve()), '-c',
                                                    'import time; time.sleep(30)']
        self.definition['tasks']['check']['timeout'] = 1
        self.digest = self.save()
        controller = self.controller()
        job = controller.call('host_task', {'workspace': 'test', 'task': 'check'})
        with self.assertRaisesRegex(TimeoutError, 'Host task stopped'):
            controller.jobs[job['job_id']][2].result(timeout=15)
        self.assertEqual(controller.job(job['job_id'])['status'], 'failed')
        self.assertEqual(controller.job(job['job_id'])['context']['policy_sha256'], self.digest)
        with WorkspaceLock(self.root):
            pass

    @unittest.skipUnless(os.name == 'posix', 'POSIX host process ownership')
    def test_cancel_kills_term_ignoring_child_before_reaping_leader(self):
        controller = self.controller()
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(lambda: os.close(write_fd) if write_fd is not None else None)
        script = ('import os, signal; '
                  'child = os.fork(); '
                  'signal.alarm(30); '
                  'signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; '
                  f'os.write({write_fd}, b"R") if child == 0 else None; '
                  'signal.pause()')
        signals = []
        killpg, wait = os.killpg, subprocess.Popen.wait

        def tracked_signal(pid, sig):
            signals.append(sig)
            return killpg(pid, sig)

        def checked_wait(process, *args, **kwargs):
            if signal.SIGTERM in signals:
                self.assertIn(signal.SIGKILL, signals,
                              'Leader must remain unreaped until final group signal')
            return wait(process, *args, **kwargs)

        with patch('forge_controller.os.killpg', side_effect=tracked_signal), \
                patch.object(subprocess.Popen, 'wait', checked_wait):
            job = controller.submit('test', 'child-cancel-test', lambda: controller.process_job(
                [sys.executable, '-c', script], owned_fds=(write_fd,),
                stopped='fixture cancelled'), cancellable=True)
            self.assertTrue(select.select([read_fd], [], [], 5)[0])
            self.assertEqual(os.read(read_fd, 1), b'R')
            os.close(write_fd)
            write_fd = None
            controller.cancel(job['job_id'])
            with self.assertRaises(JobCancelled):
                controller.jobs[job['job_id']][2].result(timeout=15)
            # Both processes inherited the writer. EOF proves neither keeps
            # running, including the child that ignored the initial TERM.
            self.assertTrue(select.select([read_fd], [], [], 2)[0])
            self.assertEqual(os.read(read_fd, 1), b'')
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])


if __name__ == '__main__':
    unittest.main()
