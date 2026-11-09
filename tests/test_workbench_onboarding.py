import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import workbench_onboarding as onboarding


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.data = b'known test image bytes'
        settings = patch.multiple(onboarding, IMAGE_BYTES=len(self.data),
                                  IMAGE_SHA256=hashlib.sha256(self.data).hexdigest())
        settings.start()
        self.addCleanup(settings.stop)

    def response(self, data=None, offset=0):
        response = io.BytesIO(self.data if data is None else data)
        response.status = 206 if offset else 200
        response.headers = {'Content-Length': str(len(response.getvalue()))}
        if offset:
            response.headers['Content-Range'] = f'bytes {offset}-{len(self.data)-1}/{len(self.data)}'
        response.geturl = lambda: onboarding.IMAGE_URL
        return response

    def fetch(self, response):
        opener = Mock()
        opener.open.return_value = response
        with patch.object(onboarding.urllib.request, 'build_opener', return_value=opener):
            result = onboarding.download(self.cache, minimum_free=0)
        return result, opener

    def test_download_is_verified_before_publication_and_reused_offline(self):
        result, _ = self.fetch(self.response())
        self.assertEqual(result.read_bytes(), self.data)
        self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        with patch.object(onboarding.urllib.request, 'build_opener') as network:
            self.assertEqual(onboarding.download(self.cache), result)
        network.assert_not_called()

    def test_resume_uses_checked_byte_range(self):
        (self.cache / (onboarding.IMAGE_NAME + '.partial')).write_bytes(self.data[:5])
        result, opener = self.fetch(self.response(self.data[5:], offset=5))
        self.assertEqual(result.read_bytes(), self.data)
        self.assertEqual(opener.open.call_args.args[0].get_header('Range'), 'bytes=5-')

    def test_server_ignoring_range_restarts_partial_safely(self):
        (self.cache / (onboarding.IMAGE_NAME + '.partial')).write_bytes(b'junk')
        result, _ = self.fetch(self.response())
        self.assertEqual(result.read_bytes(), self.data)

    def test_wrong_range_leaves_partial_untouched(self):
        path = self.cache / (onboarding.IMAGE_NAME + '.partial')
        path.write_bytes(self.data[:5])
        response = self.response(self.data[5:], offset=5)
        response.headers['Content-Range'] = 'bytes 0-99/100'
        with self.assertRaisesRegex(ValueError, 'range'):
            self.fetch(response)
        self.assertEqual(path.read_bytes(), self.data[:5])

    def test_checksum_mismatch_is_quarantined_not_importable(self):
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.fetch(self.response(b'x' * len(self.data)))
        self.assertFalse((self.cache / onboarding.IMAGE_NAME).exists())
        self.assertEqual(len(list(self.cache.glob('*.rejected-*'))), 1)

    def test_short_response_is_retained_for_resume(self):
        response = self.response(self.data[:5])
        response.headers = {}  # A connection can end early without Content-Length.
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.fetch(response)
        self.assertEqual((self.cache / (onboarding.IMAGE_NAME + '.partial')).read_bytes(), self.data[:5])

    def test_excess_data_is_not_published(self):
        response = self.response(self.data + b'excess')
        response.headers = {}
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.fetch(response)
        self.assertFalse((self.cache / onboarding.IMAGE_NAME).exists())

    def test_symlink_and_low_space_are_rejected(self):
        with patch.object(onboarding.shutil, 'disk_usage', return_value=Mock(free=0)):
            with self.assertRaisesRegex(ValueError, '20 GiB'):
                onboarding.download(self.cache)
        (self.cache / onboarding.IMAGE_NAME).symlink_to(self.cache / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            onboarding.download(self.cache)

    def test_https_cannot_redirect_to_http(self):
        with self.assertRaisesRegex(ValueError, 'non-HTTPS'):
            onboarding.HTTPSRedirects().redirect_request(None, None, 302, '', {}, 'http://example.com/image')

    def test_default_hash_matches_image_importer(self):
        # Patch is local to this fixture; compare the actual module constants.
        import uconsole_emulator
        source = Path(onboarding.__file__).read_text()
        self.assertIn(uconsole_emulator.IMAGE_SHA256, source)


class PackagesTests(unittest.TestCase):
    def test_detection_is_read_only_and_selects_missing_packages(self):
        data = ''.join(f'{name}:arm64\tinstalled\n' for name in onboarding.LINUX_RUNTIME)
        with patch.object(onboarding.sys, 'platform', 'linux'), \
                patch.object(onboarding.Path, 'is_file', return_value=True), \
                patch.object(onboarding.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, data, '')) as run:
            self.assertEqual(onboarding.package_plan()['missing'], [])
            self.assertEqual(set(onboarding.package_plan(True)['missing']), set(onboarding.LINUX_BUILD))
        self.assertTrue(all(call.args[0][0] == '/usr/bin/dpkg-query' for call in run.call_args_list))

    def test_elevation_is_fixed_system_command_not_workbench_script(self):
        with patch.object(onboarding.Path, 'is_file', return_value=True):
            command = onboarding.install_command(dict(manager='apt', missing=['python3-tk']))
        self.assertEqual(command[:4], ['/usr/bin/pkexec', '/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get'])
        self.assertIn('--no-remove', command)
        self.assertIn('--no-upgrade', command)
        self.assertNotIn('sh', command)
        self.assertEqual(command[-2:], ['install', 'python3-tk'])

    def test_unapproved_package_and_empty_plan_are_rejected(self):
        for names in ([], ['-oAPT::Update::Pre-Invoke=bad'], ['arbitrary-package']):
            with self.assertRaises(ValueError):
                onboarding.install_command(dict(manager='apt', missing=names))

    def test_mac_homebrew_runs_without_root(self):
        with patch.object(onboarding.shutil, 'which', return_value='/opt/homebrew/bin/brew'):
            command = onboarding.install_command(dict(manager='brew', missing=['ninja']))
        self.assertEqual(command, ['/opt/homebrew/bin/brew', 'install', 'ninja'])
