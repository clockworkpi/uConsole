from contextlib import nullcontext
from types import SimpleNamespace
import stat
import unittest
from unittest.mock import MagicMock, Mock, patch

from forge_recovery_bootplan import digest
import forge_recovery_restore_observe as observe
import test_recovery_restore_identity
import test_recovery_restore_plan


class RestoreObservationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.plan = fixture.compile()
        self.pin = digest(self.plan)
        self.attempt = 'e'*32
        self.boot = self.plan['binding']['boot_id']

    def identity_fixture(self):
        identity = test_recovery_restore_identity.RestoreIdentityTests()
        identity.setUp()
        self.plan['binding'] = identity.binding
        self.pin = digest(self.plan)
        self.boot = identity.binding['boot_id']
        return identity, observe.Observation(self.plan,self.pin,self.attempt,self.boot)

    def test_exact_read_only_mount_can_be_removed_from_identity_inventory_only_for_cleanup(self):
        fixture, view = self.identity_fixture()
        mount = '3 1 179:1 / '+str(view.point)+' ro,nosuid,nodev,noexec - vfat /dev/mmcblk0p1 ro\n'
        fixture.ram['identity']['mountinfo'] += mount
        with patch.object(observe,'observe_local',side_effect=[fixture.ram,fixture.layout,fixture.ram]), \
                patch.object(observe,'check_mount') as checked:
            self.assertEqual(view.check(allow_stale=True),fixture.binding['extent'])
            self.assertTrue(view.stale)
            self.assertTrue(all(call.kwargs=={'read_only':True} for call in checked.call_args_list))
        with patch.object(observe,'observe_local',return_value=fixture.ram),self.assertRaises(ValueError):
            view.check()

    def test_writable_or_unrelated_mount_never_gets_cleanup_exception(self):
        for target, options in (('/run/unrelated','ro'),('/run/forge-boot-commit-'+self.attempt,'rw')):
            fixture, view = self.identity_fixture()
            fixture.ram['identity']['mountinfo'] += '3 1 179:1 / '+target+' '+options+' - vfat /dev/mmcblk0p1 '+options+'\n'
            with patch.object(observe,'observe_local',return_value=fixture.ram), \
                    patch.object(observe,'check_mount'),self.assertRaises(ValueError):
                view.check(allow_stale=True)

    def test_stale_mount_exception_does_not_hide_wrong_boot(self):
        fixture, view = self.identity_fixture()
        fixture.ram['identity']['boot_id']='abcdef01-1234-1234-1234-123456789abc'
        fixture.ram['identity']['mountinfo'] += '3 1 179:1 / '+str(view.point)+' ro - vfat /dev/mmcblk0p1 ro\n'
        with patch.object(observe,'observe_local',return_value=fixture.ram), \
                patch.object(observe,'check_mount'),self.assertRaises(ValueError):
            view.check(allow_stale=True)

    def mock_hardware(self):
        patches = dict(check=patch.object(observe.Observation,'check',return_value=self.plan['binding']['extent']),
            ensure=patch.object(observe,'ensure_lock_directory'),
            lock=patch.object(observe,'target_lock',side_effect=lambda _:nullcontext()),
            claim=patch.object(observe,'claim_root',side_effect=lambda *_:nullcontext(SimpleNamespace(card_fd=42))),
            provision=patch.object(observe.ledger,'provision',return_value='/fixture-ledger'),
            fence=patch.object(observe.ledger,'fence',return_value=dict(status='incomplete')))
        result = {}
        for name, patcher in patches.items():
            result[name] = patcher.start()
            self.addCleanup(patcher.stop)
        return result

    def test_same_boot_fences_original_attempt_without_root_write(self):
        hardware = self.mock_hardware()
        result = observe.fence(self.plan,self.pin,self.attempt,observed_boot_id=self.boot)
        hardware['fence'].assert_called_once_with('/fixture-ledger',self.plan,self.pin,self.attempt)
        self.assertEqual(result['outcome']['status'],'incomplete')
        self.assertFalse(result['root_written'])
        self.assertFalse(result['normal_boot_release_authorized'])

    def test_active_restore_fence_failure_prevents_claim_or_cleanup(self):
        hardware = self.mock_hardware()
        hardware['fence'].side_effect = BlockingIOError('worker active')
        with self.assertRaises(BlockingIOError):
            observe.fence(self.plan,self.pin,self.attempt,observed_boot_id=self.boot)
        hardware['claim'].assert_not_called()

    def test_new_boot_never_recreates_prior_boot_ledger(self):
        hardware = self.mock_hardware()
        boot = '99999999-1234-1234-1234-123456789abc'
        result = observe.fence(self.plan,self.pin,self.attempt,observed_boot_id=boot)
        self.assertEqual(result['outcome']['status'],'previous-boot-ended')
        self.assertEqual(result['outcome']['previous_boot_id'],self.boot)
        hardware['provision'].assert_not_called()
        hardware['fence'].assert_not_called()
        self.assertEqual(self.plan['binding']['boot_id'],self.boot)

    def test_cleanup_unmounts_only_exact_read_only_attempt_path(self):
        self.mock_hardware()
        point = MagicMock()
        point.__str__.return_value = '/run/forge-boot-commit-'+self.attempt
        view = SimpleNamespace(plan=self.plan,binding=self.plan['binding'],point=point,stale=True)
        view.check = Mock(return_value=view.binding['extent'])
        with patch.object(observe,'Observation',return_value=view), \
                patch.object(observe,'check_mount') as mounted, \
                patch.object(observe.subprocess,'run') as unmount, \
                patch.object(observe.os.path,'ismount',return_value=False):
            result = observe.fence(self.plan,self.pin,self.attempt,observed_boot_id=self.boot)
        mounted.assert_called_once_with(point,'/dev/mmcblk0p1',read_only=True)
        unmount.assert_called_once_with(['umount',str(point)],check=True,timeout=10)
        point.rmdir.assert_called_once()
        self.assertTrue(result['stale_read_only_boot_unmounted'])
        self.assertFalse(result['stale_empty_mountpoint_removed'])

    def test_abandoned_empty_ram_mountpoint_cleanup_does_not_remove_nonempty_data(self):
        self.mock_hardware()
        point = MagicMock()
        point.exists.return_value = True
        point.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0,st_gid=0)
        view = SimpleNamespace(plan=self.plan,binding=self.plan['binding'],point=point,stale=False,
                               check=Mock(return_value=self.plan['binding']['extent']))
        with patch.object(observe,'Observation',return_value=view):
            result = observe.fence(self.plan,self.pin,self.attempt,observed_boot_id=self.boot)
            self.assertTrue(result['stale_empty_mountpoint_removed'])
            point.rmdir.side_effect = OSError('directory not empty')
            with self.assertRaisesRegex(OSError,'not empty'):
                observe.fence(self.plan,self.pin,self.attempt,observed_boot_id=self.boot)

    def test_read_only_inspection_requires_matching_hold_and_stable_prefix(self):
        self.mock_hardware()
        files = {key:self.fixture.inspection[key] for key in ('status','files','image','stage')}
        with patch.object(observe,'budget',return_value=240), \
                patch.object(observe,'commit_timer',return_value=nullcontext()), \
                patch.object(observe,'mounted_boot',return_value=nullcontext('/fixture')) as mount, \
                patch.object(observe,'inspect_files',return_value=files), \
                patch.object(observe,'range_digest',return_value='a'*64):
            result = self.inspect()
        mount.assert_called_once_with('/dev/mmcblk0p1',self.attempt,read_only=True)
        self.assertEqual(result['prefix']['sha256'],'a'*64)
        self.assertTrue(result['boot_unmounted'])
        self.assertFalse(result['root_written'])

    def inspect(self):
        return observe.inspect(self.plan,self.pin,self.attempt,self.fixture.hold,self.fixture.hold_pin,
            observed_boot_id=self.boot,owner=self.plan['binding']['lease_owner'],accepted={})

    def test_changed_prefix_or_expired_budget_cannot_produce_inspection(self):
        self.mock_hardware()
        with patch.object(observe,'budget',return_value=179),patch.object(observe,'mounted_boot') as mount:
            with self.assertRaises(ValueError): self.inspect()
            mount.assert_not_called()
        with patch.object(observe,'budget',return_value=240), \
                patch.object(observe,'commit_timer',return_value=nullcontext()), \
                patch.object(observe,'mounted_boot',return_value=nullcontext('/fixture')), \
                patch.object(observe,'inspect_files',return_value={}), \
                patch.object(observe,'range_digest',side_effect=['a'*64,'b'*64]),self.assertRaises(ValueError):
            self.inspect()


if __name__=='__main__': unittest.main()
