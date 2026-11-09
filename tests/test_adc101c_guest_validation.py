import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_adc101c_guest as validator
try:
    import validate_adc101c_gui as gui_validator
except ImportError as error:
    if error.name != 'tkinter':
        raise
    gui_validator = None


class ADCGuestValidationTests(unittest.TestCase):
    def test_unknown_profile_cannot_create_a_fixture(self):
        with mock.patch.object(validator, 'fixture') as fixture:
            with self.assertRaisesRegex(ValueError, 'Unknown reference profile'):
                validator.run('/missing', '0' * 64, '/missing-output', reference='typo')
            fixture.assert_not_called()

    def test_bad_base_hash_blocks_fixture_and_boot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / 'image'
            image.write_bytes(b'base')
            with mock.patch.object(validator, 'fixture') as fixture, \
                    mock.patch.object(validator, 'Runtime') as runtime:
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    validator.run(image, '0' * 64, root / 'output')
                fixture.assert_not_called()
                runtime.assert_not_called()

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / 'image'
            image.write_bytes(b'base')
            with mock.patch.object(validator, 'Runtime') as runtime:
                with self.assertRaises(FileExistsError):
                    validator.run(image, hashlib.sha256(b'base').hexdigest(), root)
                runtime.assert_not_called()
            self.assertEqual(image.read_bytes(), b'base')

    def test_guest_reader_is_valid_python_and_uses_exact_compatible(self):
        compile(validator.READER, '<guest-reader>', 'exec')
        self.assertIn("b'ti,adc101c'", validator.READER)
        self.assertNotIn('i2cdetect', validator.READER)


@unittest.skipIf(gui_validator is None, 'Tkinter is unavailable')
class ADCGUIValidationTests(unittest.TestCase):
    def test_preflight_does_not_launch_tk_or_overwrite_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / 'image'
            image.write_bytes(b'base')
            with mock.patch.object(gui_validator.tk, 'Tk') as tk:
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    gui_validator.run(image, '0' * 64, root / 'output')
                self.assertFalse((root / 'output').exists())
                with self.assertRaises(FileExistsError):
                    gui_validator.run(image, hashlib.sha256(b'base').hexdigest(), root)
                tk.assert_not_called()
            self.assertEqual(image.read_bytes(), b'base')
