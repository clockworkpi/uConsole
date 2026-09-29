import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from forge_tryboot_recipe import compile_recipe, compile_recovery_recipe, prepare_staging, PATHS, CONFIG, CMDLINE, TRYBOOT, TRIAL_CMDLINE
from forge_tryboot_recipe import compile_watchdog_trial, prepare_watchdog_staging


class TrybootRecipeTests(unittest.TestCase):
    def test_watchdog_trial_keeps_native_root_and_staging_order(self):
        backup = self.fixture()
        before = copy.deepcopy(backup)
        result = compile_watchdog_trial(backup, 'e'*32)
        config, command = [base64.b64decode(f['data']).decode() for f in result['files']]
        self.assertTrue(config.endswith('kernel_watchdog_timeout=120\n'))
        self.assertIn('[all]\ncmdline=forge-trial-cmdline.txt\ndtparam=watchdog=on\n', config)
        self.assertIn('root=PARTUUID=1234-02', command)
        self.assertFalse(result['failed_boot_fallback_qualified'])
        self.assertEqual(backup, before)
        with tempfile.TemporaryDirectory() as directory:
            review = prepare_watchdog_staging(Path(directory)/'trial', 'clockworkpi.local', backup, 'e'*32)
            self.assertEqual(review['apply_order'], ['command', 'selector'])
            self.assertEqual(review['restore_order'], ['selector', 'command'])

    def test_watchdog_trial_rejects_unsafe_timeout_and_existing_override(self):
        for timeout in (0, 15, 301, True, '120'):
            with self.assertRaises(ValueError):
                compile_watchdog_trial(self.fixture(), 'e'*32, timeout)
        for index, data in ((0, b'kernel_watchdog_timeout=90'), (0, b'kernel_watchdog_partition=2'),
                            (1, b'root=x watchdog.open_timeout=30')):
            backup = self.fixture()
            self.replace(backup, index, data)
            with self.assertRaises(ValueError):
                compile_watchdog_trial(backup, 'e'*32)

    def image_plan(self):
        return dict(schema=2, kind='private-recovery-image', host='clockworkpi.local',
                    machine_id='a' * 32, fstab_sha256='b' * 64, boot_source='/dev/mmcblk0p1',
                    source='/private/recovery.img', destination='/boot/firmware/forge-recovery-'+'c'*32+'.img',
                    sha256='d' * 64, size=123, stage_token='c'*32, preimage={'kind': 'absent'})

    def replace(self, backup, index, data):
        backup['files'][index].update(data=base64.b64encode(data).decode(),
                                    size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def test_recovery_recipe_preserves_normal_boot_and_selects_exact_image(self):
        backup = self.fixture()
        self.replace(backup, 0, b'[all]\nauto_initramfs=1\n[pi4]\ninitramfs old.img followkernel\nkernel=kernel8.img\n')
        before = copy.deepcopy(backup)
        plan = self.image_plan()
        result = compile_recovery_recipe(backup, 'e' * 32, plan)
        self.assertEqual(backup, before)
        config, command = [base64.b64decode(f['data']).decode() for f in result['files']]
        self.assertIn('kernel=kernel8.img', config)
        self.assertNotIn('old.img', config)
        self.assertNotIn('auto_initramfs=1', config)
        self.assertTrue(config.endswith('auto_initramfs=0\ninitramfs '+Path(plan['destination']).name+' followkernel\n'))
        self.assertNotIn('root=PARTUUID', command)
        self.assertNotIn('rootwait', command)
        self.assertIn('root=/dev/ram0 rdinit=/init uconsole.recovery=1', command)
        self.assertNotIn('uconsole.emulator=', command)
        self.assertEqual(result['image_dependency']['sha256'], plan['sha256'])
        self.assertFalse(result['deployment_authorized'])
        self.assertFalse(result['recovery_qualified'])

    def test_recovery_refuses_wrong_target_legacy_plan_and_ramfs_indirection(self):
        for change in (dict(machine_id='f' * 32), dict(schema=1)):
            with self.assertRaises(ValueError):
                compile_recovery_recipe(self.fixture(), 'e'*32, dict(self.image_plan(), **change))
        for directive in (b'ramfsfile=other.img', b'ramfsaddr=0x1000'):
            backup = self.fixture()
            self.replace(backup, 0, directive)
            with self.assertRaises(ValueError):
                compile_recovery_recipe(backup, 'e'*32, self.image_plan())
        for token in (b'noinitrd', b'initrd=other', b'uconsole.recovery=1'):
            backup = self.fixture()
            self.replace(backup, 1, b'root=x ' + token)
            with self.assertRaises(ValueError):
                compile_recovery_recipe(backup, 'e'*32, self.image_plan())

    def test_staging_orders_dependencies_and_retains_every_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'trial'
            review = prepare_staging(root, 'clockworkpi.local', self.fixture(), 'b' * 32)
            plans = [json.loads((root / p / 'plan.json').read_text()) for p in ('command', 'selector')]
            self.assertEqual(review['restore_order'], ['selector', 'command'])
            for plan, target in zip(plans, (TRIAL_CMDLINE, TRYBOOT)):
                self.assertEqual(plan['before']['files'][-1]['path'], target)
                self.assertEqual(plan['before']['files'][-1]['kind'], 'absent')
                self.assertEqual(plan['after']['files'][-1]['kind'], 'file')
                self.assertEqual(set(f['path'] for f in plan['before']['files']), set(PATHS))
                self.assertEqual(plan['before']['files'][:-1], plan['after']['files'][:-1])
            states = lambda p: {f['path']: f for f in p}
            self.assertEqual(states(plans[0]['after']['files']), states(plans[1]['before']['files']))
            self.assertEqual((root / 'review.json').stat().st_mode & 0o777, 0o600)

    def fixture(self):
        records = []
        for path in PATHS:
            if path in (CONFIG, CMDLINE):
                data = (b'[pi4]\ndtoverlay=clockworkpi-uconsole\n' if path == CONFIG
                        else b'console=tty1 root=PARTUUID=1234-02 rootwait quiet\n')
                records.append(dict(path=path, kind='file', size=len(data),
                                    data=base64.b64encode(data).decode(),
                                    sha256=hashlib.sha256(data).hexdigest(), mode=0o755,
                                    uid=0, gid=0, atime_ns=1, mtime_ns=1, xattrs={}))
            else:
                records.append(dict(path=path, kind='absent'))
        return dict(schema=1, machine_id='a' * 32, files=records)

    def test_private_staging_preserves_permissions_and_rejects_unqualified_metadata(self):
        for mode in (0o700, 0o755, 0o777, 0o750, 0o4700):
            backup = self.fixture()
            backup['files'][0]['mode'] = mode
            with self.subTest(mode=oct(mode)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)/'trial'
                if mode not in (0o700, 0o755):
                    with self.assertRaises(ValueError):
                        prepare_staging(root, 'clockworkpi.local', backup, 'b'*32)
                    self.assertFalse(root.exists())
                    continue
                review = prepare_staging(root, 'clockworkpi.local', backup, 'b'*32)
                for phase in review['phases']:
                    plan = json.loads((Path(phase['journal'])/'plan.json').read_text())
                    self.assertEqual(plan['after']['files'][-1]['mode'], mode)

    def test_preserves_native_boot_and_does_not_deploy(self):
        backup = self.fixture()
        before = copy.deepcopy(backup)
        recipe = compile_recipe(backup, 'b' * 32)
        self.assertEqual(backup, before)
        self.assertFalse(recipe['deployment_performed'])
        self.assertFalse(recipe['recovery_qualified'])
        config, cmdline = [base64.b64decode(f['data']).decode() for f in recipe['files']]
        self.assertTrue(config.endswith('[all]\ncmdline=forge-trial-cmdline.txt\n'))
        self.assertIn('root=PARTUUID=1234-02', cmdline)
        self.assertIn('uconsole.forge_trial=' + 'b' * 32, cmdline)
        self.assertEqual(len(recipe['restore']), 2)

    def test_rejects_existing_trial_and_invalid_nonce(self):
        with self.assertRaises(ValueError):
            compile_recipe(self.fixture(), 'reboot now')
        backup = self.fixture()
        backup['files'][2] = dict(backup['files'][0], path=PATHS[2])
        with self.assertRaises(ValueError):
            compile_recipe(backup, 'b' * 32)

    def test_rejects_indirection_and_unsafe_command_lines(self):
        for index, data in ((0, b'include other.txt\n'), (0, b'os_prefix=test/\n'),
                            (1, b'root=x init=/bin/bash'), (1, b'root=x root=y'),
                            (1, b'root=x uconsole.emulator=1'), (1, b'root=x\nquiet')):
            with self.subTest(data=data):
                backup = self.fixture()
                backup['files'][index].update(data=base64.b64encode(data).decode(),
                                             size=len(data), sha256=hashlib.sha256(data).hexdigest())
                with self.assertRaises(ValueError):
                    compile_recipe(backup, 'b' * 32)
