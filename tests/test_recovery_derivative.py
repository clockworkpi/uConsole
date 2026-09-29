import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from forge_recovery_archive import fingerprint
from forge_recovery_derivative import prepare
from forge_recovery_restore_source import CHUNK_BYTES, digest, validate
import test_recovery_restore_source


class RecoveryDerivativeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_restore_source.RestoreSourceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.baseline, self.original = self.fixture.prepared()
        self.image = self.fixture.root/'enhanced.img'
        self.image.write_bytes(self.fixture.data)
        self.image.chmod(0o600)
        self.output = self.fixture.root/'derivative'

    def run_prepare(self, **kwargs):
        return prepare(self.fixture.source, self.original, digest(self.original),
                       self.image, self.output, **kwargs)

    def read(self, name):
        return json.loads((self.output/name).read_text())

    def change(self, offset, value=b'!'):
        with self.image.open('r+b') as stream:
            stream.seek(offset)
            stream.write(value)

    def test_root_changes_have_distinct_lineage_and_no_deployment_authority(self):
        self.change(512+CHUNK_BYTES)
        archive = (self.fixture.source/'card.img.gz').read_bytes()
        result = self.run_prepare()
        manifest = self.read('manifest.json')
        self.assertEqual(result['status'], 'verified-root-only-derivative')
        self.assertEqual(result['manifest_sha256'], digest(manifest))
        self.assertEqual(manifest['changed_chunks'], [1])
        self.assertEqual(manifest['original_manifest_sha256'], digest(self.original))
        self.assertEqual(manifest['original_card'], self.original['card'])
        self.assertEqual(manifest['original_root'], self.original['root'])
        self.assertEqual(manifest['chunks'][0], self.original['chunks'][0])
        self.assertEqual(manifest['card']['sha256'], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(manifest['root']['sha256'], hashlib.sha256(self.image.read_bytes()[512:-512]).hexdigest())
        self.assertEqual((self.fixture.source/'card.img.gz').read_bytes(), archive)
        for field in ('target_write_authorized', 'normal_boot_release_authorized',
                      'filesystem_consistency_qualified', 'native_boot_qualified'):
            self.assertIs(manifest[field], False)
        # This new evidence cannot silently masquerade as an original backup.
        with self.assertRaises(ValueError):
            validate(manifest, digest(manifest))
        self.assertEqual((self.output/'manifest.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            self.run_prepare()

    def test_unchanged_derivative_is_explicitly_identical(self):
        result = self.run_prepare()
        manifest = self.read('manifest.json')
        self.assertEqual(result['changed_chunks'], 0)
        self.assertEqual(manifest['card'], manifest['original_card'])
        self.assertEqual(manifest['root'], manifest['original_root'])
        self.assertEqual(manifest['prefix']['sha256'], hashlib.sha256(b'p'*512).hexdigest())
        self.assertEqual(manifest['suffix']['sha256'], hashlib.sha256(b's'*512).hexdigest())

    def test_both_protected_ranges_are_rejected(self):
        for offset in (0, len(self.fixture.data)-1):
            with self.subTest(offset=offset):
                self.image.write_bytes(self.fixture.data)
                self.change(offset)
                self.output = self.fixture.root/('bad-'+str(offset))
                with self.assertRaisesRegex(ValueError, 'protected'):
                    self.run_prepare()
                self.assertEqual(self.read('acceptance.json')['status'], 'incomplete')
                self.assertFalse((self.output/'manifest.json').exists())

    def test_private_owned_single_link_fixed_size_image_required(self):
        for kind in ('public', 'hardlink', 'symlink', 'size'):
            with self.subTest(kind=kind):
                image = self.fixture.root/kind
                image.write_bytes(self.fixture.data)
                image.chmod(0o600)
                if kind == 'public':
                    image.chmod(0o644)
                elif kind == 'hardlink':
                    os.link(image, self.fixture.root/'second-link')
                elif kind == 'symlink':
                    image.unlink()
                    image.symlink_to(self.image)
                else:
                    with image.open('ab') as stream:
                        stream.write(b'x')
                output = self.fixture.root/('output-'+kind)
                with self.assertRaises(OSError):
                    prepare(self.fixture.source, self.original, digest(self.original), image, output)
                self.assertFalse(output.exists())

    def test_manifest_or_archive_identity_change_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, 'pinned digest'):
            prepare(self.fixture.source, self.original, '0'*64, self.image, self.output)
        self.assertFalse(self.output.exists())
        path = self.fixture.source/'card.img.gz'
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns+1000000))
        with self.assertRaisesRegex(ValueError, 'Original backup differs'):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_original_chunk_and_full_digest_are_verified(self):
        for kind in ('chunk', 'root', 'card'):
            with self.subTest(kind=kind):
                original = copy.deepcopy(self.original)
                if kind == 'chunk':
                    original['chunks'][0]['sha256'] = '0'*64
                else:
                    original[kind]['sha256'] = '0'*64
                output = self.fixture.root/('invalid-'+kind)
                with self.assertRaises(ValueError):
                    prepare(self.fixture.source, original, digest(original), self.image, output)
                self.assertFalse((output/'manifest.json').exists())

    def test_gzip_crc_is_verified_even_with_repinned_archive_metadata(self):
        path = self.fixture.source/'card.img.gz'
        data = bytearray(path.read_bytes())
        data[-8] ^= 1
        path.write_bytes(data)
        self.original['archive_fingerprint'] = list(fingerprint(path.stat()))
        with self.assertRaises(gzip.BadGzipFile):
            self.run_prepare()
        self.assertEqual(self.read('acceptance.json')['status'], 'incomplete')
        self.assertFalse((self.output/'manifest.json').exists())

    def test_heartbeat_failure_never_publishes_success(self):
        def fail():
            raise RuntimeError('lease failed')
        with self.assertRaisesRegex(RuntimeError, 'lease failed'):
            self.run_prepare(heartbeat=fail)
        self.assertEqual(self.read('acceptance.json')['status'], 'incomplete')
        self.assertFalse((self.output/'manifest.json').exists())

    def test_final_heartbeat_cannot_change_image_or_original_receipt(self):
        for kind in ('image', 'receipt'):
            with self.subTest(kind=kind):
                self.output = self.fixture.root/('mutated-'+kind)
                calls = []
                def heartbeat():
                    calls.append(True)
                    if len(calls) != 5:
                        return
                    if kind == 'image':
                        info = self.image.stat()
                        os.utime(self.image, ns=(info.st_atime_ns, info.st_mtime_ns+1000000))
                    else:
                        self.fixture.save(self.fixture.source/'acceptance.json',
                                          dict(self.fixture.accepted, unexpected=True))
                with self.assertRaisesRegex(ValueError, 'changed during'):
                    self.run_prepare(heartbeat=heartbeat)
                self.assertEqual(self.read('acceptance.json')['status'], 'incomplete')
                self.assertFalse((self.output/'manifest.json').exists())

    def test_derivative_descriptor_is_read_only(self):
        pread = os.pread
        def checked(fd, *args):
            with self.assertRaises(OSError):
                os.write(fd, b'!')
            return pread(fd, *args)
        with patch('forge_recovery_derivative.os.pread', side_effect=checked):
            self.run_prepare()

    def test_invalid_heartbeat_precedes_all_access(self):
        with self.assertRaisesRegex(ValueError, 'heartbeat'):
            prepare('/missing', {}, '', '/missing', '/missing', heartbeat=True)
