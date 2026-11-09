import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from forge_recovery_filesystems import descriptor_directory, filesystem_tool, inspect_filesystems, partition_ranges, run_check
from forge_recovery_layout import root_extent


DESCRIPTOR_HOST = sys.platform.startswith('linux') or sys.platform == 'darwin'
HOST_CHECKERS = DESCRIPTOR_HOST and filesystem_tool('fsck.fat') and filesystem_tool('e2fsck')


class RecoveryFilesystemTests(unittest.TestCase):
    def test_descriptor_host_selection_and_unsupported_host_refusal(self):
        with patch('forge_recovery_filesystems.Path.is_dir', return_value=True):
            for platform, expected in (('linux', '/proc/self/fd'), ('darwin', '/dev/fd')):
                with patch('forge_recovery_filesystems.sys.platform', platform):
                    self.assertEqual(descriptor_directory(), expected)
            with patch('forge_recovery_filesystems.sys.platform', 'win32'):
                with self.assertRaises(RuntimeError): descriptor_directory()
        with patch('forge_recovery_filesystems.Path.is_dir', return_value=False):
            with self.assertRaises(RuntimeError): descriptor_directory()

    def test_homebrew_lookup_is_fixed_and_path_has_priority(self):
        with patch('forge_recovery_filesystems.sys.platform', 'darwin'), \
                patch('forge_recovery_filesystems.shutil.which', return_value='/owner/e2fsck'):
            self.assertEqual(filesystem_tool('e2fsck'), '/owner/e2fsck')
        with patch('forge_recovery_filesystems.sys.platform', 'darwin'), \
                patch('forge_recovery_filesystems.shutil.which', return_value=None), \
                patch('forge_recovery_filesystems.Path.is_file', return_value=True), \
                patch('forge_recovery_filesystems.os.access', return_value=True):
            self.assertEqual(filesystem_tool('e2fsck'), '/opt/homebrew/opt/e2fsprogs/sbin/e2fsck')
            with self.assertRaises(ValueError): filesystem_tool('../../unapproved')

    def test_invalid_heartbeat_rejected_before_access(self):
        with self.assertRaisesRegex(ValueError, 'heartbeat'):
            inspect_filesystems('/missing-image', '/missing-backup', '/missing-output', heartbeat=True)
        with self.assertRaisesRegex(ValueError, 'heartbeat'):
            run_check([], -1, heartbeat=True)

    @unittest.skipUnless(DESCRIPTOR_HOST, 'Linux or macOS descriptor path required')
    def test_heartbeat_failure_reaps_quiet_checker_even_after_stdout_eof(self):
        original = subprocess.Popen
        for closed in (False, True):
            with self.subTest(closed=closed), tempfile.TemporaryFile() as source:
                children, calls = [], []
                def launch(*args, **kwargs):
                    child = original(*args, **kwargs)
                    children.append(child)
                    return child
                def heartbeat():
                    calls.append(True)
                    if len(calls) == 4:
                        raise RuntimeError('Lease renewal failed')
                code = 'import os,time; '
                if closed:
                    code += 'os.close(1); os.close(2); '
                code += 'time.sleep(60)'
                with patch('forge_recovery_filesystems.subprocess.Popen', side_effect=launch):
                    with self.assertRaisesRegex(RuntimeError, 'Lease renewal'):
                        run_check([sys.executable, '-c', code], source.fileno(), heartbeat=heartbeat)
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stdout.closed)

    @unittest.skipUnless(HOST_CHECKERS, 'Filesystem checkers required')
    def test_copy_and_checker_share_heartbeat_and_failure_retains_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            image, backup, _, _, _ = self.fixture(directory)
            calls = []
            def heartbeat():
                calls.append(True)
            def check(argv, fd, **kwargs):
                self.assertIs(kwargs['heartbeat'], heartbeat)
                kwargs['heartbeat']()
                raise RuntimeError('Lease renewal failed')
            destination = Path(directory)/'checks'
            with patch('forge_recovery_filesystems.run_check', side_effect=check):
                with self.assertRaisesRegex(RuntimeError, 'Lease renewal'):
                    inspect_filesystems(image, backup, destination, heartbeat=heartbeat)
            self.assertGreaterEqual(len(calls), 3)
            result = json.loads((destination/'acceptance.json').read_text())
            self.assertEqual(result['status'], 'incomplete')
            self.assertFalse(result['restore_authorized'])

    @unittest.skipUnless(HOST_CHECKERS, 'Filesystem checkers required')
    def test_copy_heartbeat_failure_prevents_checkers(self):
        with tempfile.TemporaryDirectory() as directory:
            image, backup, _, _, _ = self.fixture(directory)
            destination = Path(directory)/'checks'
            def heartbeat():
                raise RuntimeError('Lease renewal failed')
            with patch('forge_recovery_filesystems.run_check') as check:
                with self.assertRaisesRegex(RuntimeError, 'Lease renewal'):
                    inspect_filesystems(image, backup, destination, heartbeat=heartbeat)
                check.assert_not_called()
            self.assertEqual(json.loads((destination/'acceptance.json').read_text())['status'], 'incomplete')

    @unittest.skipUnless(DESCRIPTOR_HOST, 'Linux or macOS descriptor path required')
    def test_heartbeat_time_does_not_extend_checker_deadline(self):
        clock, calls = [0.0], []
        def heartbeat():
            calls.append(True)
            if len(calls) == 2:
                clock[0] = 2.0
        with tempfile.TemporaryFile() as source:
            with patch('forge_recovery_filesystems.time.monotonic', side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(TimeoutError, 'deadline'):
                    run_check([sys.executable, '-c', 'import time; time.sleep(60)'],
                              source.fileno(), timeout=1, heartbeat=heartbeat)

    def fixture(self, directory, *, real=False):
        directory = Path(directory)
        image_dir, backup_dir = directory/'image', directory/'backup'
        image_dir.mkdir(mode=0o700)
        backup_dir.mkdir(mode=0o700)
        counts = (131072, 65536) if real else (8, 8)
        parts = [dict(number=1, start=2048, sectors=counts[0]),
                 dict(number=2, start=2048+counts[0], sectors=counts[1])]
        size = (parts[1]['start']+counts[1])*512
        header = bytearray(512)
        header[510:] = b'\x55\xaa'
        struct.pack_into('<I', header, 440, 0x21965b0c)
        for part, kind in zip(parts, (0x0c, 0x83)):
            offset = 446+(part['number']-1)*16
            header[offset+4] = kind
            struct.pack_into('<II', header, offset+8, part['start'], part['sectors'])
        image = image_dir/'image.img'
        with image.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(header)
            stream.truncate(size)
            if real:
                for part, command in zip(parts, ([filesystem_tool('mkfs.fat'), '-F', '32'],
                                                 [filesystem_tool('mke2fs'), '-q', '-t', 'ext4', '-F'])):
                    temp = directory / ('partition-'+str(part['number']))
                    with temp.open('xb') as output:
                        output.truncate(part['sectors']*512)
                    subprocess.run(command+[str(temp)], check=True, capture_output=True)
                    stream.seek(part['start']*512)
                    with temp.open('rb') as source:
                        shutil.copyfileobj(source, stream)
        digest = hashlib.sha256()
        with image.open('rb') as source:
            while data := source.read(1048576):
                digest.update(data)
        observed = dict(device='/dev/mmcblk0', cid='a'*32, sector_size=512,
                        sectors=size//512, bytes=size, partitions=parts,
                        mbr=base64.b64encode(header).decode())
        plan = dict(device='/dev/mmcblk0', cid='a'*32, disk_id='21965b0c',
                    backup_kind='whole-card-bytes',
                    extent=root_extent(observed, 'a'*32, '21965b0c'))
        expected = dict(bytes=size, sha256=digest.hexdigest())
        def record(path, value):
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        record(backup_dir/'plan.json', plan)
        record(backup_dir/'acceptance.json', dict(status='verified-card-byte-backup', card=expected))
        record(image_dir/'acceptance.json', dict(status='verified-host-image', image=expected,
                                               backup_kind='whole-card-bytes'))
        return image_dir, backup_dir, plan, bytes(header), size

    def test_ranges_are_bound_to_original_layout_and_header(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, plan, header, size = self.fixture(directory)
            ranges = partition_ranges(header, size, plan)
            self.assertEqual(ranges[0], dict(name='boot', offset=1048576, bytes=4096))
            for damaged in (header[:-1], header[:440]+b'\xff'+header[441:]):
                with self.assertRaises(ValueError):
                    partition_ranges(damaged, size, plan)
            plan['extent']['offset_bytes'] += 512
            with self.assertRaisesRegex(ValueError, 'differs'):
                partition_ranges(header, size, plan)

    @unittest.skipUnless(HOST_CHECKERS, 'Filesystem checkers required')
    def test_nonzero_check_is_not_qualification_or_restore_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            image, backup, _, _, _ = self.fixture(directory)
            calls = []
            def check(argv, fd, *, heartbeat=None):
                calls.append(argv)
                with self.assertRaises(OSError):
                    os.write(fd, b'forbidden')
                return dict(returncode=4, output='fixture inconsistent', argv=argv)
            destination = Path(directory)/'checks'
            with patch('forge_recovery_filesystems.run_check', side_effect=check):
                result = inspect_filesystems(image, backup, destination)
            self.assertEqual(result['status'], 'checked')
            self.assertEqual(len(calls), 2)
            self.assertTrue(all('-n' in argv for argv in calls))
            for key in ('filesystem_consistency_qualified', 'restore_authorized', 'target_written', 'repair_performed'):
                self.assertFalse(result[key])
            with self.assertRaises(FileExistsError):
                inspect_filesystems(image, backup, destination)

    @unittest.skipUnless(HOST_CHECKERS, 'Filesystem checkers required')
    def test_source_hash_failure_prevents_any_checker(self):
        with tempfile.TemporaryDirectory() as directory:
            image, backup, _, _, _ = self.fixture(directory)
            with (image/'image.img').open('r+b') as stream:
                stream.seek(512)
                stream.write(b'changed gap')
            destination = Path(directory)/'checks'
            with patch('forge_recovery_filesystems.run_check') as check:
                with self.assertRaisesRegex(ValueError, 'hash'):
                    inspect_filesystems(image, backup, destination)
                check.assert_not_called()
            self.assertEqual(json.loads((destination/'acceptance.json').read_text())['status'], 'incomplete')

    @unittest.skipUnless(HOST_CHECKERS, 'Filesystem checkers required')
    def test_actual_invalid_filesystems_are_not_qualified(self):
        with tempfile.TemporaryDirectory() as directory:
            image, backup, _, _, _ = self.fixture(directory)
            destination = Path(directory)/'checks'
            result = inspect_filesystems(image, backup, destination)
            self.assertFalse(result['filesystem_consistency_qualified'])
            self.assertTrue(all(code != 0 for code in result['check_returncodes'].values()))
            self.assertTrue((destination/'root-check.json').is_file())
            self.assertTrue((destination/'boot-check.json').is_file())

    @unittest.skipUnless(DESCRIPTOR_HOST, 'Linux or macOS descriptor path required')
    def test_checker_diagnostics_and_deadline_are_bounded(self):
        with tempfile.TemporaryFile() as source:
            fd = source.fileno()
            result = run_check([sys.executable, '-c', 'print("checked")'], fd)
            self.assertEqual(result['returncode'], 0)
            self.assertEqual(result['output'], 'checked\n')
            with self.assertRaisesRegex(ValueError, 'diagnostics'):
                run_check([sys.executable, '-c', 'print("x"*10000)'], fd, maximum=100)
            with self.assertRaises(TimeoutError):
                run_check([sys.executable, '-c', 'import time; time.sleep(10)'], fd, timeout=0.1)

    @unittest.skipUnless(HOST_CHECKERS and filesystem_tool('mkfs.fat') and filesystem_tool('mke2fs'),
                         'Filesystem creation and checking tools required')
    def test_actual_clean_fat32_and_ext4_check_without_mount_or_root(self):
        with tempfile.TemporaryDirectory() as directory:
            image, backup, _, _, _ = self.fixture(directory, real=True)
            before = (image/'image.img').stat()
            destination = Path(directory)/'checks'
            result = inspect_filesystems(image, backup, destination)
            self.assertTrue(result['filesystem_consistency_qualified'])
            self.assertEqual(result['check_returncodes'], dict(boot=0, root=0))
            self.assertFalse(result['restore_authorized'])
            self.assertEqual((image/'image.img').stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual((destination/'root.img').stat().st_mode & 0o777, 0o600)
