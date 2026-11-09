import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import forge_recovery_publication_prepare as publication
from forge_recovery_bootplan import digest
import test_recovery_build_job


class PublicationPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_build_job.RecoveryBuildTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.build()
        self.build = self.fixture.output
        self.accepted = json.loads((self.build/'acceptance.json').read_text())
        self.pin = digest(self.accepted)
        self.frozen = publication.inputs(self.build, self.pin)
        self.output = self.fixture.root/'publication-preparation'
        self.boot = self.fixture.boot
        data = b'PARTUUID=21965b0c-01 /boot/firmware vfat defaults,uid=0,gid=0,fmask=0077,dmask=0077 0 2\n'
        self.backup = dict(schema=1, machine_id=self.boot['machine_id'], files=[dict(
            path='/etc/fstab', kind='file', data=base64.b64encode(data).decode(), size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), uid=0, gid=0, mode=0o644,
            atime_ns=1, mtime_ns=2, xattrs={})])

    def prepare(self, *, private=True, state='absent', boots=None):
        def capture(host, paths, output):
            self.assertEqual(host, 'fixture')
            self.assertEqual(paths, ['/etc/fstab'])
            output.write_text(json.dumps(self.backup))
            output.chmod(0o600)
        def transport(plan, direction, nonce, pin):
            self.assertEqual(direction, 'inspect', 'No publication transport authorized')
            self.assertEqual(plan['source'], self.frozen['native']['image'])
            return dict(kind='inspection', nonce=nonce, plan_sha256=pin,
                machine_id=self.boot['machine_id'], boot_id=self.boot['boot_id'],
                policy=dict(valid=private), files=dict(parent_private=private,
                    destination=dict(state=state), scratch=dict(state='absent')),
                mutation_performed=False, retry_authorized=False)
        with patch.object(publication, 'capture_boot', side_effect=boots or [self.boot, self.boot]), \
                patch.object(publication, 'capture_files', side_effect=capture), \
                patch.object(publication, 'transport', side_effect=transport) as remote:
            result = publication.prepare(self.output, self.frozen)
            remote.assert_called_once()
            return result

    def test_completed_build_becomes_draft_without_publication(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'prepared-not-published')
        for field in ('publication_performed', 'reboot_performed', 'boot_qualified'):
            self.assertIs(result[field], False)
        plan = json.loads((self.output/'publication/plan.json').read_text())
        self.assertEqual(digest(plan), result['plan_sha256'])
        self.assertEqual(plan['preimage'], dict(kind='absent'))
        self.assertNotIn('private-build-canary', json.dumps(result))
        self.assertNotIn('source', result)

    def test_failed_or_changed_build_rejected_before_target_contact(self):
        (self.build/'failure.json').write_text('{}')
        with patch.object(publication, 'capture_boot') as remote:
            with self.assertRaises(ValueError): publication.prepare(self.output, self.frozen)
            remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_image_refused_before_target_contact(self):
        (self.build/'recovery.img').write_bytes(b'wrong image')
        with patch.object(publication, 'capture_boot') as remote:
            with self.assertRaises(ValueError): publication.prepare(self.output, self.frozen)
            remote.assert_not_called()

    def test_public_boot_mount_retains_failed_draft_without_publish(self):
        with self.assertRaises(ValueError): self.prepare(private=False)
        self.assertTrue((self.output/'failure.json').exists())
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_matching_existing_destination_is_not_adopted(self):
        with self.assertRaises(ValueError): self.prepare(state='matching-bytes')
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_reboot_during_preparation_rejected(self):
        changed = dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555')
        with self.assertRaises(ValueError): self.prepare(boots=[self.boot, changed])
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_changed_native_provenance_and_wrong_pin_rejected(self):
        with self.assertRaises(ValueError): publication.inputs(self.build, '0'*64)
        native = copy.deepcopy(self.frozen['native'])
        native['image'] = '/unreviewed/image'
        (self.build/'native-build.json').write_text(json.dumps(native))
        with self.assertRaises(ValueError): publication.inputs(self.build, self.pin)

    def test_existing_output_is_never_replayed(self):
        self.output.mkdir(mode=0o700)
        with patch.object(publication, 'capture_boot') as remote:
            with self.assertRaises(FileExistsError): publication.prepare(self.output, self.frozen)
            remote.assert_not_called()
