import base64
from contextlib import contextmanager
import copy
import hashlib
import os
from pathlib import Path
import signal
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import forge_recovery_bootcommit as commit
from forge_target_files import TargetConflict
from forge_tryboot_recipe import CONFIG
import test_recovery_bootplan
from target_test_support import linux_target


class BootCommitTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_bootplan.RecoveryBootPlanTests()
        fixture.setUp()
        self.plan = fixture.compile()
        self.plan['stage_token'] = 'a'*32
        self.pin = commit.plan_digest(self.plan)

    def test_pinned_complete_single_selector_plan_required(self):
        commit.validate(self.plan, self.pin)
        with self.assertRaises(ValueError):
            commit.validate(self.plan, 'f'*64)
        for field, value in (('stage_token','bad'), ('changed_paths',[]),
                             ('root_write_authorized',True), ('unexpected',True)):
            plan = dict(self.plan, **{field:value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                commit.validate(plan, commit.plan_digest(plan))

    def test_recomputed_pin_cannot_widen_file_scope(self):
        plan = copy.deepcopy(self.plan)
        record = next(item for item in plan['after']['files'] if item['path'] != CONFIG and item['kind']=='file')
        record.update(data=base64.b64encode(b'other').decode(), size=5,
                      sha256=hashlib.sha256(b'other').hexdigest())
        with self.assertRaises(ValueError):
            commit.validate(plan, commit.plan_digest(plan))

    def test_identity_failure_prevents_even_ram_lock_creation(self):
        with patch.object(commit,'ensure_lock_directory') as directory, patch.object(commit,'target_lock') as lock:
            with self.assertRaisesRegex(ValueError,'before locking'):
                commit.execute(self.plan,self.pin,check=lambda:{},approve=lambda *args:None,
                               live_budget=lambda:300)
            directory.assert_not_called()
            lock.assert_not_called()

    def test_lock_directory_rejects_unowned_or_writable_directory(self):
        for uid, mode, valid in ((0,0o755,True),(1,0o755,False),(0,0o777,False),(0,0o775,False)):
            with patch.object(Path,'mkdir') as mkdir, patch.object(os,'open',return_value=123) as opened, \
                    patch.object(os,'fstat',return_value=SimpleNamespace(st_uid=uid,st_mode=stat.S_IFDIR|mode)), \
                    patch.object(os,'close') as close:
                if valid:
                    commit.ensure_lock_directory()
                else:
                    with self.assertRaises(ValueError):
                        commit.ensure_lock_directory()
                mkdir.assert_called_once_with(mode=0o755,exist_ok=True)
                self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
                close.assert_called_once_with(123)

    def materialize(self, directory):
        plan = copy.deepcopy(self.plan)
        for state in ('before','after'):
            for record in plan[state]['files']:
                if record['kind'] == 'file':
                    record.update(uid=os.getuid(), gid=os.getgid())
        for record in plan['before']['files']:
            if record['kind'] == 'file':
                path = directory/Path(record['path']).name
                path.write_bytes(base64.b64decode(record['data']))
                path.chmod(record['mode'])
                os.utime(path, ns=(record['atime_ns'],record['mtime_ns']))
        image = plan['image_dependency']
        payload = b'disposable recovery image fixture'
        image.update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        path = directory/Path(image['path']).name
        path.write_bytes(payload)
        path.chmod(0o700)
        return plan

    @linux_target
    def test_real_files_commit_changes_only_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = self.materialize(directory)
            before = {p.name:p.read_bytes() for p in directory.iterdir()}
            result = commit.files_commit(plan, directory)
            self.assertEqual(result, dict(status='applied', path=CONFIG))
            after = {p.name:p.read_bytes() for p in directory.iterdir()}
            self.assertEqual([name for name in before if before[name]!=after[name]], ['config.txt'])
            self.assertEqual(set(before), set(after))
            # A lost reply is not permission to rerun against stale range guards.
            with self.assertRaises(TargetConflict):
                commit.files_commit(plan, directory)

    @linux_target
    def test_changed_dependency_or_image_never_changes_selector(self):
        for kind in ('file','image'):
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                plan = self.materialize(directory)
                before = (directory/'config.txt').read_bytes()
                path = directory/('cmdline.txt' if kind=='file' else Path(plan['image_dependency']['path']).name)
                path.write_bytes(b'changed dependency')
                with self.assertRaises((ValueError, TargetConflict)):
                    commit.files_commit(plan, directory)
                self.assertEqual((directory/'config.txt').read_bytes(), before)

    @linux_target
    def test_image_symlink_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = self.materialize(directory)
            path = directory/Path(plan['image_dependency']['path']).name
            path.unlink()
            path.symlink_to(directory/'config.txt')
            with patch.object(commit, 'apply_file') as apply:
                with self.assertRaises(OSError):
                    commit.files_commit(plan, directory)
                apply.assert_not_called()

    def simulate(self, *, acknowledgement=True, budget=300, fault=None):
        events = []
        @contextmanager
        def lock(path):
            events.append('lock')
            try: yield
            finally: events.append('unlock')
        class Claim:
            def verify_guards(inner, guards, **kwargs):
                events.append('hash:'+','.join(sorted(guards)))
                if fault=='posthash' and 'prefix' not in guards:
                    raise ValueError('root changed')
        @contextmanager
        def claim(*args):
            events.append('claim')
            try: yield Claim()
            finally: events.append('release')
        @contextmanager
        def mount(*args):
            events.append('mount')
            try: yield Path('/run/fixture')
            finally:
                events.append('unmount')
                if fault=='unmount': raise RuntimeError('unmount failed')
        @contextmanager
        def timer():
            events.append('timer')
            yield
        def approve(pin, binding):
            events.append('approve')
            return {'commit':pin, 'boot_id':binding['boot_id']} if acknowledgement else {}
        def live_budget():
            events.append('budget')
            return budget
        def files(*args):
            events.append('write')
            if fault=='write': raise RuntimeError('reply lost after write')
            return dict(status='applied', path=CONFIG)
        with patch.object(commit,'target_lock',lock), patch.object(commit,'claim_root',claim), \
                patch.object(commit,'ensure_lock_directory'), \
                patch.object(commit,'mounted_boot',mount), patch.object(commit,'commit_timer',timer), \
                patch.object(commit,'files_commit',files):
            try:
                result = commit.execute(self.plan, self.pin, check=lambda:self.plan['binding']['extent'],
                                        approve=approve, live_budget=live_budget)
                return result, events
            except Exception as exc:
                exc.events = events
                raise

    def test_approval_and_unmount_order(self):
        result, events = self.simulate()
        self.assertEqual(events, ['lock','claim','hash:prefix,root,suffix','approve','budget',
                                  'timer','mount','write','unmount','hash:root,suffix','release','unlock'])
        self.assertEqual(result['status'], 'boot-file-commit-verified')
        self.assertTrue(result['boot_unmounted'])
        self.assertFalse(result['root_written'])
        self.assertFalse(result['reboot_performed'])

    def test_missing_approval_or_budget_prevents_mount(self):
        for options in ({'acknowledgement':False}, {'budget':89}, {'budget':301},
                        {'budget':float('nan')}, {'budget':True}):
            with self.subTest(options=options), self.assertRaises(ValueError) as raised:
                self.simulate(**options)
            self.assertNotIn('mount', raised.exception.events)
            self.assertEqual(raised.exception.events[-2:], ['release','unlock'])

    def test_write_unmount_and_posthash_failures_never_acknowledge(self):
        for fault in ('write','unmount','posthash'):
            with self.subTest(fault=fault), self.assertRaises((ValueError,RuntimeError)) as raised:
                self.simulate(fault=fault)
            self.assertIn('unmount', raised.exception.events)
            self.assertEqual(raised.exception.events[-2:], ['release','unlock'])

    def test_mount_requires_exact_device_private_fat_and_safe_options(self):
        point = Path('/run/forge-boot-commit-'+'a'*32)
        valid = f'42 1 179:1 / {point} rw,nosuid,nodev,noexec - vfat /dev/mmcblk0p1 rw\n'
        for record, mode, passes in ((valid,0o700,True),
                (valid.replace('179:1','179:2'),0o700,False),
                (valid.replace('vfat','ext4'),0o700,False),
                (valid.replace(',nodev',''),0o700,False),
                (valid+valid,0o700,False), (valid,0o755,False)):
            def read(path):
                return record if str(path)=='/proc/self/mountinfo' else '179:1'
            with patch.object(Path,'read_text',read), patch.object(Path,'lstat',return_value=
                    SimpleNamespace(st_mode=stat.S_IFDIR|mode, st_uid=0, st_gid=0)):
                if passes:
                    commit.check_mount(point, '/dev/mmcblk0p1')
                else:
                    with self.assertRaises(ValueError):
                        commit.check_mount(point, '/dev/mmcblk0p1')

    def test_mount_attempt_is_cleaned_up_when_verification_fails(self):
        with patch.object(Path,'mkdir'), patch.object(Path,'rmdir') as rmdir, \
                patch.object(commit.subprocess,'run') as run, \
                patch.object(commit.os.path,'ismount',side_effect=[True,False]), \
                patch.object(commit,'check_mount',side_effect=ValueError('bad mount')):
            with self.assertRaisesRegex(ValueError,'bad mount'):
                with commit.mounted_boot('/dev/mmcblk0p1','a'*32):
                    self.fail('Unverified mount yielded')
            self.assertEqual([call.args[0][0] for call in run.call_args_list], ['mount','umount'])
            rmdir.assert_called_once()

    def test_failed_unmount_preserves_mountpoint_for_reconciliation(self):
        with patch.object(Path,'mkdir'), patch.object(Path,'rmdir') as rmdir, \
                patch.object(commit.subprocess,'run',side_effect=[None, RuntimeError('failed unmount')]), \
                patch.object(commit.os.path,'ismount',return_value=True), \
                patch.object(commit,'check_mount'):
            with self.assertRaisesRegex(RuntimeError,'failed unmount'):
                with commit.mounted_boot('/dev/mmcblk0p1','a'*32):
                    pass
            rmdir.assert_not_called()

    def test_commit_alarm_is_bounded_restored_and_does_not_replace_existing_timer(self):
        with patch.object(signal,'getitimer',return_value=(0.0,0.0)), \
                patch.object(signal,'getsignal',return_value=signal.SIG_DFL), \
                patch.object(signal,'signal') as handler, patch.object(signal,'setitimer') as timer:
            with self.assertRaisesRegex(TimeoutError,'60-second'):
                with commit.commit_timer():
                    handler.call_args.args[1](signal.SIGALRM,None)
            self.assertEqual([call.args for call in timer.call_args_list],
                             [(signal.ITIMER_REAL,60),(signal.ITIMER_REAL,0)])
            self.assertEqual(handler.call_args.args, (signal.SIGALRM,signal.SIG_DFL))
        with patch.object(signal,'getitimer',return_value=(1.0,0.0)), patch.object(signal,'signal') as handler:
            with self.assertRaisesRegex(RuntimeError,'already has an alarm'):
                with commit.commit_timer():
                    self.fail('Existing timer replaced')
            handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
