import base64
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_effects import TargetEffects, fingerprint
from forge_target_phases import canonical
from forge_target_service_runtime import execute, validate
from forge_target_ssh import target_lock
if __package__:
    from .target_test_support import linux_target
else:
    from target_test_support import linux_target


@linux_target
class ServiceRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.ledgers = self.root / 'ledgers'
        self.ledgers.mkdir(mode=0o700)
        self.lock = self.root / 'target.lock'
        path = str(self.root / 'proof')
        self.path = Path(path)
        data = b'proof'
        absent = {'path': path, 'kind': 'absent'}
        file = {'path': path, 'kind': 'file', 'data': base64.b64encode(data).decode(),
                'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644,
                'uid': os.getuid(), 'gid': os.getgid(), 'atime_ns': 1000000000, 'mtime_ns': 1000000000, 'xattrs': {}}
        scope = {'services': [], 'files': [path], 'links': []}
        before = {'services': {}, 'files': {path: absent}, 'links': {}}
        after = {'services': {}, 'files': {path: fingerprint(file)}, 'links': {}}
        self.envelope = {'schema': 1, 'scope': scope, 'plan': dict(TargetEffects.identity(), schema=1, phases=[
            {'id': 'file', 'before': before, 'after': after, 'repeatable': True,
             'action': {'operation': 'file', 'observe': scope, 'expected': absent, 'desired': file, 'stage_token': 'a' * 32}},
            {'id': 'verify', 'before': after, 'after': after, 'repeatable': True,
             'action': {'operation': 'verify', 'observe': scope}}])}

    def digest(self):
        return hashlib.sha256(canonical(self.envelope).encode()).hexdigest()

    def execute(self, digest=None):
        return execute(self.envelope, digest or self.digest(), ledger_parent=self.ledgers, lock_path=self.lock)

    def test_pinned_envelope_writes_and_verifies_under_private_ledger(self):
        result = self.execute()
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(self.path.read_bytes(), b'proof')
        self.assertEqual(Path(result['ledger']).stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.execute()['status'], 'verified')

    def test_same_global_lock_excludes_file_and_service_workers(self):
        with target_lock(self.lock):
            with self.assertRaises(BlockingIOError):
                self.execute()
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.ledgers.iterdir()), [])

    def test_wrong_digest_or_identity_refused_before_lock_or_ledger(self):
        with self.assertRaises(PermissionError):
            self.execute('0' * 64)
        self.envelope['plan']['machine_id'] = '0' * 32
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.lock.exists())
        self.assertFalse(self.path.exists())

    def test_bad_later_phase_rejected_before_earlier_effect(self):
        self.envelope['plan']['phases'][-1]['action']['operation'] = 'arbitrary-command'
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.lock.exists())

    def test_fake_file_postcondition_cannot_skip_requested_write(self):
        self.envelope['plan']['phases'][0]['after'] = copy.deepcopy(self.envelope['plan']['phases'][0]['before'])
        with self.assertRaisesRegex(ValueError, 'intended contents'):
            self.execute()
        self.assertFalse(self.path.exists())

    def test_final_verification_cannot_omit_affected_object(self):
        phase = self.envelope['plan']['phases'][-1]
        phase['action'] = {'operation': 'verify', 'observe': {'services': [], 'files': [], 'links': []}}
        phase['before'] = phase['after'] = {'services': {}, 'files': {}, 'links': {}}
        with self.assertRaisesRegex(ValueError, 'every approved object'):
            self.execute()

    def test_insecure_parent_refused(self):
        self.ledgers.chmod(0o755)
        with self.assertRaises(PermissionError):
            self.execute()
        self.assertFalse(self.path.exists())

    def test_reload_invoked_even_when_properties_already_match(self):
        unit = 'proof.service'
        scope = {'services': [unit], 'files': [], 'links': []}
        state = {'services': {unit: {'NeedDaemonReload': 'no'}}, 'files': {}, 'links': {}}
        self.envelope['scope'] = scope
        self.envelope['plan']['phases'] = [
            {'id': 'reload', 'before': state, 'after': state, 'repeatable': True,
             'action': {'operation': 'daemon-reload', 'observe': scope}},
            {'id': 'verify', 'before': state, 'after': state, 'repeatable': True,
             'action': {'operation': 'verify', 'observe': scope}}]
        with patch.object(TargetEffects, 'observe', return_value=state), \
                patch.object(TargetEffects, 'command') as command:
            self.execute()
            command.assert_called_once_with(['daemon-reload'])
            self.execute()
            command.assert_called_once_with(['daemon-reload'])

    def published_scratch(self, phase):
        from forge_target_files import apply_file
        from forge_target_links import apply_link
        action = phase['action']
        file = action['operation'] == 'file'
        scratch = self.root / (('.uconsole-forge-' if file else '.uconsole-link-') + action['stage_token'])
        desired = dict(action['desired'], path=str(scratch))
        (apply_file if file else apply_link)({'path': str(scratch), 'kind': 'absent'}, desired)
        os.link(scratch, self.path, follow_symlinks=False)
        return scratch

    def test_interrupted_file_publication_reconciles_second_name_before_observation(self):
        scratch = None
        def interrupted(phase):
            nonlocal scratch
            scratch = self.published_scratch(phase)
            raise ConnectionError('lost after publication, before scratch unlink')
        with patch.object(TargetEffects, 'act', side_effect=interrupted) as act:
            with self.assertRaises(ConnectionError):
                self.execute()
            self.assertEqual(act.call_count, 1)
        self.assertEqual(self.path.stat().st_nlink, 2)
        with patch.object(TargetEffects, 'act') as act:
            self.assertEqual(self.execute()['status'], 'verified')
            act.assert_not_called()
        self.assertFalse(scratch.exists())
        self.assertEqual(self.path.stat().st_nlink, 1)

    def test_interrupted_symlink_publication_reconciles_without_following_target(self):
        first, final = self.envelope['plan']['phases']
        action = first['action']
        desired = {'path': str(self.path), 'kind': 'symlink', 'target': '../unit.service',
                   'uid': os.getuid(), 'gid': os.getgid(), 'mtime_ns': 1000000000}
        scope = {'services': [], 'files': [], 'links': [str(self.path)]}
        self.envelope['scope'] = scope
        action.update(operation='link', desired=desired, observe=scope)
        first['before'] = {'services': {}, 'files': {}, 'links': {str(self.path): action['expected']}}
        first['after'] = {'services': {}, 'files': {}, 'links': {str(self.path): desired}}
        final.update(before=first['after'], after=first['after'], action={'operation': 'verify', 'observe': scope})
        def interrupted(phase):
            self.published_scratch(phase)
            raise ConnectionError('lost ack')
        with patch.object(TargetEffects, 'act', side_effect=interrupted):
            with self.assertRaises(ConnectionError):
                self.execute()
        self.assertEqual(self.path.lstat().st_nlink, 2)
        self.assertEqual(self.execute()['status'], 'verified')
        self.assertEqual(self.path.lstat().st_nlink, 1)
        self.assertEqual(os.readlink(self.path), '../unit.service')

    def test_no_durable_intent_means_no_scratch_reconciliation(self):
        scratch = self.published_scratch(self.envelope['plan']['phases'][0])
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertTrue(scratch.exists())
        self.assertEqual(self.path.stat().st_nlink, 2)
