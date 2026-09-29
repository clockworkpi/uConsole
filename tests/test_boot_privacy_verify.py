import copy
import json
import os
import subprocess
import unittest
from unittest.mock import patch

import forge_boot_privacy_verify as privacy
from forge_target_journal import private_directory, append_event
import test_boot_privacy_setup


class PrivacyVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_boot_privacy_setup.PrivacySetupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.fixture.apply()
        self.directory = self.fixture.output
        ack = dict(nonce='b'*32, direction='apply', machine_id=self.fixture.boot['machine_id'],
                   files=[dict(path='/etc/fstab', status='applied')])
        (self.directory/'apply-attempt/acknowledgement.json').write_text(json.dumps(ack))
        fd = private_directory(self.directory/'transaction')
        try:
            intent = dict(state='dispatch', direction='apply', nonce=ack['nonce'])
            append_event(fd, intent)
            append_event(fd, dict(intent, state='acknowledged', result=ack))
        finally:
            os.close(fd)
        self.frozen = privacy.inputs(self.directory, self.fixture.frozen['acceptance_sha256'])
        self.before = self.fixture.boot
        self.after = dict(self.before, boot_id='99999999-2222-3333-4444-555555555555')
        self.mount = copy.deepcopy(self.fixture.mount)
        self.mount['mounts']['filesystems'][0]['options'] = 'rw,fmask=0077,dmask=0077'
        self.mount['probe_exit'] = 13
        for info in self.mount['metadata'].values(): info['mode'] = 0o700

    def verify(self, *, reboot=False, observations=None, transport=None):
        boots = observations or ([self.before, self.after, self.after] if reboot else [self.after, self.after])
        with patch.object(privacy, 'capture', side_effect=boots), \
                patch.object(privacy, 'capture_files', side_effect=self.fixture.capture), \
                patch.object(privacy, 'fstab_source', return_value='/dev/mmcblk0p1'), \
                patch.object(privacy, 'observe_mount', return_value=self.mount), \
                patch.object(privacy, 'reboot_transport', return_value=transport or dict(returncode=0)) as dispatch, \
                patch.object(privacy.time, 'sleep'):
            result = privacy.verify(self.frozen, reboot=reboot, timeout=1)
            self.assertEqual(dispatch.call_count, int(reboot))
            return result

    def test_manual_reboot_can_be_verified_without_reboot_request(self):
        result = self.verify()
        self.assertEqual(result['status'], 'verified-private-normal-boot')
        self.assertTrue(result['private_mount_qualified'])
        self.assertFalse(result['reboot_request_dispatched'])
        self.assertFalse(result['recovery_fallback_qualified'])

    def test_one_reboot_waits_for_new_identity_and_private_permissions(self):
        result = self.verify(reboot=True, observations=[self.before, OSError('rebooting'), self.after, self.after])
        self.assertTrue(result['reboot_request_dispatched'])
        with self.assertRaises(FileExistsError): self.verify(reboot=True)

    def test_uncertain_reboot_transport_does_not_prevent_independent_verification(self):
        result = self.verify(reboot=True, transport=dict(transport_timeout=True, do_not_repeat=True))
        self.assertTrue(result['private_mount_qualified'])

    def test_same_boot_does_not_qualify_private_mount(self):
        with self.assertRaises(RuntimeError): self.verify(observations=[self.before])
        self.assertEqual(len(list(self.directory.glob('mount-verification-*/failure.json'))), 1)

    def test_readonly_recheck_retains_failed_observation_and_never_reboots(self):
        with self.assertRaises(RuntimeError): self.verify(observations=[self.before])
        self.assertTrue(self.verify()['private_mount_qualified'])
        self.assertEqual(len(list(self.directory.glob('mount-verification-*'))), 2)
        self.assertEqual(len(list(self.directory.glob('mount-verification-*/failure.json'))), 1)

    def test_public_effective_mount_does_not_qualify(self):
        self.mount = self.fixture.mount
        with self.assertRaises(RuntimeError): self.verify()

    def test_new_boot_before_dispatch_refuses_second_reboot(self):
        with self.assertRaises(ValueError): self.verify(reboot=True, observations=[self.after])
        failed = json.loads((self.directory/'reboot-attempt/failure.json').read_text())
        self.assertFalse(failed['reboot_may_have_started'])

    def test_uncertain_application_cannot_authorize_reboot(self):
        (self.directory/'apply-attempt/failure.json').write_text('{}')
        with self.assertRaises(ValueError): privacy.inputs(self.directory, self.frozen['acceptance_sha256'])

    def test_reboot_transport_sends_exact_boot_and_fstab_pin_once(self):
        with patch.object(privacy.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 30)) as run:
            result = privacy.reboot_transport(self.frozen)
        self.assertTrue(result['do_not_repeat'])
        run.assert_called_once()
        request = json.loads(run.call_args.kwargs['input'])
        self.assertEqual(request['boot'], self.before)
        self.assertEqual(request['fstab_sha256'], self.frozen['plan']['after']['files'][0]['sha256'])
