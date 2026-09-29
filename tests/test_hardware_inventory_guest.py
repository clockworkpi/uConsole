import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_hardware_inventory_guest as validator


class GuestInventoryTests(unittest.TestCase):
    def test_invalid_base_or_failed_reference_never_creates_or_boots_guest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, reference = root / 'base.img', root / 'reference.json'
            image.write_bytes(b'fixture')
            reference.write_text(json.dumps({'probes': {'subsystems': {'exit_code': 2}}}))
            for digest, error in (('0' * 64, 'hash mismatch'),
                                  (hashlib.sha256(b'fixture').hexdigest(), 'incomplete')):
                with self.subTest(error=error), \
                        patch.object(sys, 'argv', ['capture', '--image', str(image), '--sha256', digest,
                                                 '--reference', str(reference), '--output', str(root / 'output')]), \
                        patch.object(validator, 'fixture') as fixture, \
                        patch.object(validator, 'Runtime') as runtime:
                    with self.assertRaisesRegex(ValueError, error):
                        validator.main()
                    fixture.assert_not_called()
                    runtime.assert_not_called()
