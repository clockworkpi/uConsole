import copy
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import forge_recovery_cleanup as cleanup
from forge_recovery_journal import acknowledgement
from forge_target_journal import private_directory, write_record
import test_recovery_stage_dispatch


class RecoveryCleanupTests(unittest.TestCase):
    def setUp(self):
        self.f = test_recovery_stage_dispatch.RecoveryStageDispatchTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for phase in cleanup.PHASES: self.f.dispatch(phase)
        self.f.boot['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        self.frozen = self.inputs()
        self.output = self.f.fixture.root/'cleanup'
        self.calls = []
        self.bad_files = False
        self.real_transport = cleanup.image_transport
        for name, effect in (('capture', lambda host: copy.deepcopy(self.f.boot)),
                             ('capture_files', self.capture), ('image_transport', self.transport)):
            p = patch.object(cleanup, name, side_effect=effect)
            p.start()
            self.addCleanup(p.stop)

    def inputs(self):
        return cleanup.inputs(self.f.directory, self.f.pin, self.f.boot['boot_id'])

    def capture(self, host, paths, output):
        value = dict(self.frozen['before'], files=[self.f.files[path] for path in paths])
        if self.bad_files: value['machine_id'] = 'f'*32
        fd = private_directory(output.parent)
        try: write_record(fd, output.name, value)
        finally: os.close(fd)
        return {'status': 'verified-preimages', 'files': len(paths)}

    def transport(self, frozen, boot, plan, direction, nonce, pin):
        self.calls.append(direction)
        fd = private_directory(self.f.directory)
        try:
            self.assertEqual(cleanup.phase_states(fd, self.f.reviewed, self.f.pin), list(reversed(cleanup.PHASES)))
        finally: os.close(fd)
        if direction == 'restore': return acknowledgement(plan, direction, nonce, pin)
        return dict(kind='inspection', nonce=nonce, plan_sha256=pin, machine_id=plan['machine_id'],
                    boot_id=boot['boot_id'], mutation_performed=False, retry_authorized=False,
                    policy={'valid': True}, files=dict(parent_private=True,
                        destination={'state': 'absent'}, scratch={'state': 'absent'}))

    def test_reverse_restore_before_image_removal_and_private_mount_retained(self):
        result = cleanup.cleanup(self.output, self.frozen)
        self.assertEqual(self.calls, ['restore', 'inspect'])
        self.assertEqual(result['status'], 'cleaned-staging-and-owned-image')
        self.assertTrue(result['private_mount_retained'])
        self.assertFalse(result['public_permissions_authorized'])
        self.assertTrue(self.inputs()['image_removed'])
        self.assertEqual([v['phase'] for v in self.f.requests[-4:]], list(reversed(cleanup.PHASES)))

    def test_completed_phases_are_skipped_without_replay(self):
        self.f.dispatch('selector', 'restore')
        frozen = self.inputs()
        before = len(self.f.requests)
        cleanup.cleanup(self.output, frozen)
        self.assertEqual(len(self.f.requests)-before, 3)

    def test_completed_cleanup_can_only_reinspect(self):
        cleanup.cleanup(self.output, self.frozen)
        self.calls.clear()
        before = len(self.f.requests)
        cleanup.cleanup(self.output.with_name('verify-cleanup'), self.inputs())
        self.assertEqual(self.calls, ['inspect'])
        self.assertEqual(len(self.f.requests), before)

    def test_changed_preimages_prevent_image_removal(self):
        self.bad_files = True
        with self.assertRaisesRegex(ValueError, 'preimages not restored'):
            cleanup.cleanup(self.output, self.frozen)
        self.assertEqual(self.calls, [])
        self.assertTrue((self.output/'failure.json').exists())

    def test_changed_owner_boot_prevents_any_dispatch(self):
        self.f.boot['boot_id'] = 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'
        before = len(self.f.requests)
        with self.assertRaisesRegex(ValueError, 'normal boot changed'):
            cleanup.cleanup(self.output, self.frozen)
        self.assertEqual(len(self.f.requests), before)
        self.assertEqual(self.calls, [])

    def test_lost_phase_reply_preserved_and_never_replayed(self):
        self.f.lose_reply = True
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            cleanup.cleanup(self.output, self.frozen)
        count = len(self.f.requests)
        self.f.lose_reply = False
        with self.assertRaises(RuntimeError): self.inputs()
        self.assertEqual(len(self.f.requests), count)
        self.assertEqual(self.calls, [])
        self.f.reconcile('selector', 'restore')
        cleanup.cleanup(self.output.with_name('continued'), self.inputs())
        self.assertEqual(self.calls, ['restore', 'inspect'])

    def test_review_progress_change_requires_new_review(self):
        self.f.dispatch('selector', 'restore')
        with self.assertRaisesRegex(ValueError, 'progress changed'):
            cleanup.cleanup(self.output, self.frozen)
        self.assertFalse(self.output.exists())

    def test_transport_has_explicit_stdin_and_self_contained_modules(self):
        # Use the real transport rather than the fixture's simulated target.
        with patch.object(cleanup.subprocess, 'run') as remote:
            remote.return_value.stdout = '{}'
            self.real_transport(self.frozen, self.f.boot, self.frozen['image_plan'], 'restore', 'a'*32,
                                    self.frozen['publication_sha256'])
        payload = remote.call_args.kwargs['input']
        script = cleanup.BOOTSTRAP.split('from forge_boot_observation import read_boot')[0] + 'print("loaded")\n'
        result = subprocess.run([sys.executable, '-I', '-S', '-c', script], input=payload,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'loaded')
