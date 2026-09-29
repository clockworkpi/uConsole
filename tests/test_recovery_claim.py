import hashlib
import os
import tempfile
import unittest
import stat
import struct
from types import SimpleNamespace
from unittest.mock import patch

from forge_recovery_claim import RootClaim, block_identity, claim_root, range_digest


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.card = tempfile.TemporaryFile(dir=self.directory.name)
        self.root = tempfile.TemporaryFile(dir=self.directory.name)
        self.addCleanup(self.card.close)
        self.addCleanup(self.root.close)
        self.data = b'p'*512 + b'r'*1024 + b's'*512
        self.card.write(self.data)
        self.card.flush()
        self.root.write(self.data[512:1536])
        self.root.flush()
        self.extent = dict(device='/dev/mmcblk0p2', offset_bytes=512,
                           length_bytes=1024, disk_bytes=2048)
        self.guards = {name: dict(offset=start, bytes=end-start,
                                 sha256=hashlib.sha256(self.data[start:end]).hexdigest())
                       for name, start, end in [('prefix',0,512), ('root',512,1536), ('suffix',1536,2048)]}

    def test_real_descriptor_hashes_and_progress(self):
        claim = RootClaim(self.root.fileno(), self.card.fileno(), self.extent)
        progress = []
        self.assertEqual(claim.verify_guards(self.guards, progress=lambda *args: progress.append(args)),
                         dict(status='matched', ranges=['prefix','root','suffix']))
        self.assertEqual([item[0] for item in progress], ['prefix', 'root', 'suffix'])
        # Root reads must come from the exclusively claimed partition descriptor.
        os.pwrite(self.root.fileno(), b'x', 0)
        with self.assertRaisesRegex(ValueError, 'digest differs: root'):
            claim.verify_guards(self.guards)

    def test_malformed_ranges_rejected_before_read(self):
        claim = RootClaim(self.root.fileno(), self.card.fileno(), self.extent)
        for changed in ({'offset': True}, {'bytes': 1023}, {'sha256': 'x'*64}):
            guards = {'root': dict(self.guards['root'], **changed)}
            with patch('forge_recovery_claim.range_digest') as read:
                with self.assertRaises(ValueError):
                    claim.verify_guards(guards)
                read.assert_not_called()

    def test_short_read_and_empty_range(self):
        with self.assertRaisesRegex(ValueError, 'ended early'):
            range_digest(self.root.fileno(), 0, 2048)
        self.assertEqual(range_digest(self.root.fileno(), 1024, 0), hashlib.sha256(b'').hexdigest())
        with self.assertRaises(ValueError):
            range_digest(self.root.fileno(), 0, True)

    def run_claim(self, check, body):
        opened = []
        def open_device(path, flags):
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertEqual(bool(flags & os.O_EXCL), path.endswith('p2'))
            fd = os.dup(self.root.fileno() if path.endswith('p2') else self.card.fileno())
            opened.append(fd)
            return fd
        try:
            with patch('forge_recovery_claim.os.open', side_effect=open_device), \
                    patch('forge_recovery_claim.block_identity') as identity:
                with claim_root('/dev/mmcblk0', self.extent, check) as claim:
                    self.assertEqual(identity.call_count, 2)
                    body(claim)
        finally:
            for fd in opened:
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_claim_lifetime_and_postcheck(self):
        calls, claims = [], []
        def check():
            calls.append(True)
            return self.extent
        def body(claim):
            claims.append(claim)
            claim.verify_guards(self.guards)
            os.fstat(claim.root_fd)
        self.run_claim(check, body)
        self.assertEqual(len(calls), 3)
        with self.assertRaisesRegex(ValueError, 'released'):
            claims[0].verify_guards(self.guards)

    def test_exception_closes_claim(self):
        claims = []
        def body(claim):
            claims.append(claim)
            raise RuntimeError('commit interrupted')
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.run_claim(lambda: self.extent, body)
        self.assertFalse(claims[0].active)

    def test_layout_change_after_acquisition_and_before_release(self):
        for fail_at in (2, 3):
            calls = []
            def check():
                calls.append(True)
                return {} if len(calls) == fail_at else self.extent
            with self.assertRaisesRegex(ValueError, 'layout changed'):
                self.run_claim(check, lambda claim: None)

    def test_bad_layout_never_opens(self):
        with patch('forge_recovery_claim.os.open') as opened:
            with self.assertRaises(ValueError):
                with claim_root('/dev/mmcblk0', dict(self.extent, length_bytes=True), lambda: self.extent):
                    self.fail('Invalid geometry accepted')
            opened.assert_not_called()

    def test_device_identity_requires_block_type_number_and_size(self):
        for mode, number, length, valid in (
                (stat.S_IFBLK, os.makedev(179, 2), 1024, True),
                (stat.S_IFREG, os.makedev(179, 2), 1024, False),
                (stat.S_IFBLK, os.makedev(179, 3), 1024, False),
                (stat.S_IFBLK, os.makedev(179, 2), 2048, False)):
            def ioctl(fd, operation, buffer, mutate):
                self.assertEqual(operation, 0x80081272)
                buffer[:] = struct.pack('=Q', length)
            with patch('forge_recovery_claim.os.fstat', return_value=SimpleNamespace(st_mode=mode, st_rdev=number)), \
                    patch('forge_recovery_claim.Path.read_text', return_value='179:2\n'), \
                    patch('forge_recovery_claim.fcntl.ioctl', side_effect=ioctl):
                if valid:
                    block_identity(123, '/dev/mmcblk0p2', 1024)
                else:
                    with self.assertRaises(ValueError):
                        block_identity(123, '/dev/mmcblk0p2', 1024)


if __name__ == '__main__':
    unittest.main()
