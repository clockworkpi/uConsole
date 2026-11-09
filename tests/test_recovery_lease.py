import unittest

from forge_recovery_lease import Lease


class RecoveryLeaseTests(unittest.TestCase):
    def setUp(self):
        self.lease = Lease.start('a'*32, '11111111-2222-3333-4444-555555555555', 'b'*64, 10)
        self.request = dict(schema=1, purpose='offline-backup', nonce=self.lease.nonce,
                            boot_id=self.lease.boot_id, owner=self.lease.owner,
                            sequence=1, seconds=300)

    def test_renew_and_duplicate_do_not_accumulate_time(self):
        renewed = self.lease.renew(self.request, 200)
        self.assertEqual(renewed.deadline, 500)
        duplicate = renewed.renew(self.request, 499)
        self.assertEqual(duplicate.deadline, 500)
        self.assertEqual(duplicate.sampled, 499)
        with self.assertRaises(ValueError):
            duplicate.renew(self.request, 500)
        self.assertEqual(self.lease.deadline, 310)

    def test_sequence_conflict_replay_and_gap(self):
        renewed = self.lease.renew(self.request, 200)
        for changes in ({'seconds': 299}, {'sequence': 0}, {'sequence': 3}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                renewed.renew(dict(self.request, **changes), 201)
        twice = renewed.renew(dict(self.request, sequence=2), 250)
        with self.assertRaises(ValueError):
            twice.renew(self.request, 251)

    def test_bound_identity_and_read_only_purpose(self):
        for key, value in [('schema', True), ('purpose', 'restore'), ('nonce', 'c'*32),
                           ('boot_id', '22222222-2222-3333-4444-555555555555'),
                           ('owner', 'c'*64), ('extra', 1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.lease.renew(dict(self.request, **{key: value}), 20)
        for key in self.request:
            request = self.request.copy()
            del request[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.lease.renew(request, 20)

    def test_bad_clock_or_duration_fails_closed(self):
        for now in (9, 310, 311, float('nan'), float('inf'), True, '20'):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.lease.renew(self.request, now)
        for key in ('seconds', 'sequence'):
            for value in (0, -1, True, 1.5, '1', float('nan'), 2**53):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.lease.renew(dict(self.request, **{key: value}), 20)
        with self.assertRaises(ValueError):
            self.lease.renew(dict(self.request, seconds=301), 20)

    def test_hard_cap_cannot_be_extended(self):
        lease = self.lease
        for sequence, now in enumerate(range(200, 86410, 200), 1):
            lease = lease.renew(dict(self.request, sequence=sequence), now)
        self.assertEqual(lease.deadline, 86410)
        with self.assertRaises(ValueError):
            lease.renew(dict(self.request, sequence=lease.sequence + 1), 86410)
        receipt = lease.receipt()
        self.assertFalse(receipt['root_write_authorized'])
        self.assertFalse(receipt['normal_boot_release_authorized'])

    def test_start_rejects_bad_binding_and_time(self):
        args = [self.lease.nonce, self.lease.boot_id, self.lease.owner, 10]
        for index, value in [(0, 'a'*31), (1, 'not-a-boot'), (2, 'b'*63),
                             (3, float('inf')), (3, 1e300), (3, -1), (3, True)]:
            bad = args.copy()
            bad[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                Lease.start(*bad)
