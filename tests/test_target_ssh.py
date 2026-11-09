import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_journal as journal
import forge_target_ssh as ssh
if __package__:
    from .target_test_support import identity as fixture_identity, linux_target
else:
    from target_test_support import identity as fixture_identity, linux_target


class TargetSSHTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.path = self.root / 'app'
        self.directory = self.root / 'journal'
        identity = (Path('/etc/machine-id').read_text().strip() if sys.platform.startswith('linux')
                    else fixture_identity()['machine_id'])
        self.before = {'schema': 1, 'machine_id': identity,
                       'files': [{'path': str(self.path), 'kind': 'absent'}]}
        data = b'proof'
        self.after = dict(self.before, files=[{'path': str(self.path), 'kind': 'file',
            'data': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data), 'mode': 0o700, 'uid': os.getuid(), 'gid': os.getgid(),
            'atime_ns': 1000000000, 'mtime_ns': 1000000000, 'xattrs': {}}])
        journal.prepare(self.directory, 'clockworkpi.local', self.before, self.after)

    def transport(self, command, **kwargs):
        request = json.loads(kwargs['input'])
        # Prove dispatch was journaled before the transport was invoked.
        record = json.loads(sorted(self.directory.glob('event-*.json'))[-1].read_text())
        self.assertEqual(record['state'], 'dispatch')
        result = ssh.perform(request['plan'], request['direction'], request['nonce'],
                             lock_path=str(self.root / 'target.lock'))
        return subprocess.CompletedProcess(command, 0, json.dumps(result), '')

    @linux_target
    def test_dispatch_apply_restore(self):
        with patch('forge_target_ssh.subprocess.run', side_effect=self.transport):
            result = ssh.dispatch(self.directory, 'apply')
            self.assertEqual(result['files'][0]['status'], 'applied')
            self.assertEqual(self.path.read_bytes(), b'proof')
            ssh.dispatch(self.directory, 'restore')
            self.assertFalse(self.path.exists())
        with journal.locked(self.directory) as (fd, _):
            self.assertEqual([e['state'] for e in journal.events(fd)],
                             ['dispatch', 'acknowledged', 'dispatch', 'acknowledged'])

    @linux_target
    def test_lost_ack_requires_same_direction_reconciliation(self):
        def lose_ack(command, **kwargs):
            self.transport(command, **kwargs)
            raise subprocess.TimeoutExpired(command, 60)
        with patch('forge_target_ssh.subprocess.run', side_effect=lose_ack):
            with self.assertRaises(ssh.TargetUncertain):
                ssh.dispatch(self.directory, 'apply')
        self.assertTrue(self.path.exists())
        with patch('forge_target_ssh.subprocess.run') as run:
            with self.assertRaises(ssh.TargetUncertain):
                ssh.dispatch(self.directory, 'restore')
            run.assert_not_called()
        with patch('forge_target_ssh.subprocess.run', side_effect=self.transport):
            result = ssh.dispatch(self.directory, 'apply')
            self.assertEqual(result['files'][0]['status'], 'already-applied')
            ssh.dispatch(self.directory, 'restore')
        self.assertFalse(self.path.exists())

    @linux_target
    def test_wrong_machine_refused_before_target_lock_or_file(self):
        with journal.locked(self.directory) as (_, plan):
            plan['before']['machine_id'] = plan['after']['machine_id'] = '0' * 32
            with self.assertRaises(ValueError):
                ssh.perform(plan, 'apply', 'test', lock_path=str(self.root / 'target.lock'))
        self.assertFalse((self.root / 'target.lock').exists())
        self.assertFalse(self.path.exists())

    def test_bad_ack_is_uncertain(self):
        bad = subprocess.CompletedProcess([], 0, '{}', '')
        with patch('forge_target_ssh.subprocess.run', return_value=bad):
            with self.assertRaises(ssh.TargetUncertain):
                ssh.dispatch(self.directory, 'apply')
        with journal.locked(self.directory) as (fd, _):
            self.assertEqual(journal.events(fd)[-1]['state'], 'uncertain')

    def test_owner_digest_checked_before_dispatch_event_or_ssh(self):
        with patch('forge_target_ssh.subprocess.run') as run:
            with self.assertRaises(PermissionError):
                ssh.dispatch(self.directory, 'apply', approved_plan_sha256='0' * 64)
            run.assert_not_called()
        with journal.locked(self.directory) as (fd, _):
            self.assertEqual(journal.events(fd), [])

    @linux_target
    def test_bundled_worker_runs_without_installed_modules(self):
        with journal.locked(self.directory) as (_, plan):
            request = json.loads(ssh.payload(plan, 'apply', 'test'))
        request['modules'] = [(name, source.replace("'/run/lock/uconsole-forge-target.lock'",
                             repr(str(self.root / 'target.lock')))) for name, source in request['modules']]
        result = subprocess.run([sys.executable, '-I', '-c', ssh.BOOTSTRAP],
                                input=json.dumps(request), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['nonce'], 'test')
        self.assertEqual(self.path.read_bytes(), b'proof')


if __name__ == '__main__':
    unittest.main()
