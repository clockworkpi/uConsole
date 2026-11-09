from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_installed_capture as validation


class InstalledCaptureGuards(unittest.TestCase):
    def test_appkit_option_requires_macos_before_fixture(self):
        with patch.object(sys, 'platform', 'linux'), patch.object(validation, 'fixture') as fixture:
            with self.assertRaisesRegex(ValueError, 'only supported on macOS'):
                validation.run('/archive', '/base', 'expected', '/output', ignore_saved_state=True)
            fixture.assert_not_called()

    def test_candidate_module_requires_mode_and_matching_pin_before_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / 'candidate.ko'
            module.write_bytes(b'fixture')
            cases = ({'module': module}, {'module_sha256': '0' * 64},
                     {'module': module, 'module_sha256': '0' * 64},
                     {'duplex': True, 'module': module, 'module_sha256': '0' * 64})
            with patch.object(validation, 'fixture') as fixture:
                for options in cases:
                    with self.subTest(options=options), self.assertRaises(ValueError):
                        validation.run('/archive', '/base', 'expected', root / 'output', **options)
                fixture.assert_not_called()
            self.assertFalse((root / 'output').exists())

    def test_bad_base_does_not_create_fixture(self):
        with patch.object(validation, 'sha256', return_value='changed'), \
                patch.object(validation, 'fixture') as fixture:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validation.run('/archive', '/base', 'expected', '/output')
            fixture.assert_not_called()

    def test_missing_archive_precedes_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(validation, 'sha256', side_effect=['expected', FileNotFoundError('archive')]), \
                    patch.object(validation.shutil, 'disk_usage', return_value=MagicMock(free=1024**3)), \
                    patch.object(validation, 'fixture') as fixture:
                with self.assertRaises(FileNotFoundError):
                    validation.run(root / 'archive', root / 'base', 'expected', root / 'output')
                fixture.assert_not_called()

    def test_missing_qemu_precedes_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(validation, 'sha256', side_effect=['expected', 'archive-hash', FileNotFoundError('qemu')]), \
                    patch.object(validation.shutil, 'disk_usage', return_value=MagicMock(free=1024**3)), \
                    patch.object(validation, 'fixture') as fixture:
                with self.assertRaises(FileNotFoundError):
                    validation.run(root / 'archive', root / 'base', 'expected', root / 'output')
                fixture.assert_not_called()
