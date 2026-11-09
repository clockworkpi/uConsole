import copy
import os
from unittest.mock import Mock,patch
import unittest

from forge_recovery_restore_claim import claim_restore_root
import test_recovery_claim


class RestoreClaimTests(unittest.TestCase):
    def setUp(self):
        fixture=test_recovery_claim.ClaimTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture=fixture
        self.pin='a'*64
        self.boot='01234567-1234-1234-1234-0123456789ab'
        self.check=Mock(side_effect=lambda:copy.deepcopy(fixture.extent))
        self.authorize=Mock(return_value=dict(restore=self.pin,boot_id=self.boot))

    def claim(self,**options):
        args=dict(device='/dev/mmcblk0',extent=self.fixture.extent,guards=self.fixture.guards,
                  pin=self.pin,boot_id=self.boot,check=self.check,authorize=self.authorize)
        args.update(options)
        return claim_restore_root(**args)

    def run_claim(self,body,**options):
        opened=[]
        def open_device(path,flags):
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(flags & os.O_NONBLOCK)
            self.assertIn(path,('/dev/mmcblk0','/dev/mmcblk0p2'))
            root=path.endswith('p2')
            self.assertEqual(flags & os.O_ACCMODE,os.O_RDWR if root else os.O_RDONLY)
            self.assertEqual(bool(flags & os.O_EXCL),root)
            self.authorize.assert_called_once_with(self.pin,self.boot)
            fd=os.dup(self.fixture.root.fileno() if root else self.fixture.card.fileno())
            opened.append(fd)
            return fd
        try:
            with patch('forge_recovery_restore_claim.os.open',side_effect=open_device), \
                 patch('forge_recovery_restore_claim.block_identity') as identity:
                with self.claim(**options) as claim:
                    self.assertEqual(identity.call_count,2)
                    body(claim)
        finally:
            for fd in opened:
                with self.assertRaises(OSError): os.fstat(fd)

    def test_only_root_descriptor_writable_and_layout_checked_throughout(self):
        claims=[]
        def change(claim):
            claims.append(claim)
            os.pwrite(claim.root_fd,b'x',0)
            os.fsync(claim.root_fd)
        original=os.pread(self.fixture.card.fileno(),2048,0)
        self.run_claim(change)
        self.assertEqual(self.check.call_count,6)
        self.assertEqual(os.pread(self.fixture.card.fileno(),2048,0),original)
        self.assertFalse(claims[0].active)

    def test_protected_prefix_or_suffix_change_rejected(self):
        for offset in (0,1536):
            with self.subTest(offset=offset):
                self.authorize.reset_mock()
                os.pwrite(self.fixture.card.fileno(),self.fixture.data,0)
                with self.assertRaisesRegex(ValueError,'Protected digest differs'):
                    self.run_claim(lambda claim:os.pwrite(self.fixture.card.fileno(),b'!',offset))

    def test_bad_initial_root_never_yields_writable_descriptor(self):
        os.pwrite(self.fixture.root.fileno(),b'!',0)
        with self.assertRaisesRegex(ValueError,'digest differs: root'):
            self.run_claim(lambda claim:self.fail('Unexpected root accepted'))

    def test_owner_denial_or_backup_lease_never_opens_device(self):
        for approval in (None,True,dict(purpose='offline-backup'),dict(restore=self.pin,boot_id='wrong')):
            with patch('forge_recovery_restore_claim.os.open') as opened:
                with self.assertRaises(ValueError):
                    with self.claim(authorize=lambda pin,boot:approval): self.fail('Approval accepted')
                opened.assert_not_called()

    def test_geometry_guard_and_arbitrary_path_rejected_before_approval(self):
        for options in (dict(device='/dev/mmcblk0p2'),dict(device='/tmp/image'),
                        dict(extent=dict(self.fixture.extent,length_bytes=True)),
                        dict(guards={'root':self.fixture.guards['root']}),
                        dict(guards=dict(self.fixture.guards,prefix=dict(self.fixture.guards['prefix'],offset=True)))):
            with patch('forge_recovery_restore_claim.os.open') as opened:
                with self.assertRaises(ValueError):
                    with self.claim(**options): self.fail('Invalid claim accepted')
                opened.assert_not_called()
            self.authorize.assert_not_called()

    def test_layout_change_at_every_boundary_fails_closed(self):
        for step in range(1,7):
            self.authorize.reset_mock()
            count=0
            def check():
                nonlocal count
                count+=1
                return {} if count==step else self.fixture.extent
            with self.subTest(step=step),self.assertRaisesRegex(ValueError,'layout changed'):
                self.run_claim(lambda claim:None,check=check)

    def test_body_failure_closes_descriptors_and_does_not_rollback(self):
        def fail(claim):
            os.pwrite(claim.root_fd,b'!',0)
            raise RuntimeError('connection lost')
        with self.assertRaisesRegex(RuntimeError,'connection lost'): self.run_claim(fail)
        self.assertEqual(os.pread(self.fixture.root.fileno(),1,0),b'!')


if __name__=='__main__': unittest.main()
