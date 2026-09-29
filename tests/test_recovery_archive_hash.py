import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from forge_recovery_archive_hash import capture


class RecoveryArchiveHashTests(unittest.TestCase):
    def fixture(self, root, *, suffix=True):
        source = root/'backup'
        source.mkdir(mode=0o700)
        # Cross a streaming block boundary inside the root range.
        self.data = b'p'*512 + b'r'*(1024*1024+512) + (b's'*512 if suffix else b'')
        payload = gzip.compress(self.data)
        self.plan = dict(backup_kind='whole-card-bytes', device='/dev/mmcblk0',
                         source=dict(device='/dev/mmcblk0', length_bytes=len(self.data)),
                         extent=dict(disk_bytes=len(self.data), offset_bytes=512, length_bytes=1024*1024+512))
        self.accepted = dict(status='verified-card-byte-backup', compressed_bytes=len(payload),
                             card=dict(bytes=len(self.data), sha256=hashlib.sha256(self.data).hexdigest()))
        self.save(source/'plan.json', self.plan)
        self.save(source/'acceptance.json', self.accepted)
        (source/'card.img.gz').write_bytes(payload)
        (source/'card.img.gz').chmod(0o600)
        return source

    def save(self, path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_ranges_independent_card_check_and_empty_suffix(self):
        for suffix in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = self.fixture(root, suffix=suffix)
                pulses = []
                result = capture(source, root/'hashes', heartbeat=lambda: pulses.append(True))
                self.assertEqual(result['status'], 'verified-backup-range-digests')
                self.assertTrue(pulses)
                for key in ('prefix', 'root', 'suffix'):
                    digest = result['digests'][key]
                    data = self.data[digest['offset']:digest['offset']+digest['bytes']]
                    self.assertEqual(hashlib.sha256(data).hexdigest(), digest['sha256'])
                self.assertEqual(result['digests']['card'], self.accepted['card'])
                for key in ('target_written', 'restore_authorized', 'normal_boot_release_authorized',
                            'filesystem_consistency_qualified'):
                    self.assertFalse(result[key])
                self.assertEqual(sorted(p.name for p in (root/'hashes').iterdir()), ['acceptance.json','plan.json'])
                with self.assertRaises(FileExistsError):
                    capture(source, root/'hashes')

    def test_bad_receipt_or_extent_rejected_before_output(self):
        for field, value in (('status','incomplete'),('card',dict(bytes=True,sha256='0'*64)),
                             ('compressed_bytes',True),('extent',dict(disk_bytes=512,offset_bytes=512,length_bytes=512))):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                source=self.fixture(root)
                record=self.plan if field=='extent' else self.accepted
                record[field]=value
                self.save(source/('plan.json' if field=='extent' else 'acceptance.json'),record)
                with self.assertRaises(ValueError): capture(source,root/'hashes')
                self.assertFalse((root/'hashes').exists())

    def test_failed_stream_retains_incomplete_evidence(self):
        for failure in ('hash','expansion','crc','heartbeat'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                source=self.fixture(root)
                if failure=='hash': self.accepted['card']['sha256']='0'*64
                if failure in ('expansion','crc'):
                    payload=bytearray(gzip.compress(self.data+(b'!' if failure=='expansion' else b'')))
                    if failure=='crc': payload[-8]^=1
                    (source/'card.img.gz').write_bytes(payload)
                    self.accepted['compressed_bytes']=len(payload)
                self.save(source/'acceptance.json',self.accepted)
                def pulse():
                    if failure=='heartbeat': raise RuntimeError('Lease lost')
                with self.assertRaises((ValueError,OSError,RuntimeError)):
                    capture(source,root/'hashes',heartbeat=pulse)
                result=json.loads((root/'hashes/acceptance.json').read_text())
                self.assertEqual(result['status'],'incomplete')
                self.assertNotIn('digests',result)

    def test_links_and_public_archive_rejected(self):
        for kind in ('symlink','hardlink','public'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                source=self.fixture(root)
                archive=source/'card.img.gz'
                if kind=='symlink':
                    archive.rename(source/'real.gz')
                    archive.symlink_to('real.gz')
                elif kind=='hardlink': os.link(archive,source/'other.gz')
                else: archive.chmod(0o644)
                with self.assertRaises(OSError): capture(source,root/'hashes')

    def test_source_mutation_and_path_replacement_rejected(self):
        for kind in ('metadata','receipt','replace'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                source=self.fixture(root)
                changed=False
                def pulse():
                    nonlocal changed
                    if changed: return
                    changed=True
                    archive=source/'card.img.gz'
                    if kind=='metadata':
                        info=archive.stat()
                        os.utime(archive,ns=(info.st_atime_ns,info.st_mtime_ns+1000000))
                    elif kind=='receipt':
                        self.save(source/'acceptance.json',dict(self.accepted,extra=True))
                    else:
                        archive.rename(source/'old.gz')
                        archive.write_bytes((source/'old.gz').read_bytes())
                        archive.chmod(0o600)
                with self.assertRaisesRegex(ValueError,'changed during hashing'):
                    capture(source,root/'hashes',heartbeat=pulse)


if __name__=='__main__': unittest.main()
