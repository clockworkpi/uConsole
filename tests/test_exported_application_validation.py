"""Boot-back validation refuses untrusted fixture metadata before launch."""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from validate_exported_application import main


class ExportApplicationValidationTests(unittest.TestCase):
    def test_failed_output_check_attempts_clean_cleanup_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'files').mkdir()
            (root / 'files/enhanced.img').write_bytes(b'fixture')
            record, output = root / 'record.json', root / 'boot-back'
            record.write_text(json.dumps({'validation': 'passed',
                'application_path': '/usr/local/bin/attached-forge-' + 'a' * 32,
                'application_sha256': '0' * 64,
                'export_sha256': hashlib.sha256(b'fixture').hexdigest()}))
            runtime = MagicMock(identity='fixture-runtime')
            runtime.process.poll.return_value = None
            runtime.execute.return_value = {'exit_code': 0, 'stdout': 'wrong application output'}
            runtime.stop.side_effect = lambda: setattr(runtime.process.poll, 'return_value', 0)
            with patch.object(sys, 'argv', ['verify', '--record', str(record), '--output', str(output)]), \
                    patch('validate_exported_application.fixture', side_effect=lambda *args: output.mkdir(mode=0o700)), \
                    patch('validate_exported_application.Runtime', return_value=runtime), \
                    patch('validate_exported_application.wait_for_log'):
                with self.assertRaisesRegex(ValueError, 'did not match'):
                    main()
            runtime.stop.assert_called_once_with()
            self.assertIn('cancel', runtime.execute.call_args.kwargs)
            evidence = json.loads((output / 'acceptance.json').read_text())
            self.assertEqual(evidence['validation'], 'failed')
            self.assertFalse(evidence['forced_cleanup'])

    def test_bad_fixture_path_or_checksum_never_creates_boot_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'files').mkdir()
            image = root / 'files/enhanced.img'
            image.write_bytes(b'fixture bytes')
            record = root / 'record.json'
            output = root / 'boot-back'
            base = {'validation': 'passed', 'application_path': '/usr/local/bin/attached-forge-' + 'a' * 32,
                    'application_sha256': '0' * 64, 'export_sha256': '0' * 64}
            for changes, error in (({'application_path': '/tmp/foo; id'}, 'fixture path'),
                                   ({'application_sha256': 'not-a-digest'}, 'digest'),
                                   ({}, 'checksum')):
                with self.subTest(changes=changes):
                    record.write_text(json.dumps(dict(base, **changes)))
                    with patch.object(sys, 'argv', ['verify', '--record', str(record), '--output', str(output)]), \
                            patch('validate_exported_application.Runtime') as runtime:
                        with self.assertRaisesRegex(ValueError, error):
                            main()
                        runtime.assert_not_called()
                    self.assertFalse(output.exists())
                    self.assertEqual(image.read_bytes(), b'fixture bytes')


if __name__ == '__main__':
    unittest.main()
