import base64
import copy
import hashlib
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import forge_boot_privacy_restore as restore
from forge_boot_privacy import prepare_policy
from forge_recovery_bootplan import digest
from forge_target_journal import private_directory, append_event
import forge_target_ssh as target


class PrivacyRestoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.journal = self.root/'transaction'
        data = b'PARTUUID=1234-01 /boot/firmware vfat defaults 0 2\n'
        record = dict(path='/etc/fstab', kind='file', data=base64.b64encode(data).decode(),
            size=len(data), sha256=hashlib.sha256(data).hexdigest(), uid=0, gid=0,
            mode=0o644, atime_ns=12, mtime_ns=14, xattrs={})
        backup = dict(schema=1, machine_id='a'*32, files=[record])
        prepare_policy(self.journal, 'fixture', backup, 'PARTUUID=1234-01')
        self.plan = json.loads((self.journal/'plan.json').read_text())
        self.pin = digest(self.plan)
        self.boot = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        self.clean = dict(staging=str(self.root/'stage'), staging_sha256='b'*64, boot_id=self.boot,
            host='fixture', machine_id='a'*32, image_removed=True, completed_phases=list(reversed(restore.PHASES)),
            image_plan=dict(fstab_sha256=self.plan['after']['files'][0]['sha256']))
        fd = private_directory(self.journal)
        try:
            intent = dict(state='dispatch', direction='apply', nonce='c'*32)
            append_event(fd, intent)
            append_event(fd, dict(intent, state='acknowledged', result=self.ack('apply', 'c'*32)))
        finally: os.close(fd)
        p = patch.object(restore, 'cleanup_inputs', return_value=self.clean)
        p.start()
        self.addCleanup(p.stop)
        self.frozen = self.inputs()

    def ack(self, direction, nonce):
        return dict(direction=direction, nonce=nonce, machine_id='a'*32,
                    files=[dict(path='/etc/fstab', status='applied')])

    def inputs(self):
        return restore.inputs(self.clean['staging'], self.clean['staging_sha256'], self.boot, self.journal, self.pin)

    def response(self, frozen, nonce):
        return dict(acknowledgement=self.ack('restore', nonce), inventory=[], boot_id=self.boot)

    def test_exact_original_journal_restored_once_without_reboot(self):
        with patch.object(restore, 'transport', side_effect=self.response) as remote:
            result = restore.restore(self.frozen)
            with self.assertRaisesRegex(ValueError, 'never replay'): restore.restore(self.frozen)
        remote.assert_called_once()
        self.assertTrue(result['private_mount_retained'])
        self.assertFalse(result['original_mount_verified'])
        self.assertFalse(result['reboot_performed'])
        self.assertTrue((self.journal/'privacy-restore-acceptance.json').exists())

    def test_uncertainty_preserves_attempt_and_prevents_repeat(self):
        with patch.object(restore, 'transport', side_effect=TimeoutError) as remote:
            with self.assertRaises(TimeoutError): restore.restore(self.frozen)
            with self.assertRaises(ValueError): restore.restore(self.frozen)
        remote.assert_called_once()
        self.assertTrue((self.journal/'privacy-restore-attempt.json').exists())
        self.assertFalse((self.journal/'privacy-restore-acceptance.json').exists())

    def test_cleanup_required_before_any_privacy_attempt(self):
        self.clean['image_removed'] = False
        with self.assertRaisesRegex(ValueError, 'removal first'): self.inputs()
        self.assertFalse((self.journal/'privacy-restore-attempt.json').exists())

    def test_wrong_private_policy_or_target_refused(self):
        for key, value in (('host', 'other'), ('machine_id', 'f'*32)):
            wrong = dict(self.clean, **{key: value})
            with self.assertRaises(ValueError): restore.validate_privacy(self.plan, self.pin, wrong)
        wrong = copy.deepcopy(self.clean)
        wrong['image_plan']['fstab_sha256'] = 'f'*64
        with self.assertRaises(ValueError): restore.validate_privacy(self.plan, self.pin, wrong)

    def test_bundle_is_self_contained_and_transport_stdin_explicit(self):
        with patch.object(restore.subprocess, 'run') as remote:
            remote.return_value.stdout = '{}'
            restore.transport(self.frozen, 'd'*32)
        script = restore.BOOTSTRAP.split('from forge_boot_observation import read_boot')[0]+'print("loaded")\n'
        result = subprocess.run([sys.executable, '-I', '-S', '-c', script],
            input=remote.call_args.kwargs['input'], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'loaded')

    def test_target_guard_executes_under_lock_before_and_after_effect(self):
        from contextlib import contextmanager
        calls = []
        @contextmanager
        def lock(path):
            calls.append('lock'); yield; calls.append('unlock')
        def effect(*args, **kwargs):
            calls.append('write')
            return dict(path='/etc/fstab', status='applied')
        with patch.object(target.Path, 'read_text', return_value='a'*32), \
                patch.object(target, 'target_lock', lock), patch.object(target, 'apply_file', effect):
            target.perform(self.plan, 'restore', 'd'*32, guard=lambda: calls.append('guard'))
        self.assertEqual(calls, ['lock', 'guard', 'write', 'guard', 'unlock'])

    def test_failed_inventory_guard_prevents_fstab_write(self):
        def guard(): raise ValueError('leftover artifact')
        with patch.object(target.Path, 'read_text', return_value='a'*32), patch.object(target, 'apply_file') as effect:
            with self.assertRaisesRegex(ValueError, 'leftover artifact'):
                target.perform(self.plan, 'restore', 'd'*32, lock_path=str(self.root/'lock'), guard=guard)
        effect.assert_not_called()

    def test_installed_worker_checks_boot_preimages_mount_and_inventory(self):
        from contextlib import nullcontext, redirect_stdout
        boot = dict(machine_id='a'*32, boot_id=self.boot, cmdline='root=PARTUUID=1234-02 rw', tryboot=0, partition=1)
        desired = self.plan['before']['files'][0]
        clean = dict(self.clean, original_boot=boot, before={'files': [desired]},
                     image_plan=dict(boot_source='/dev/mmcblk0p1', sha256='f'*64))
        mount = dict(mounts={'filesystems': [dict(source='/dev/mmcblk0p1', fstype='vfat', options='fmask=0077,dmask=0077')]},
                     metadata={p: dict(uid=0, gid=0, mode=0o700) for p in ('/boot/firmware', '/boot/firmware/config.txt')},
                     probe_exit=13)
        script = 'from forge_boot_observation import read_boot'+restore.BOOTSTRAP.split('from forge_boot_observation import read_boot', 1)[1]
        for failure in ('none', 'boot', 'preimage', 'mount', 'inventory', 'inventory-drift'):
            with self.subTest(failure=failure):
                actual_boot = dict(boot, boot_id='bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee') if failure == 'boot' else boot
                actual_mount = dict(mount, probe_exit=0) if failure == 'mount' else mount
                snapshot = dict(desired, mode=0o600) if failure == 'preimage' else desired
                inventories = ValueError('artifact remains') if failure == 'inventory' else ([[], [{'path': 'changed'}]] if failure == 'inventory-drift' else [[], []])
                with patch('forge_boot_observation.read_boot', return_value=actual_boot), \
                        patch('forge_target_files.parent_fd', return_value=nullcontext((1, 'fixture'))), \
                        patch('forge_target_files.snapshot', return_value=snapshot), \
                        patch('forge_boot_artifacts.inventory', side_effect=inventories), \
                        patch.object(target.Path, 'read_text', return_value='a'*32), \
                        patch.object(target, 'target_lock', return_value=nullcontext()), \
                        patch.object(target, 'apply_file', return_value=dict(path='/etc/fstab', status='applied')) as effect, \
                        patch.object(subprocess, 'check_output', return_value=json.dumps(actual_mount)), redirect_stdout(io.StringIO()):
                    namespace = dict(r=dict(clean=clean, plan=self.plan, nonce='e'*32), json=json, subprocess=subprocess, sys=sys)
                    if failure == 'none': exec(script, namespace)
                    else:
                        with self.assertRaises(ValueError): exec(script, namespace)
                    self.assertEqual(effect.call_count, 1 if failure in ('none', 'inventory-drift') else 0)
