import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from io import BytesIO

from forge_trial_firmware import paths, prepare, fetch_pair, NATIVE, compile_recovery_recipe, prepare_recovery
from forge_trial_firmware import compile_failed_root_trial
import test_tryboot_recipe


class TrialFirmwareTests(unittest.TestCase):
    def test_lease_is_explicit_bound_read_only_recipe_option(self):
        image = test_tryboot_recipe.TrybootRecipeTests().image_plan()
        owner = 'e'*64
        recipe = compile_recovery_recipe(self.backup, self.nonce, self.bundle, image, lease_owner=owner)
        command = base64.b64decode(recipe['files'][1]['data']).decode().split()
        self.assertEqual(command.count('uconsole.recovery_lease=1'), 1)
        self.assertEqual(command.count('uconsole.recovery_owner='+owner), 1)
        self.assertFalse(recipe['lease']['root_write_authorized'])
        self.assertFalse(recipe['lease']['persistent_hold_authorized'])
        self.assertIn('qualified-renewable-recovery-image', recipe['required_gates'])
        default = compile_recovery_recipe(self.backup, self.nonce, self.bundle, image)
        self.assertNotIn('lease', default)
        for invalid in ('', 'e'*63, 'x'*64, True, owner+' injected=1'):
            with self.assertRaises(ValueError):
                compile_recovery_recipe(self.backup, self.nonce, self.bundle, image, lease_owner=invalid)

    def test_native_preimage_must_not_already_contain_lease_options(self):
        fixture = test_tryboot_recipe.TrybootRecipeTests()
        image = fixture.image_plan()
        for option in ('uconsole.recovery_lease=1', 'uconsole.recovery_owner='+'e'*64):
            backup = copy.deepcopy(self.backup)
            fixture.replace(backup, 1, ('root=PARTUUID=1234-02 '+option).encode())
            with self.assertRaises(ValueError):
                compile_recovery_recipe(backup, self.nonce, self.bundle, image)

    def setUp(self):
        self.nonce = 'c' * 32
        self.backup = test_tryboot_recipe.TrybootRecipeTests().fixture()
        template = self.backup['files'][0]
        self.backup['files'].extend(dict(template, path=p) for p in NATIVE)
        self.backup['files'].extend(dict(path=p, kind='absent') for p in paths(self.nonce)[-2:])
        self.bundle = dict(schema=1, revision='d'*40, files=[])
        for name in ('start4.elf','fixup4.dat'):
            data = ('disposable test ' + name).encode()
            self.bundle['files'].append(dict(name=name,
                url='https://raw.githubusercontent.com/raspberrypi/firmware/'+'d'*40+'/boot/'+name,
                data=base64.b64encode(data).decode(), size=len(data), sha256=hashlib.sha256(data).hexdigest()))

    def test_pair_before_selector_restore_selector_before_pair(self):
        before = copy.deepcopy(self.backup)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'trial'
            review = prepare(root, 'clockworkpi.local', self.backup, self.nonce, self.bundle)
            self.assertEqual(review['apply_order'], ['firmware-start','firmware-fixup','command','selector'])
            self.assertEqual(review['restore_order'], list(reversed(review['apply_order'])))
            for phase in review['apply_order']:
                plan = json.loads((root / phase / 'plan.json').read_text())
                self.assertEqual(len(plan['before']['files']), 9)
                self.assertEqual(plan['before']['files'][:-1], plan['after']['files'][:-1])
                self.assertEqual(plan['before']['files'][-1]['kind'], 'absent')
            config = base64.b64decode(review['files'][0]['data']).decode()
            self.assertIn('start_file=forge-start-'+self.nonce+'.elf', config)
            self.assertIn('fixup_file=forge-fixup-'+self.nonce+'.dat', config)
            self.assertFalse(review['deployment_authorized'])
            self.assertFalse(review['eeprom_modified'])
        self.assertEqual(before, self.backup)

    def test_mutated_pair_or_mixed_revision_rejected_before_preparing(self):
        for key, value in (('sha256','0'*64), ('url','https://example.com/fixup4.dat'), ('size',True)):
            bundle = copy.deepcopy(self.bundle)
            bundle['files'][1][key] = value
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'trial'
                with self.assertRaises(ValueError):
                    prepare(root, 'clockworkpi.local', self.backup, self.nonce, bundle)
                self.assertFalse(root.exists())

    def test_mutable_download_revision_rejected(self):
        for revision in ('master','latest','../other'):
            with self.assertRaises(ValueError):
                fetch_pair(revision)

    def test_failed_root_trial_cannot_mount_native_root_or_software_reboot(self):
        fixture = test_tryboot_recipe.TrybootRecipeTests()
        fixture.replace(self.backup, 0, b'[all]\nauto_initramfs=1\ninitramfs native.img followkernel\n')
        fixture.replace(self.backup, 1, b'root=PARTUUID=1234-02 rootwait rw resume=/dev/mmcblk0p2 panic=10 quiet')
        before = copy.deepcopy(self.backup)
        recipe = compile_failed_root_trial(self.backup, self.nonce, self.bundle)
        config, command = [base64.b64decode(f['data']).decode() for f in recipe['files'][:2]]
        self.assertNotIn('native.img', config)
        self.assertNotIn('auto_initramfs=1', config)
        self.assertIn('auto_initramfs=0', config)
        self.assertIn('kernel_watchdog_timeout=120', config)
        self.assertIn('root=/dev/ram0', command.split())
        self.assertIn('noinitrd', command.split())
        self.assertIn('panic=0', command.split())
        self.assertIn('init=/forge-intentional-failure', command.split())
        for token in command.split():
            self.assertFalse(token.startswith(('resume=', 'root=PARTUUID', 'rootwait')))
        self.assertFalse(recipe['normal_return_alone_is_proof'])
        self.assertFalse(recipe['deployment_authorized'])
        self.assertIn('physical-power-cycle-recovery', recipe['required_gates'])
        self.assertEqual(self.backup, before)

    def test_failed_root_trial_rejects_legacy_ramfs_indirection(self):
        fixture = test_tryboot_recipe.TrybootRecipeTests()
        for directive in (b'ramfsfile=other.img', b'ramfsaddr=1234'):
            fixture.replace(self.backup, 0, directive)
            with self.assertRaises(ValueError):
                compile_failed_root_trial(self.backup, self.nonce, self.bundle)

    def test_private_recovery_staging_retains_gates_and_inverse(self):
        image = test_tryboot_recipe.TrybootRecipeTests().image_plan()
        for record in self.backup['files']:
            if record['kind'] == 'file':
                record['mode'] = 0o700
        before = copy.deepcopy(self.backup)
        with tempfile.TemporaryDirectory() as directory:
            review = prepare_recovery(Path(directory)/'trial', image['host'], self.backup,
                                      self.nonce, self.bundle, image)
            self.assertEqual(review['apply_order'], ['firmware-start', 'firmware-fixup', 'command', 'selector'])
            self.assertEqual(review['restore_order'], list(reversed(review['apply_order'])))
            self.assertIn('independent-boot-fallback', review['required_gates'])
            self.assertFalse(review['deployment_authorized'])
            self.assertEqual(review['image_dependency']['sha256'], image['sha256'])
            for phase in review['phases']:
                plan = json.loads((Path(phase['journal'])/'plan.json').read_text())
                self.assertEqual(len(plan['before']['files']), 9)
                self.assertEqual(plan['before']['files'][:-1], plan['after']['files'][:-1])
                self.assertEqual(plan['after']['files'][-1]['mode'], 0o700)
                self.assertEqual(plan['before']['files'][-1]['kind'], 'absent')
        self.assertEqual(before, self.backup)

    def test_recovery_staging_rejects_public_metadata_or_wrong_host_before_writing(self):
        image = test_tryboot_recipe.TrybootRecipeTests().image_plan()
        for host in (image['host'], 'other.local'):
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory)/'trial'
                with self.assertRaises(ValueError):
                    prepare_recovery(destination, host, self.backup, self.nonce, self.bundle, image)
                self.assertFalse(destination.exists())

    def test_combined_ram_recipe_keeps_image_and_fallback_gates(self):
        before = copy.deepcopy(self.backup)
        image = test_tryboot_recipe.TrybootRecipeTests().image_plan()
        recipe = compile_recovery_recipe(self.backup, self.nonce, self.bundle, image)
        config, command = [base64.b64decode(f['data']).decode() for f in recipe['files'][:2]]
        self.assertIn('initramfs ' + Path(image['destination']).name + ' followkernel', config)
        self.assertIn('auto_initramfs=0', config)
        self.assertIn('kernel_watchdog_timeout=120', config)
        self.assertIn('dtparam=watchdog=on', config)
        self.assertIn('start_file=forge-start-' + self.nonce + '.elf', config)
        self.assertIn('fixup_file=forge-fixup-' + self.nonce + '.dat', config)
        self.assertIn('root=/dev/ram0 rdinit=/init uconsole.recovery=1', command)
        self.assertIn('uconsole.recovery_watchdog=1', command)
        self.assertNotIn('uconsole.emulator=', command)
        self.assertNotIn('root=PARTUUID', command)
        self.assertEqual(recipe['image_dependency']['sha256'], image['sha256'])
        self.assertFalse(recipe['deployment_authorized'])
        self.assertFalse(recipe['failed_boot_fallback_qualified'])
        for gate in ('private-persistent-boot-policy', 'acknowledged-image-publication',
                     'independent-boot-fallback', 'physical-power-cycle-recovery',
                     'recovery-watchdog-ownership'):
            self.assertIn(gate, recipe['required_gates'])
        self.assertEqual(self.backup, before)

    def test_combined_recipe_rejects_wrong_image_identity_and_mutated_firmware(self):
        image = test_tryboot_recipe.TrybootRecipeTests().image_plan()
        with self.assertRaises(ValueError):
            compile_recovery_recipe(self.backup, self.nonce, self.bundle, dict(image, machine_id='f'*32))
        bundle = copy.deepcopy(self.bundle)
        bundle['files'][0]['sha256'] = '0'*64
        with self.assertRaises(ValueError):
            compile_recovery_recipe(self.backup, self.nonce, bundle, image)

    def test_fetch_checks_pinned_git_blob_content(self):
        calls = []
        def response(url, timeout):
            calls.append(url)
            name = 'start4.elf' if len(calls) <= 2 else 'fixup4.dat'
            data = name.encode()
            sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            value = (dict(type='file',path='boot/'+name,size=len(data),sha=sha)
                     if 'contents/' in url else dict(encoding='base64',sha=sha,content=base64.b64encode(data).decode()))
            return BytesIO(json.dumps(value).encode())
        with patch('forge_trial_firmware.urlopen', side_effect=response):
            bundle = fetch_pair('e'*40)
        self.assertEqual(len(bundle['files']), 2)
        self.assertIn('?ref='+'e'*40, calls[0])
        self.assertIn('?ref='+'e'*40, calls[2])

    def test_fetch_rejects_wrong_blob_bytes(self):
        entry = dict(type='file', path='boot/start4.elf', size=4, sha='a'*40)
        blob = dict(encoding='base64',sha='a'*40,content=base64.b64encode(b'bad!').decode())
        with patch('forge_trial_firmware.urlopen', side_effect=[BytesIO(json.dumps(v).encode()) for v in (entry,blob)]):
            with self.assertRaises(ValueError):
                fetch_pair('e'*40)
