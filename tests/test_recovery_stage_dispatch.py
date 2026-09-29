import copy
import base64
from contextlib import contextmanager, nullcontext
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import forge_recovery_stage_dispatch as host
import forge_recovery_stage_worker as worker
from forge_recovery_stage_prepare import prepare
from forge_recovery_stage_review import load
from forge_target_files import equivalent
import forge_target_files as target_files
from forge_target_journal import private_directory
from forge_target_ssh import target_lock
import test_recovery_stage_prepare as fixtures


class RecoveryStageDispatchTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.RecoveryStagePreparationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.directory = fixture.output
        self.acceptance = prepare(self.directory, fixture.frozen)
        self.pin = worker.digest(self.acceptance)
        self.reviewed = load(self.directory, self.pin)
        self.boot = copy.deepcopy(fixture.boot)
        self.files = {item['path']: copy.deepcopy(item) for item in self.reviewed['plans'][0]['before']['files']}
        self.writes = []
        self.real_inspect = worker.inspect_files
        self.real_apply = worker.apply_file
        self.requests = []
        self.fail_after_effect = self.lose_reply = self.fail_before_worker = False
        self.ledger = fixture.root/'ledgers'
        self.ledger.mkdir(mode=0o700)
        patches = (
            ('forge_recovery_stage_dispatch.capture', lambda host: copy.deepcopy(self.boot)),
            ('forge_recovery_stage_dispatch.transport', self.transport),
            ('forge_recovery_stage_worker.read_boot', lambda: copy.deepcopy(self.boot)),
            ('forge_recovery_stage_worker.target_lock', lambda path: nullcontext()),
            ('forge_recovery_stage_worker.provision', self.provision),
            ('forge_recovery_stage_worker.target_policy', lambda plan: None),
            ('forge_recovery_stage_worker.mount_guard', lambda plan: None),
            ('forge_recovery_stage_worker.runtime_guard', lambda: None),
            ('forge_recovery_stage_worker.inspect_image', lambda *args: dict(destination={'state': 'matching-bytes'}, scratch={'state': 'absent'})),
            ('forge_recovery_stage_worker.inspect_files', self.inspect),
            ('forge_recovery_stage_worker.apply_file', self.apply),
            ('forge_recovery_stage_worker.os.geteuid', lambda: 0))
        for name, effect in patches:
            patched = patch(name, side_effect=effect)
            patched.start()
            self.addCleanup(patched.stop)

    def provision(self, boot, pin):
        path = self.ledger/(boot+'-'+pin)
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def inspect(self, plan):
        values = []
        for old, new in zip(plan['before']['files'], plan['after']['files']):
            current = self.files[old['path']]
            before, after = equivalent(current, old), equivalent(current, new)
            values.append(dict(path=old['path'], state='unchanged' if before and after else
                               'before' if before else 'after' if after else 'conflict'))
        return dict(files=values, scratch=[])

    def apply(self, before, after, *, stage_token):
        if equivalent(self.files[before['path']], after):
            return dict(path=before['path'], status='already-applied')
        self.assertTrue(equivalent(self.files[before['path']], before))
        self.files[before['path']] = copy.deepcopy(after)
        self.writes.append(before['path'])
        if self.fail_after_effect: raise OSError('injected death after file publication')
        return dict(path=before['path'], status='applied')

    def transport(self, value, operation, observed_boot=None):
        self.requests.append(copy.deepcopy(value))
        if self.fail_before_worker: raise TimeoutError('injected queued SSH worker')
        response = worker.perform(value, operation, observed_boot)
        if self.lose_reply: raise TimeoutError('injected lost SSH reply')
        return response

    def dispatch(self, phase='firmware-start', direction='apply', authorize=lambda value: value):
        return host.dispatch(self.directory, self.pin, phase, direction, authorize=authorize)

    def reconcile(self, phase='firmware-start', direction='apply'):
        return host.reconcile(self.directory, self.pin, phase, direction)

    def test_four_phases_and_reverse_restore_change_only_selected_boot_files(self):
        original = copy.deepcopy(self.files)
        for phase in host.PHASES:
            result = self.dispatch(phase)
            self.assertFalse(result['root_written'])
            self.assertFalse(result['reboot_performed'])
        self.boot['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        for phase in reversed(host.PHASES):
            self.dispatch(phase, 'restore')
        self.assertEqual(self.files, original)
        self.assertEqual(len(self.writes), 8)
        self.assertNotIn('/boot/firmware/config.txt', self.writes)
        self.assertNotIn('/boot/firmware/start4.elf', self.writes)

    def test_lost_reply_is_fenced_and_reconciled_without_retrying_effect(self):
        self.lose_reply = True
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
        self.lose_reply = False
        with self.assertRaisesRegex(RuntimeError, 'never resubmit'): self.dispatch()
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch('firmware-fixup')
        response = self.reconcile()
        self.assertEqual(response['outcome']['status'], 'completed')
        self.assertEqual(len(self.writes), 1)
        self.dispatch('firmware-fixup')
        self.assertEqual(len(self.writes), 2)

    def test_prestart_fence_rejects_delayed_worker(self):
        self.fail_before_worker = True
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
        delayed = copy.deepcopy(self.requests[-1])
        self.fail_before_worker = False
        response = self.reconcile()
        self.assertEqual(response['outcome']['status'], 'fenced-not-started')
        with self.assertRaisesRegex(RuntimeError, 'fenced'):
            worker.perform(delayed, 'execute')
        self.dispatch(direction='restore')
        self.assertEqual(self.writes, [])

    def test_incomplete_write_requires_new_boot_before_restore(self):
        self.fail_after_effect = True
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
        delayed = copy.deepcopy(self.requests[-1])
        self.fail_after_effect = False
        response = self.reconcile()
        self.assertEqual(response['outcome']['status'], 'incomplete')
        self.assertTrue(response['requires_new_boot'])
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch(direction='restore')
        with self.assertRaisesRegex(RuntimeError, 'incomplete'): worker.perform(delayed, 'execute')
        self.boot['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        with self.assertRaisesRegex(ValueError, 'another boot'): worker.perform(delayed, 'execute')
        response = self.reconcile()
        self.assertEqual(response['outcome']['status'], 'previous-boot-fenced')
        self.dispatch(direction='restore')
        self.assertEqual(self.files, {item['path']: item for item in self.reviewed['plans'][0]['before']['files']})

    def test_reboot_between_host_capture_and_worker_blocks_write(self):
        def change_boot(value, operation, observed_boot=None):
            self.boot['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
            return worker.perform(value, operation, observed_boot)
        with patch.object(host, 'transport', side_effect=change_boot):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
        self.assertEqual(self.writes, [])
        self.assertEqual(self.reconcile()['outcome']['status'], 'previous-boot-fenced')

    def test_wrong_or_refused_approval_has_no_attempt_or_write(self):
        for approval in (lambda value: None, lambda value: dict(value, boot_id='wrong')):
            with self.assertRaises(PermissionError): self.dispatch(authorize=approval)
        self.assertFalse(list(self.directory.glob('stage-attempt-*')))
        self.assertEqual(self.requests, [])

    def test_changed_preparation_pin_stops_before_ssh(self):
        with patch.object(host, 'capture') as contact:
            with self.assertRaises(ValueError):
                host.dispatch(self.directory, '0'*64, 'firmware-start', 'apply', authorize=lambda value: value)
            contact.assert_not_called()

    def test_skipped_phase_or_guard_conflict_never_writes(self):
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch('selector')
        self.assertEqual(self.writes, [])

    def test_changed_private_image_blocks_apply_but_not_original_file_restore(self):
        with patch.object(worker, 'target_policy', side_effect=ValueError('privacy changed')):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
        self.assertEqual(self.writes, [])
        self.boot['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        self.reconcile()
        with patch.object(host, 'published', side_effect=AssertionError('must not inspect image for restore')), \
                patch.object(worker, 'target_policy', side_effect=AssertionError('must not require image for restore')):
            self.dispatch(direction='restore')

    def test_target_lock_is_required_and_host_draft_lock_excludes_competitors(self):
        fd = private_directory(self.directory)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): self.dispatch()
        finally: os.close(fd)
        with patch.object(worker, 'target_lock', side_effect=BlockingIOError('busy')):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
        self.assertEqual(self.writes, [])

    def test_wrong_boot_mount_prevents_restore_before_any_write(self):
        with patch.object(worker, 'mount_guard', side_effect=ValueError('boot unmounted')):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch(direction='restore')
        self.assertEqual(self.writes, [])

    def test_private_image_drift_after_publication_leaves_incomplete_attempt(self):
        with patch.object(worker, 'target_policy', side_effect=[None, ValueError('policy drift')]):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
        self.assertEqual(len(self.writes), 1)
        self.assertTrue(self.reconcile()['requires_new_boot'])

    def test_non_ram_bookkeeping_is_refused_before_target_lock_or_write(self):
        with patch.object(worker, 'runtime_guard', side_effect=ValueError('not tmpfs')), \
                patch.object(worker, 'target_lock') as lock:
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
            lock.assert_not_called()
        self.assertEqual(self.writes, [])

    def test_worker_rejects_non_boot_paths_even_with_recomputed_plan_pin(self):
        self.fail_before_worker = True
        with self.assertRaises(RuntimeError): self.dispatch()
        value = self.requests[-1]
        for state in ('before', 'after'):
            value['plan'][state]['files'][-1]['path'] = '/etc/passwd'
        value['plan_sha256'] = worker.digest(value['plan'])
        with self.assertRaisesRegex(ValueError, 'nine fixed boot paths'): worker.validate(value)

    def test_owner_bootstrap_loads_all_modules_without_repository_imports(self):
        self.fail_before_worker = True
        with self.assertRaises(RuntimeError): self.dispatch()
        value = self.requests[-1]
        root = Path(host.__file__).parent
        payload = dict(value=value, operation='execute', observed_boot=None,
                       modules=[(name, (root/(name+'.py')).read_text()) for name in host.MODULES])
        script = host.BOOTSTRAP.replace('from forge_recovery_stage_worker import perform',
            'from forge_recovery_stage_worker import validate\ndef perform(value, *args):\n    validate(value)\n    return {"validated": True}')
        result = subprocess.run([sys.executable, '-I', '-c', script], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=10, check=True, cwd=self.fixture.root)
        self.assertEqual(json.loads(result.stdout), {'validated': True})

    @unittest.skipUnless(sys.platform == 'linux' and os.getuid() == 0,
                         'native root-owned filesystem integration is run separately on Linux')
    def test_native_root_owned_filesystem_roundtrip_and_lost_reply(self):
        # Only the pathname adapter and boot/mount observations are synthetic.
        # Actual bytes, metadata, atomic publication, fsync and ledgers use the
        # native filesystem below this exclusive temporary test directory.
        boot_files = self.fixture.root/'native-boot-files'
        boot_files.mkdir(mode=0o700)
        for record in self.files.values():
            if record['kind'] == 'absent': continue
            path = boot_files/Path(record['path']).name
            path.write_bytes(base64.b64decode(record['data']))
            path.chmod(record['mode'])
            os.utime(path, ns=(record['atime_ns'], record['mtime_ns']))
        original_parent = target_files.parent_fd
        @contextmanager
        def mapped_parent(path):
            if path not in self.files:
                raise AssertionError('Test worker escaped fixed boot paths')
            with original_parent(str(boot_files/Path(path).name)) as value:
                yield value
        with patch.object(target_files, 'parent_fd', mapped_parent), \
                patch.object(worker, 'parent_fd', mapped_parent), \
                patch.object(worker, 'inspect_files', self.real_inspect), \
                patch.object(worker, 'apply_file', self.real_apply), \
                patch.object(worker, 'target_lock', lambda _: target_lock(str(self.fixture.root/'native.lock'))):
            self.lose_reply = True
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): self.dispatch()
            self.lose_reply = False
            self.assertEqual(self.reconcile()['outcome']['status'], 'completed')
            for phase in host.PHASES[1:]: self.dispatch(phase)
            self.boot['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
            for phase in reversed(host.PHASES): self.dispatch(phase, 'restore')
            first_plan = self.reviewed['plans'][0]
            result = self.real_inspect(first_plan)
            self.assertTrue(all(item['state'] in ('unchanged', 'before') for item in result['files']))
            self.assertEqual(result['scratch'], [])


if __name__ == '__main__':
    unittest.main()
