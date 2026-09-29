from pathlib import Path
import hashlib
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_forge_roundtrip as roundtrip
from validate_forge_service_restart import fixture_paths
import validate_forge_service_restart as restart


class RoundtripTests(unittest.TestCase):
    def test_service_recheck_rejects_failed_or_changed_inputs_before_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / 'roundtrip.json'
            module = root / 'module.ko'
            module.write_bytes(b'candidate')
            imported = root / 'reimported'
            imported.mkdir()
            (imported / 'base.img').write_bytes(b'changed base')
            valid_module = hashlib.sha256(b'candidate').hexdigest()
            for validation, module_hash, message in (
                    ('failed', valid_module, 'completed, passing'),
                    ('passed', '0' * 64, 'Candidate differs'),
                    ('passed', valid_module, 'Reimported base differs')):
                report.write_text(json.dumps({'validation': validation, 'identity': 'a' * 32,
                                             'module_sha256': module_hash,
                                             'export_sha256': '0' * 64}))
                argv = ['restart', '--roundtrip', str(report), '--module', str(module)]
                with patch.object(sys, 'argv', argv), patch.object(restart, 'Runtime') as runtime, \
                     self.assertRaisesRegex(ValueError, message):
                    restart.main()
                runtime.assert_not_called()
            self.assertEqual((imported / 'base.img').read_bytes(), b'changed base')

    def test_service_recheck_paths_require_exact_fixture_identity(self):
        token = 'a' * 32
        paths = fixture_paths(token)
        self.assertEqual(paths['state'], '/var/lib/uconsole-forge-proof-' + token)
        for invalid in (None, '', '../etc', 'a' * 31, 'a' * 33, 'A' * 32,
                        'a' * 31 + ';', 'a' * 32 + '\n'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                fixture_paths(invalid)

    def test_existing_output_is_never_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            existing = output / 'user-data'
            existing.write_text('preserve')
            argv = ['proof', '--image', 'unused.img', '--sha256', '0' * 64,
                    '--module', 'unused.ko', '--output', directory]
            with patch.object(sys, 'argv', argv), \
                 patch.object(roundtrip.shutil, 'disk_usage',
                              return_value=SimpleNamespace(free=64 * 1024 ** 3)), \
                 patch.object(roundtrip.emulator, 'prepare') as prepare, \
                 self.assertRaises(FileExistsError):
                roundtrip.main()
            prepare.assert_not_called()
            self.assertEqual(existing.read_text(), 'preserve')

    def test_native_boot_identity_covers_config_command_line_kernel_and_dtb(self):
        extracted = []
        class Boot:
            def __init__(self, image):
                self.image = image
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def extract(self, name, target):
                extracted.append(name)
                target.write_bytes(name.encode())
        with patch.object(roundtrip, 'BootPartition', Boot):
            first = roundtrip.boot_identity('first.img')
            second = roundtrip.boot_identity('second.img')
        self.assertEqual(first, second)
        self.assertEqual(set(first), {'config.txt', 'cmdline.txt', 'kernel8.img',
                                      'bcm2711-rpi-cm4.dtb'})
        self.assertEqual(len(extracted), 8)
        self.assertTrue(all(len(value) == 64 for value in first.values()))


if __name__ == '__main__':
    unittest.main()
