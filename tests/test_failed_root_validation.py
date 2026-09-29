import base64
import hashlib
import unittest

from validate_failed_root_trial import arguments, check_log


class FailedRootValidationTests(unittest.TestCase):
    nonce = 'a'*32

    def recipe(self, extra=''):
        command = ('console=tty1 root=/dev/ram0 noinitrd panic=0 '
                   'init=/forge-intentional-failure rdinit=/forge-intentional-failure '
                   'uconsole.forge_trial=' + self.nonce + extra).encode()
        return dict(kind='alternate-firmware-failed-root-trial', nonce=self.nonce, files=[dict(
            path='/boot/firmware/forge-trial-cmdline.txt', data=base64.b64encode(command).decode(),
            size=len(command), sha256=hashlib.sha256(command).hexdigest())])

    def test_exact_failure_arguments_and_console_only_adaptation(self):
        command = arguments(self.recipe())
        self.assertNotIn('console=tty1', command)
        self.assertIn('console=ttyAMA1,115200', command)
        self.assertIn('root=/dev/ram0', command)

    def test_rejects_conflicting_arguments_or_tampered_recipe(self):
        for extra in (' root=/dev/mmcblk0p2', ' panic=10', ' resume=/dev/mmcblk0p2', ' rootwait',
                      ' initrd=private.img', ' noinitrd'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                arguments(self.recipe(extra))
        recipe = self.recipe()
        recipe['files'][0]['sha256'] = '0'*64
        with self.assertRaises(ValueError):
            arguments(recipe)

    def test_requires_nonce_ram_panic_and_no_init_mount_or_reboot(self):
        text = ('Kernel command line: uconsole.forge_trial=' + self.nonce + '\n'
                'Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(1,0)\n')
        check_log(text, self.nonce)
        check_log(text.replace('on unknown-block', 'on "/dev/ram0" or unknown-block'), self.nonce)
        for bad in (text.replace(self.nonce, 'b'*32), text.replace('(1,0)', '(179,2)'),
                    text.replace('on unknown-block', 'on "/dev/mmcblk0p2" or unknown-block'),
                    text + 'reboot: Restarting system', text + 'Run /init as init process',
                    text + 'Mounted root (ext4 filesystem)'):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                check_log(bad, self.nonce)
