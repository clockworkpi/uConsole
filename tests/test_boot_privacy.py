import unittest
import subprocess
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import copy
from unittest.mock import patch
from forge_boot_privacy import private_fstab, prepare_policy, verify_mount
from validate_boot_privacy import run, unprivileged_read


class BootPrivacyTests(unittest.TestCase):
    def test_effective_mount_verifier_rejects_false_privacy(self):
        observed = dict(mounts={'filesystems': [dict(source='/dev/test', fstype='vfat',
                                                    options='rw,fmask=0077,dmask=0077')]},
                        metadata={p: dict(uid=0, gid=0, mode=0o700) for p in
                                  ('/boot/firmware', '/boot/firmware/config.txt')}, probe_exit=13)
        self.assertEqual(verify_mount(observed, '/dev/test', True)['status'], 'verified-private')
        wrong = copy.deepcopy(observed)
        wrong['mounts']['filesystems'][0]['options'] += ',fmask=0022'
        with self.assertRaises(ValueError):
            verify_mount(wrong, '/dev/test', True)
        for code in (0, 1, 2):
            with self.subTest(code=code), self.assertRaises(ValueError):
                verify_mount(dict(observed, probe_exit=code), '/dev/test', True)
        wrong = copy.deepcopy(observed)
        wrong['metadata']['/boot/firmware/config.txt']['mode'] = 0o755
        with self.assertRaises(ValueError):
            verify_mount(wrong, '/dev/test', True)

    def test_validator_requires_isolation_before_mutation(self):
        with patch('validate_boot_privacy.os.readlink', return_value='same'), \
                patch('validate_boot_privacy.tempfile.mkdtemp') as mkdir:
            with self.assertRaises(RuntimeError):
                run()
        mkdir.assert_not_called()

    def test_only_permission_denied_counts_as_access_denial(self):
        for code, expected in ((0, 'readable'), (13, 'denied')):
            with patch('validate_boot_privacy.subprocess.run', return_value=subprocess.CompletedProcess([], code)):
                self.assertEqual(unprivileged_read('/fixture'), expected)
        with patch('validate_boot_privacy.subprocess.run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(RuntimeError):
                unprivileged_read('/fixture')

    def setUp(self):
        self.source = 'PARTUUID=21965b0c-01'
        self.line = self.source + '  /boot/firmware  vfat  defaults  0  2 # boot\n'

    def test_changes_only_options_preserves_comments_and_other_mounts(self):
        other = '# note\nUUID=abcd / ext4 defaults,noatime 0 1\n'
        result = private_fstab(other + self.line, self.source)
        self.assertEqual(result, other + self.line.replace('defaults  ',
                         'defaults,uid=0,gid=0,fmask=0077,dmask=0077  '))
        self.assertEqual(private_fstab(result, self.source), result)

    def test_policy_preparation_preserves_exact_restore_state(self):
        data = self.line.encode()
        record = dict(path='/etc/fstab', kind='file', data=base64.b64encode(data).decode(),
                      size=len(data), sha256=hashlib.sha256(data).hexdigest(), uid=0, gid=0,
                      mode=0o644, atime_ns=12, mtime_ns=14, xattrs={})
        backup = dict(schema=1, machine_id='a'*32, files=[record])
        with tempfile.TemporaryDirectory() as directory:
            result = prepare_policy(Path(directory)/'plan', 'clockworkpi.local', backup, self.source)
            plan = json.loads((Path(result['journal'])/'plan.json').read_text())
        self.assertEqual(plan['before'], backup)
        changed = plan['after']['files'][0]
        self.assertEqual(base64.b64decode(changed['data']).decode(), private_fstab(self.line, self.source))
        for name in ('mode', 'uid', 'gid', 'xattrs', 'atime_ns'):
            self.assertEqual(changed[name], record[name])

    def test_ambiguous_or_unreviewed_policy_rejected(self):
        for text in (self.line * 2, '', self.line.replace('vfat', 'ext4'),
                     self.line.replace('defaults', 'defaults,user'),
                     self.line.replace('defaults', 'uid=1000'),
                     self.line.replace('defaults', 'fmask=0022,fmask=0077'),
                     self.line.replace('/boot/firmware', '/boot\\057firmware')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                private_fstab(text, self.source)
        with self.assertRaises(ValueError):
            private_fstab(self.line, 'PARTUUID=other')
