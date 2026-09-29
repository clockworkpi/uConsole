from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import forge_recovery_tryboot as tryboot
from forge_session_enrollment import inputs
import test_recovery_stage_dispatch


class RecoveryTrybootTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_stage_dispatch.RecoveryStageDispatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.fixture.root
        for name in ('key', 'known_hosts'):
            (self.root/name).write_text('private fixture')
            (self.root/name).chmod(0o600)
        self.value = inputs(self.fixture.directory, self.fixture.pin, 'fixture',
            self.root/'key', self.root/'known_hosts', '6.12.62-v8+', '100000007b961d25', None, 1)
        self.output = self.root/'tryboot'
        self.boot_id = '99999999-2222-3333-4444-555555555555'

    def stage(self):
        for phase in tryboot.PHASES: self.fixture.dispatch(phase)

    def boot(self, observations=None, normal=None):
        def enroll(output, value):
            self.assertEqual(output, self.output/'enrollment')
            self.assertEqual(value.boot_id, self.boot_id)
            self.assertEqual(value.owner, self.value.owner)
            return dict(status='enrolled-not-leased', boot_id=value.boot_id, lease_acquired=False)
        with patch.object(tryboot, 'capture', return_value=normal or self.fixture.boot), \
                patch.object(tryboot, 'reboot_transport', return_value=dict(transport_timeout=True)) as dispatch, \
                patch.object(type(self.value.probe), 'inspect', side_effect=observations or [dict(verification=dict(boot_id=self.boot_id))]), \
                patch.object(tryboot, 'enroll', side_effect=enroll), patch.object(tryboot.time, 'sleep'):
            result = tryboot.boot_and_enroll(self.output, self.value, timeout=1)
            dispatch.assert_called_once()
            return result

    def test_all_stages_required_before_any_reboot_or_claim(self):
        with patch.object(tryboot, 'reboot_transport') as dispatch:
            with self.assertRaises(FileNotFoundError): self.boot()
            dispatch.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.value.staging/'tryboot-attempt.json').exists())

    def test_one_tryboot_pins_first_verified_ram_boot_without_lease(self):
        self.stage()
        result = self.boot([OSError('rebooting'), dict(verification=dict(boot_id=self.boot_id))])
        self.assertEqual(result['status'], 'recovery-boot-enrolled-not-leased')
        for key in ('lease_acquired', 'root_write_authorized', 'recovery_fallback_qualified', 'automatic_retry_performed'):
            self.assertIs(result[key], False)
        self.assertTrue((self.value.staging/'tryboot-attempt.json').exists())

    def test_completed_attempt_cannot_be_rebooted_again(self):
        self.stage()
        self.boot()
        with patch.object(tryboot, 'reboot_transport') as dispatch:
            with self.assertRaises(ValueError): self.boot()
            dispatch.assert_not_called()

    def test_changed_normal_boot_refuses_before_reboot(self):
        self.stage()
        with self.assertRaises(ValueError): self.boot(normal=dict(self.fixture.boot, boot_id=self.boot_id))
        self.assertFalse(json.loads((self.output/'failure.json').read_text())['reboot_may_have_started'])

    def test_failed_enrollment_retains_reboot_claim(self):
        self.stage()
        with patch.object(tryboot, 'capture', return_value=self.fixture.boot), \
                patch.object(tryboot, 'reboot_transport', return_value={}) as dispatch, \
                patch.object(type(self.value.probe), 'inspect', return_value=dict(verification=dict(boot_id=self.boot_id))), \
                patch.object(tryboot, 'enroll', side_effect=ValueError('wrong owner')):
            with self.assertRaises(ValueError): tryboot.boot_and_enroll(self.output, self.value)
            dispatch.assert_called_once()
        with self.assertRaises(ValueError): tryboot.reviewed(self.value)
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_restoration_or_pinned_old_uuid_prevents_tryboot(self):
        self.stage()
        self.fixture.dispatch('selector', 'restore')
        with self.assertRaises(ValueError): tryboot.reviewed(self.value)
        with self.assertRaises(ValueError): tryboot.reviewed(replace(self.value, boot_id=self.boot_id))

    def test_existing_output_does_not_claim_or_reboot(self):
        self.stage()
        self.output.mkdir(mode=0o700)
        with self.assertRaises(FileExistsError): self.boot()
        self.assertFalse((self.value.staging/'tryboot-attempt.json').exists())

    def test_transport_contains_exact_nine_guards_and_explicit_payload(self):
        self.stage()
        draft, staged = tryboot.reviewed(self.value)
        from types import SimpleNamespace
        with patch.object(tryboot.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as remote:
            result = tryboot.reboot_transport(draft, staged)
        request = json.loads(remote.call_args.kwargs['input'])
        self.assertEqual(len(request['files']), 9)
        self.assertEqual(request['boot'], self.fixture.boot)
        self.assertEqual(request['image'], draft['request']['image_plan'])
        self.assertFalse(result['recovery_boot_verified'])

    def remote(self, *, changed=False, private=True):
        from contextlib import nullcontext
        self.stage()
        draft, staged = tryboot.reviewed(self.value)
        files = {item['path']: item for item in staged['files']}
        def snapshot(fd, name, path):
            return dict(kind='absent', path=path) if changed else files[path]
        request = dict(modules=[], boot=self.fixture.boot, image=draft['request']['image_plan'], files=staged['files'])
        image = dict(parent_private=private, destination=dict(state='matching-bytes'), scratch=dict(state='absent'))
        with patch('json.load', return_value=request), patch('os.geteuid', return_value=0), \
                patch('forge_boot_observation.read_boot', return_value=self.fixture.boot), \
                patch('forge_target_ssh.target_lock', return_value=nullcontext()), \
                patch('forge_recovery_ssh.target_policy'), patch('forge_recovery_image.inspect', return_value=image), \
                patch('forge_target_files.parent_fd', side_effect=lambda path: nullcontext((123, path))), \
                patch('forge_target_files.snapshot', side_effect=snapshot), patch('subprocess.run') as reboot:
            if changed or not private:
                with self.assertRaises(ValueError): exec(compile(tryboot.REBOOT, '<tryboot-worker>', 'exec'), {})
                reboot.assert_not_called()
            else:
                exec(compile(tryboot.REBOOT, '<tryboot-worker>', 'exec'), {})
                reboot.assert_called_once_with(['/sbin/reboot', '0 tryboot'], check=True, timeout=15)

    def test_remote_guard_dispatches_only_exact_tryboot(self):
        self.remote()

    def test_remote_changed_boot_file_prevents_reboot(self):
        self.remote(changed=True)

    def test_remote_public_image_parent_prevents_reboot(self):
        self.remote(private=False)
