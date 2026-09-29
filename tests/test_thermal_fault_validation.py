from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_thermal_fault as validation


class ThermalFaultValidation(unittest.TestCase):
    def test_bad_base_precedes_fixture_or_owner(self):
        with patch.object(validation, 'sha256', return_value='wrong'), \
                patch.object(validation, 'fixture') as fixture, \
                patch.object(validation, 'Controller') as owner:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validation.run('/image', 'expected', '/output')
            fixture.assert_not_called()
            owner.assert_not_called()

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            proof = output / 'thermal-acceptance.json'
            proof.write_text('prior evidence')
            with patch.object(validation, 'sha256', return_value='expected'), \
                    patch.object(validation, 'Controller') as owner:
                with self.assertRaises(FileExistsError):
                    validation.run('/image', 'expected', output)
                owner.assert_not_called()
            self.assertEqual(proof.read_text(), 'prior evidence')

    def test_guest_probe_is_compilable_and_targets_exact_compatible(self):
        source = validation.ENABLE.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        compile(source, 'thermal-enable.py', 'exec')
        self.assertIn("b'x-powers,axp221'", source)
        self.assertIn('assert len(devices) == 1', source)
