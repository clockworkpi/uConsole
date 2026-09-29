from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from build_recovery_initramfs import CREDENTIALS, BOOT_FILESYSTEM_MODULES, build, read_credentials, python_runtime_hook, hook


class RecoveryBuilderTests(unittest.TestCase):
    def test_boot_filesystem_dependencies_are_explicit_not_automounted(self):
        script = hook(Path('/fixture'))
        self.assertEqual(BOOT_FILESYSTEM_MODULES, ('vfat', 'nls_cp437', 'nls_ascii'))
        for name in BOOT_FILESYSTEM_MODULES:
            self.assertIn('manual_add_modules '+name+'\n', script)
        self.assertNotIn('mount -t vfat', script)

    def test_dynamic_firmware_vendor_plugins_are_bundled_when_available(self):
        script = hook(Path('/fixture'))
        self.assertIn('for vendor in brcmfmac_wcc brcmfmac_cyw brcmfmac_bca', script)
        self.assertIn('modinfo -k "$version" "$vendor"', script)
        self.assertIn('manual_add_modules "$vendor"', script)

    def test_python_runtime_excludes_customization_and_copies_extension_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root/'encodings').mkdir()
            (root/'encodings/__init__.py').write_text('fixture')
            (root/'lib-dynload').mkdir()
            (root/'lib-dynload/_ctypes.so').write_bytes(b'fixture')
            (root/'site-packages').mkdir()
            (root/'site-packages/private.py').write_text('not bundled')
            (root/'sitecustomize.py').symlink_to('/nonexistent-customization')
            commands = python_runtime_hook(root)
            self.assertIn('copy_exec /usr/bin/python3', commands)
            self.assertIn('copy_exec ' + str(root/'lib-dynload/_ctypes.so'), commands)
            self.assertIn(str(root/'encodings/__init__.py'), commands)
            self.assertNotIn('sitecustomize', commands)
            self.assertNotIn('site-packages', commands)

    def test_python_runtime_rejects_external_library_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'stdlib').mkdir()
            (root/'outside.py').write_text('not bundled')
            (root/'stdlib/escape.py').symlink_to(root/'outside.py')
            with self.assertRaises(ValueError):
                python_runtime_hook(root/'stdlib')

    def test_private_inputs_and_no_symlink_or_public_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in CREDENTIALS:
                (root / name).write_bytes(b'fixture')
                (root / name).chmod(0o600)
            self.assertEqual(set(read_credentials(root)), set(CREDENTIALS))
            (root / CREDENTIALS[0]).chmod(0o644)
            with self.assertRaises(ValueError):
                read_credentials(root)
            (root / CREDENTIALS[0]).unlink()
            (root / CREDENTIALS[0]).symlink_to(root / CREDENTIALS[1])
            with self.assertRaises(OSError):
                read_credentials(root)

    def test_invalid_release_does_not_read_credentials_or_build(self):
        with patch('build_recovery_initramfs.read_credentials') as read:
            with self.assertRaises(ValueError):
                build('/unused', '/unused', '../kernel')
            read.assert_not_called()
