import copy
from contextlib import nullcontext
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import forge_boot_mount_restore as mount
import forge_boot_mount_worker as worker
import test_boot_privacy_restore


def observation(private):
    return dict(mounts={'filesystems': [dict(source='/dev/mmcblk0p1', fstype='vfat',
                options='fmask=0077,dmask=0077' if private else 'fmask=0022,dmask=0022')]},
                metadata={p: dict(uid=0, gid=0, mode=0o700 if private else 0o755)
                          for p in ('/boot/firmware', '/boot/firmware/config.txt')}, probe_exit=13 if private else 0)


class OriginalMountTests(unittest.TestCase):
    def setUp(self):
        self.f = test_boot_privacy_restore.PrivacyRestoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.old = dict(machine_id='a'*32, boot_id=self.f.boot, cmdline='root=PARTUUID=1234-02 rw', tryboot=0, partition=1)
        self.new = dict(self.old, boot_id='bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee')
        self.f.clean.update(original_boot=dict(self.old, boot_id='cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee'), before={'files': []})
        self.f.clean['image_plan'].update(boot_source='/dev/mmcblk0p1', sha256='f'*64)
        self.f.frozen = self.f.inputs()
        with patch('forge_boot_privacy_restore.transport', side_effect=self.f.response):
            test_boot_privacy_restore.restore.restore(self.f.frozen)
        p = patch.object(mount, 'cleanup_inputs', return_value=self.f.clean)
        p.start(); self.addCleanup(p.stop)
        self.frozen = mount.inputs(self.f.journal, self.f.pin)
        self.observed = dict(boot=self.new, mount=observation(False), inventory=[],
            verification=dict(status='verified-original', source='/dev/mmcblk0p1'),
            mutation_performed=False, reboot_requested=False)

    def test_one_reboot_then_verified_original_permissions(self):
        with patch.object(mount, 'transport', side_effect=[subprocess.TimeoutExpired('ssh', 300), self.observed]) as remote:
            result = mount.verify(self.frozen, reboot=True)
            with self.assertRaises(FileExistsError): mount.verify(self.frozen, reboot=True)
        self.assertEqual([c.args[1] for c in remote.call_args_list], ['reboot', 'inspect'])
        self.assertTrue(result['original_mount_verified'])
        self.assertFalse(result['filesystem_health_qualified'])

    def test_failed_reboot_can_be_verified_read_only_without_replay(self):
        with patch.object(mount, 'transport', side_effect=TimeoutError), \
                patch.object(mount.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaises(RuntimeError): mount.verify(self.frozen, reboot=True, timeout=1)
        failed = (self.f.journal/'original-mount-reboot/failure.json').read_bytes()
        with patch.object(mount, 'transport', return_value=self.observed) as remote:
            result = mount.verify(self.frozen)
        self.assertFalse(result['reboot_request_dispatched'])
        remote.assert_called_once_with(self.frozen, 'inspect')
        self.assertEqual((self.f.journal/'original-mount-reboot/failure.json').read_bytes(), failed)

    def test_old_boot_private_mount_inventory_or_mutation_claim_rejected(self):
        cases = [dict(self.observed, boot=self.old), dict(self.observed, mount=observation(True)),
                 dict(self.observed, inventory=[{}]), dict(self.observed, mutation_performed=0)]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError): mount.checked(value, self.frozen)

    def test_receipt_tampering_cannot_enable_reboot(self):
        path = self.f.journal/'privacy-restore-acceptance.json'
        value = json.loads(path.read_text()); value['original_mount_verified'] = True
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'receipt differs'): mount.inputs(self.f.journal, self.f.pin)

    def test_fixed_bundle_and_explicit_stdin(self):
        with patch.object(mount.subprocess, 'run') as remote:
            remote.return_value.stdout = '{}'
            mount.transport(self.frozen, 'inspect')
        script = mount.BOOTSTRAP.split('from forge_boot_mount_worker import run')[0]+'print("loaded")\n'
        result = subprocess.run([sys.executable, '-I', '-S', '-c', script],
            input=remote.call_args.kwargs['input'], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_reboot_is_guarded_and_inspection_never_reboots(self):
        for operation, boot, private in (('reboot', self.old, True), ('inspect', self.new, False)):
            for fault in ('none', 'boot', 'file', 'inventory', 'mount'):
                with self.subTest(operation=operation, fault=fault):
                    actual_boot = self.new if fault == 'boot' and operation == 'reboot' else self.old if fault == 'boot' else boot
                    old_file = self.f.plan['before']['files'][0]
                    actual_file = dict(old_file, mode=0o600) if fault == 'file' else old_file
                    with patch.object(worker.os, 'geteuid', return_value=0), \
                            patch.object(worker, 'read_boot', return_value=actual_boot), \
                            patch.object(worker, 'target_lock', return_value=nullcontext()), \
                            patch.object(worker, 'parent_fd', return_value=nullcontext((1, 'fstab'))), \
                            patch.object(worker, 'snapshot', return_value=actual_file), \
                            patch.object(worker, 'inventory', return_value=[{}] if fault == 'inventory' else []), \
                            patch.object(worker.subprocess, 'check_output', return_value=json.dumps(observation(not private if fault == 'mount' else private))), \
                            patch.object(worker.subprocess, 'run') as reboot:
                        request = dict(frozen=self.frozen, operation=operation)
                        if fault != 'none':
                            with self.assertRaises(ValueError): worker.run(request)
                        else: worker.run(request)
                        self.assertEqual(reboot.call_count, int(operation == 'reboot' and fault == 'none'))
                        if reboot.called:
                            self.assertEqual(reboot.call_args.args[0], ['/usr/bin/systemctl', 'reboot'])
                            self.assertIs(reboot.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_public_fat_mode_change_is_expected_not_content_change(self):
        frozen = copy.deepcopy(self.frozen)
        config = dict(self.f.plan['before']['files'][0], path='/boot/firmware/config.txt', mode=0o700)
        frozen['cleanup']['before']['files'] = [config]
        current = {config['path']: dict(config, mode=0o755), '/etc/fstab': self.f.plan['before']['files'][0]}
        with patch.object(worker.os, 'geteuid', return_value=0), patch.object(worker, 'read_boot', return_value=self.new), \
                patch.object(worker, 'target_lock', return_value=nullcontext()), \
                patch.object(worker, 'parent_fd', side_effect=lambda path: nullcontext((1, path))), \
                patch.object(worker, 'snapshot', side_effect=lambda fd, name, path: current[path]), \
                patch.object(worker, 'inventory', return_value=[]), \
                patch.object(worker.subprocess, 'check_output', return_value=json.dumps(observation(False))):
            result = worker.run(dict(frozen=frozen, operation='inspect'))
        self.assertFalse(result['mutation_performed'])
