from contextlib import nullcontext, contextmanager
import hashlib
import json
import unittest
import tempfile
from unittest.mock import patch

from forge_recovery_ssh import perform


class RecoveryTransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch('forge_recovery_ssh.provision', return_value=directory.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plan = dict(schema=2, kind='private-recovery-image', host='clockworkpi.local',
                         machine_id='a'*32, fstab_sha256='b'*64, boot_source='/dev/mmcblk0p1',
                         source='/private/recovery.img', destination='/boot/firmware/forge-recovery-'+'c'*32+'.img',
                         sha256='d'*64, size=123, stage_token='c'*32, preimage={'kind':'absent'})
        self.digest = hashlib.sha256((json.dumps(self.plan, sort_keys=True, indent=2)+'\n').encode()).hexdigest()

    def test_policy_failure_prevents_all_image_mutation(self):
        with patch('forge_recovery_ssh.target_lock', return_value=nullcontext()), \
                patch('forge_recovery_ssh.target_policy', side_effect=ValueError('public mount')), \
                patch('forge_recovery_ssh.publish') as publish, patch('forge_recovery_ssh.remove') as remove:
            for direction in ('apply', 'restore'):
                with self.assertRaises(ValueError):
                    perform(self.plan, direction, 'e'*32, self.digest)
            publish.assert_not_called()
            remove.assert_not_called()

    def test_effect_is_bracketed_by_policy_checks(self):
        with patch('forge_recovery_ssh.target_lock', return_value=nullcontext()), \
                patch('forge_recovery_ssh.target_policy') as policy, \
                patch('forge_recovery_ssh.publish', return_value=dict(status='published',sha256='d'*64,size=123)):
            result = perform(self.plan, 'apply', 'e'*32, self.digest)
        self.assertEqual(policy.call_count, 2)
        self.assertEqual(result['plan_sha256'], self.digest)

    def test_post_effect_policy_drift_never_acknowledged(self):
        with patch('forge_recovery_ssh.target_lock', return_value=nullcontext()), \
                patch('forge_recovery_ssh.target_policy', side_effect=[None, ValueError('changed')]), \
                patch('forge_recovery_ssh.publish', return_value=dict(status='published',sha256='d'*64,size=123)) as publish:
            with self.assertRaises(ValueError):
                perform(self.plan, 'apply', 'e'*32, self.digest)
            publish.assert_called_once()

    def test_wrong_plan_digest_fails_before_target_lock(self):
        with patch('forge_recovery_ssh.target_lock') as lock:
            with self.assertRaises(ValueError):
                perform(self.plan, 'apply', 'e'*32, 'f'*64)
            lock.assert_not_called()

    def test_owner_guard_brackets_removal_inside_target_lock(self):
        calls = []
        @contextmanager
        def lock(path):
            calls.append('lock')
            yield
            calls.append('unlock')
        def remove(*args):
            calls.append('remove')
            return dict(status='removed', sha256='d'*64, size=123)
        with patch('forge_recovery_ssh.target_lock', lock), \
                patch('forge_recovery_ssh.target_policy'), patch('forge_recovery_ssh.remove', remove):
            perform(self.plan, 'restore', 'e'*32, self.digest, guard=lambda: calls.append('guard'))
        self.assertEqual(calls, ['lock', 'guard', 'remove', 'guard', 'unlock'])

    def test_failed_owner_guard_prevents_removal(self):
        def guard(): raise ValueError('boot files changed')
        with patch('forge_recovery_ssh.target_lock', return_value=nullcontext()), \
                patch('forge_recovery_ssh.remove') as remove:
            with self.assertRaisesRegex(ValueError, 'boot files changed'):
                perform(self.plan, 'restore', 'e'*32, self.digest, guard=guard)
        remove.assert_not_called()

    def test_fencing_does_not_require_private_policy_or_mutate_image(self):
        with patch('forge_recovery_ssh.target_lock', return_value=nullcontext()), \
                patch('forge_recovery_ssh.os.geteuid', return_value=0), \
                patch('forge_recovery_ssh.Path.read_text', return_value='a'*32), \
                patch('forge_recovery_ssh.target_policy') as policy, \
                patch('forge_recovery_ssh.publish') as publish, patch('forge_recovery_ssh.remove') as remove:
            result = perform(self.plan, 'fence-apply', 'e'*32, self.digest)
            self.assertEqual(result['outcome']['status'], 'fenced-not-started')
            policy.assert_not_called()
            publish.assert_not_called()
            remove.assert_not_called()
