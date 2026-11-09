import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_prepare import author, freeze_sources


class TargetAuthorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'application'
        self.source.write_bytes(b'new application')
        self.output = self.root / 'prepared'
        self.mapping = [{'source': str(self.source), 'target': '/usr/local/bin/proof', 'mode': '0755'}]
        self.old = {'path': '/usr/local/bin/proof', 'kind': 'absent'}

    def capture(self, host, paths, output):
        output.write_text(json.dumps({'schema': 1, 'machine_id': 'a' * 32, 'files': [self.old]}))
        output.chmod(0o600)
        # Local editing after snapshot must not alter the reviewed deployment.
        self.source.write_bytes(b'later local edit')
        return {'status': 'verified-preimages'}

    def test_prepare_freezes_payload_preserves_absence_and_never_dispatches(self):
        with patch('forge_target_prepare.capture', side_effect=self.capture), \
                patch('forge_target_ssh.dispatch') as dispatch:
            result = author(self.output, 'clockworkpi.local', self.mapping)
        dispatch.assert_not_called()
        self.assertFalse(result['deployment_performed'])
        self.assertEqual(result['status'], 'prepared-for-review')
        plan = json.loads((self.output / 'transaction/plan.json').read_text())
        self.assertEqual(base64.b64decode(plan['after']['files'][0]['data']), b'new application')
        self.assertEqual(plan['before']['files'][0], self.old)
        self.assertEqual(plan['after']['files'][0]['mode'], 0o755)
        self.assertEqual((self.output / 'review.json').stat().st_mode & 0o777, 0o600)

    def existing(self):
        data = b'old'
        self.old.update(kind='file', size=len(data), data=base64.b64encode(data).decode(),
                        sha256=hashlib.sha256(data).hexdigest(), mode=0o640, uid=123, gid=456,
                        atime_ns=10, mtime_ns=20, xattrs={'user.note': 'b2xk'})

    def test_existing_metadata_preserved_when_mode_not_requested(self):
        self.existing()
        del self.mapping[0]['mode']
        with patch('forge_target_prepare.capture', side_effect=self.capture):
            author(self.output, 'clockworkpi.local', self.mapping)
        plan = json.loads((self.output / 'transaction/plan.json').read_text())
        after = plan['after']['files'][0]
        for field in ('mode', 'uid', 'gid', 'xattrs', 'atime_ns'):
            self.assertEqual(after[field], self.old[field])
        self.assertEqual(plan['before']['files'][0], self.old)

    def test_bad_sources_modes_hashes_and_targets_fail_before_ssh(self):
        bad = [dict(self.mapping[0], mode='4755'), dict(self.mapping[0], mode=755),
               dict(self.mapping[0], sha256='0' * 64), dict(self.mapping[0], target='/boot/firmware/kernel8.img'),
               dict(self.mapping[0], target='/a/../b'), dict(self.mapping[0], source=str(self.root))]
        for item in bad:
            with self.subTest(item=item), patch('forge_target_prepare.capture') as capture:
                with self.assertRaises(ValueError):
                    author(self.output, 'clockworkpi.local', [item])
                capture.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_duplicate_targets_rejected(self):
        with self.assertRaises(ValueError):
            freeze_sources(self.mapping * 2)

    def test_privileged_existing_file_preserves_backup_but_creates_no_plan(self):
        self.existing()
        self.old['xattrs']['security.capability'] = 'AA=='
        with patch('forge_target_prepare.capture', side_effect=self.capture):
            with self.assertRaises(ValueError):
                author(self.output, 'clockworkpi.local', self.mapping)
        self.assertTrue((self.output / 'before.json').exists())
        self.assertFalse((self.output / 'transaction').exists())

    def test_existing_output_never_reused(self):
        self.output.mkdir()
        with patch('forge_target_prepare.capture') as capture:
            with self.assertRaises(FileExistsError):
                author(self.output, 'clockworkpi.local', self.mapping)
            capture.assert_not_called()
