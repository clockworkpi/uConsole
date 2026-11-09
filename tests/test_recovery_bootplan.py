import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from forge_recovery_bootplan import compile_transition, prepare
from forge_target_journal import validate_plan
from forge_tryboot_recipe import CONFIG, CMDLINE
import test_recovery_hold


class RecoveryBootPlanTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_hold.RecoveryHoldTests()
        fixture.setUp()
        self.review = fixture.compile()
        self.binding = dict(nonce=self.review['nonce'], kernel='6.12.62-v8+', serial='100000007b961d25',
            mode='physical', boot_id='12345678-1234-1234-1234-123456789abc', cid='a'*32,
            disk_id='21965b0c', device='/dev/mmcblk0',
            extent=dict(disk_bytes=12288, offset_bytes=4096, length_bytes=8192))
        self.root = dict(offset=4096, bytes=8192, sha256='c'*64)
        self.observation = dict(status='verified-offline-storage-digests', mode='physical', is_backup=False,
            root_write_authorized=False, normal_boot_release_authorized=False, target_written=False,
            digests=dict(boot_id=self.binding['boot_id'], card=dict(bytes=12288, sha256='d'*64),
                         prefix=dict(offset=0, bytes=4096, sha256='e'*64), root=copy.deepcopy(self.root),
                         suffix=dict(offset=12288, bytes=0, sha256=hashlib.sha256(b'').hexdigest())))

    def compile(self, operation='install-hold'):
        return compile_transition(self.review, self.binding, self.observation, self.root, operation)

    def test_only_selector_changes_and_release_retains_all_gates(self):
        install, release = self.compile(), self.compile('release-hold')
        self.assertEqual(install['before'], release['after'])
        self.assertEqual(install['after'], release['before'])
        self.assertEqual(install['changed_paths'], [CONFIG])
        self.assertEqual(install['root_guard'], self.root)
        self.assertTrue(set(self.review['required_gates']).issubset(install['required_gates']))
        self.assertTrue(set(self.review['release_requires']).issubset(release['required_gates']))
        for plan in (install, release):
            for field in ('deployment_authorized','root_write_authorized','normal_boot_release_authorized'):
                self.assertFalse(plan[field])
            with self.assertRaises(ValueError):
                validate_plan(plan)

    def test_release_never_silently_adopts_current_root(self):
        self.observation['digests']['root']['sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'independently approved root'):
            self.compile('release-hold')

    def test_other_boot_file_or_metadata_changes_are_rejected(self):
        original = copy.deepcopy(self.review)
        for field in ('content', 'mode'):
            self.review = copy.deepcopy(original)
            path = CMDLINE if field == 'content' else CONFIG
            record = next(item for item in self.review['after']['files'] if item['path'] == path)
            if field == 'content':
                data = b'changed unrelated command'
                record.update(data=base64.b64encode(data).decode(), size=len(data),
                              sha256=hashlib.sha256(data).hexdigest())
            else:
                record['mode'] = 0o755
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.compile()

    def test_identity_nonce_lease_or_incomplete_evidence_rejected(self):
        for field, value in (('nonce','f'*32), ('serial',None), ('lease_owner','a'*64),
                             ('device','/dev/mmcblk0p2'), ('kernel',None)):
            old = copy.deepcopy(self.binding)
            self.binding[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.compile()
            self.binding = old
        self.observation['status'] = 'incomplete'
        with self.assertRaises(ValueError):
            self.compile()

    def test_preparation_is_private_exclusive_and_digest_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'plan'
            result = prepare(path, self.review, self.binding, self.observation, self.root, 'install-hold')
            payload = (path/'plan.json').read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result['plan_sha256'])
            self.assertEqual(json.loads(payload)['root_guard'], self.root)
            self.assertRegex(json.loads(payload)['stage_token'], '^[0-9a-f]{32}$')
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path/'plan.json').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                prepare(path, self.review, self.binding, self.observation, self.root, 'release-hold')
