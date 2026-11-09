from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

from forge_recovery_deadline import DeadlineFile, validate
from forge_recovery_lease import Lease


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).resolve()
        self.initial = Lease.start('a'*32, '11111111-2222-3333-4444-555555555555', 'b'*64, 10)
        self.writer = DeadlineFile(self.path, self.initial)
        self.reader = DeadlineFile(self.path, self.initial)
        self.addCleanup(self.writer.close)
        self.addCleanup(self.reader.close)
        self.request = dict(schema=1, purpose='offline-backup', nonce=self.initial.nonce,
                            boot_id=self.initial.boot_id, owner=self.initial.owner,
                            sequence=1, seconds=300)

    def test_atomic_initial_and_renewal(self):
        self.writer.publish(self.initial, 20)
        self.assertEqual(self.reader.read(20), self.initial)
        renewed = self.initial.renew(self.request, 200)
        self.writer.publish(renewed, 201)
        self.assertEqual(self.reader.read(202), renewed)
        self.assertEqual(list(self.path.iterdir()), [self.path/'deadline.json'])
        with self.assertRaises(ValueError):
            self.reader.read(500)

    def test_cannot_reinitialize_existing_deadline(self):
        self.writer.publish(self.initial, 20)
        with self.assertRaises(FileExistsError):
            self.reader.publish(self.initial, 21)
        self.assertEqual(self.reader.read(22), self.initial)

    def test_replacement_and_symlink_fail_closed(self):
        self.writer.publish(self.initial, 20)
        path = self.path/'deadline.json'
        path.write_text('{}')
        with self.assertRaises(ValueError):
            self.writer.publish(self.initial, 21)
        with self.assertRaises(ValueError):
            self.reader.read(21)
        path.unlink()
        path.symlink_to(self.path/'missing')
        with self.assertRaises(OSError):
            self.reader.read(21)

    def test_bad_permissions_fail_closed(self):
        self.writer.publish(self.initial, 20)
        (self.path/'deadline.json').chmod(0o644)
        with self.assertRaises(ValueError):
            self.reader.read(21)

    def test_wrong_binding_and_invalid_bounds(self):
        renewed = self.initial.renew(self.request, 200)
        for changes in ({'owner': 'c'*64}, {'deadline': 501}, {'sampled': 301},
                        {'sampled': 310}, {'sequence': True}, {'deadline': 309},
                        {'sequence': 0}, {'started': 11}, {'last_seconds': 301}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(self.initial, replace(renewed, **changes), 300)

    def test_duplicate_cannot_change_deadline(self):
        renewed = self.initial.renew(self.request, 200)
        with self.assertRaises(ValueError):
            validate(renewed, replace(renewed, deadline=501, sampled=201), 202)
        with self.assertRaises(ValueError):
            validate(renewed, self.initial, 202)

    def test_nonfinite_or_untyped_times_rejected(self):
        for field in ('started', 'sampled', 'deadline'):
            for value in (float('nan'), float('inf'), True, -1, '10'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate(self.initial, replace(self.initial, **{field: value}), 20)

    def test_reader_accepts_open_snapshot_unlinked_by_atomic_renewal(self):
        self.writer.publish(self.initial,20)
        renewed=self.initial.renew(self.request,200)
        original=os.open
        replaced=False
        def opening(path,*args,**kwargs):
            nonlocal replaced
            fd=original(path,*args,**kwargs)
            if path=='deadline.json' and not replaced:
                replaced=True
                self.writer.publish(renewed,201)
            return fd
        with patch('forge_recovery_deadline.os.open',side_effect=opening):
            self.assertEqual(self.reader.read(202),self.initial)
        self.assertEqual(self.reader.read(202),renewed)

    def test_atomic_replacement_during_read_is_not_in_place_mutation(self):
        self.writer.publish(self.initial,20)
        renewed=self.initial.renew(self.request,200)
        original=os.read
        replaced=False
        def reading(fd,length):
            nonlocal replaced
            if not replaced:
                replaced=True
                self.writer.publish(renewed,201)
            return original(fd,length)
        with patch('forge_recovery_deadline.os.read',side_effect=reading):
            self.assertEqual(self.reader.read(202),self.initial)
        self.assertEqual(self.reader.read(202),renewed)

    def test_in_place_permission_change_during_read_still_rejected(self):
        self.writer.publish(self.initial,20)
        original=os.read
        def reading(fd,length):
            value=original(fd,length)
            (self.path/'deadline.json').chmod(0o644)
            return value
        with patch('forge_recovery_deadline.os.read',side_effect=reading),self.assertRaises(ValueError):
            self.reader.read(202)
        self.assertEqual(self.reader.state,self.initial)
