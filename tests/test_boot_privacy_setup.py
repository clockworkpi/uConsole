import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import forge_boot_privacy_setup as privacy
from forge_recovery_bootplan import digest


class PrivacySetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root/'privacy'
        self.boot = dict(machine_id='a'*32, boot_id='11111111-2222-3333-4444-555555555555',
                         cmdline='root=PARTUUID=21965b0c-02 rw', partition=1, tryboot=0)
        data = b'PARTUUID=21965b0c-01 /boot/firmware vfat defaults 0 2\n'
        self.backup = dict(schema=1, machine_id=self.boot['machine_id'], files=[dict(
            path='/etc/fstab', kind='file', data=base64.b64encode(data).decode(), size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), uid=0, gid=0, mode=0o644,
            atime_ns=1, mtime_ns=2, xattrs={})])
        self.mount = dict(mounts=dict(filesystems=[dict(source='/dev/mmcblk0p1', fstype='vfat',
            options='rw,fmask=0022,dmask=0022')]), probe_exit=0,
            metadata={name: dict(uid=0, gid=0, mode=0o755) for name in ('/boot/firmware', '/boot/firmware/config.txt')})

    def capture(self, host, paths, output):
        self.assertEqual(host, 'fixture')
        self.assertEqual(paths, ['/etc/fstab'])
        value = getattr(self, 'applied', self.backup)
        output.write_text(json.dumps(value))
        output.chmod(0o600)

    def prepare(self, boots=None):
        with patch.object(privacy, 'capture_boot', side_effect=boots or [self.boot, self.boot]), \
                patch.object(privacy, 'capture_files', side_effect=self.capture), \
                patch.object(privacy, 'observe_mount', return_value=self.mount), \
                patch.object(privacy, 'fstab_source', return_value=getattr(self, 'boot_source', '/dev/mmcblk0p1')), \
                patch.object(privacy, 'verify_candidate') as parser:
            accepted = privacy.prepare(self.output, 'fixture')
            parser.assert_called_once()
            self.frozen = privacy.inputs(self.output, digest(accepted))
            return accepted

    def apply(self, *, failure=False, boots=None):
        def dispatch(directory, direction, **kwargs):
            self.assertEqual(direction, 'apply')
            self.assertEqual(kwargs['approved_plan_sha256'], digest(self.frozen['plan']))
            self.applied = self.frozen['plan']['after']
            if failure: raise TimeoutError('uncertain SSH')
            return dict(status='fixture-acknowledged')
        with patch.object(privacy, 'capture_boot', side_effect=boots or [self.boot, self.boot]), \
                patch.object(privacy, 'observe_mount', return_value=self.mount), \
                patch.object(privacy, 'fstab_source', return_value=getattr(self, 'boot_source', '/dev/mmcblk0p1')), \
                patch.object(privacy, 'capture_files', side_effect=self.capture), \
                patch.object(privacy, 'dispatch', side_effect=dispatch) as remote:
            result = privacy.apply(self.frozen)
            remote.assert_called_once()
            return result

    def test_preparation_preserves_backup_and_does_not_apply(self):
        with patch.object(privacy, 'dispatch') as remote:
            accepted = self.prepare()
            remote.assert_not_called()
        self.assertEqual(accepted['status'], 'prepared-not-applied')
        self.assertEqual(self.frozen['plan']['before'], self.backup)
        self.assertFalse(accepted['private_mount_qualified'])

    def test_apply_changes_only_fstab_and_does_not_claim_private_mount(self):
        self.prepare()
        result = self.apply()
        self.assertEqual(result['status'], 'applied-awaiting-private-mount')
        for key in ('reboot_performed', 'private_mount_qualified', 'image_publication_authorized', 'automatic_retry_performed'):
            self.assertIs(result[key], False)
        self.assertTrue((self.output/'apply-attempt/acknowledgement.json').exists())

    def test_changed_boot_prevents_application(self):
        self.prepare()
        with self.assertRaises(ValueError): self.apply(boots=[dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555')])
        self.assertFalse(hasattr(self, 'applied'))
        self.assertFalse(json.loads((self.output/'apply-attempt/failure.json').read_text())['target_write_may_have_started'])

    def test_uncertain_apply_preserves_evidence_and_blocks_replay(self):
        self.prepare()
        with self.assertRaises(TimeoutError): self.apply(failure=True)
        with patch.object(privacy, 'dispatch') as remote:
            with self.assertRaises(ValueError): privacy.apply(self.frozen)
            remote.assert_not_called()
        self.assertTrue(json.loads((self.output/'apply-attempt/failure.json').read_text())['target_write_may_have_started'])

    def test_preparation_wrong_mount_is_rejected(self):
        self.mount['probe_exit'] = 13
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_preparation_reboot_is_rejected(self):
        changed = dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555')
        with self.assertRaises(ValueError): self.prepare([self.boot, changed])
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_wrong_persistent_boot_source_rejected_before_application(self):
        self.prepare()
        self.boot_source = '/dev/mmcblk0p3'
        with self.assertRaises(ValueError): self.apply()
        self.assertFalse(hasattr(self, 'applied'))

    def test_wrong_pin_is_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError): privacy.inputs(self.output, '0'*64)

    def test_existing_preparation_is_never_overwritten(self):
        self.prepare()
        with patch.object(privacy, 'capture_boot') as remote:
            with self.assertRaises(FileExistsError): privacy.prepare(self.output, 'fixture')
            remote.assert_not_called()

    def test_forged_extra_change_cannot_pass_policy_compiler(self):
        self.prepare()
        plan = copy.deepcopy(self.frozen['plan'])
        data = base64.b64decode(plan['after']['files'][0]['data']) + b'UUID=bad /home ext4 defaults 0 2\n'
        plan['after']['files'][0].update(data=base64.b64encode(data).decode(), size=len(data), sha256=hashlib.sha256(data).hexdigest())
        (self.output/'transaction/plan.json').write_text(json.dumps(plan))
        accepted = json.loads((self.output/'acceptance.json').read_text())
        review = json.loads((self.output/'owner-review.json').read_text())
        accepted['plan_sha256'] = review['plan_sha256'] = digest(plan)
        (self.output/'acceptance.json').write_text(json.dumps(accepted))
        (self.output/'owner-review.json').write_text(json.dumps(review))
        with self.assertRaisesRegex(ValueError, 'compiled private'):
            privacy.inputs(self.output, digest(accepted))
