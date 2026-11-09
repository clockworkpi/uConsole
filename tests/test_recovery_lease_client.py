import unittest
from unittest.mock import Mock

from forge_recovery_lease_client import BackupLeaseClient, LeasePulse, check_receipt


class LeaseClientTests(unittest.TestCase):
    def test_pulse_renews_at_bounded_intervals_and_propagates_loss(self):
        client = Mock()
        current = [0]
        pulse = LeasePulse(client, now=lambda: current[0])
        pulse()
        current[0] = 59
        pulse()
        self.assertEqual(client.renew.call_count, 1)
        current[0] = 60
        pulse()
        self.assertEqual(client.renew.call_count, 2)
        current[0] = 59
        with self.assertRaises(ValueError):
            pulse()
        current[0] = 120
        client.renew.side_effect = RuntimeError('lost')
        with self.assertRaises(RuntimeError):
            pulse()
    def setUp(self):
        self.probe = Mock(nonce='a'*32, kernel='6.12.62-v8+', serial='100000007b961d25', mode='physical')
        self.boot = '11111111-2222-3333-4444-555555555555'
        self.client = BackupLeaseClient(self.probe, self.boot, 'b'*64)
        self.request = dict(schema=1, purpose='offline-backup', nonce='a'*32,
                            boot_id=self.boot, owner='b'*64, sequence=1, seconds=300)
        self.receipt = {k: v for k, v in self.request.items() if k != 'seconds'}
        self.receipt.update(deadline_monotonic=400, hard_deadline_monotonic=86410,
                            root_write_authorized=False, normal_boot_release_authorized=False)
        self.result = dict(receipt=self.receipt, now=101)

    def test_lost_reply_retries_exact_command_and_sequence(self):
        self.probe._observe.side_effect = [RuntimeError('lost reply'), self.result]
        with self.assertRaises(RuntimeError):
            self.client.renew()
        first = self.probe._observe.call_args
        with self.assertRaises(ValueError):
            self.client.renew(200)
        self.assertEqual(self.client.renew(), self.receipt)
        self.assertEqual(self.probe._observe.call_args, first)
        self.assertIsNone(self.client.pending)

    def test_postcheck_failure_preserves_pending_request(self):
        self.probe._observe.return_value = self.result
        self.probe.inspect.side_effect = ValueError('rebooted')
        with self.assertRaises(ValueError):
            self.client.renew()
        self.assertEqual(self.client.pending, self.request)
        self.assertIsNone(self.client.accepted)

    def test_bad_binding_authority_and_deadlines_rejected(self):
        for changes in ({'sequence': True}, {'owner': 'c'*64}, {'root_write_authorized': True},
                        {'deadline_monotonic': 401.1}, {'deadline_monotonic': float('nan')},
                        {'hard_deadline_monotonic': 999999}, {'extra': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check_receipt(dict(self.result, receipt=dict(self.receipt, **changes)), self.request)

    def test_hard_deadline_cannot_reset(self):
        previous = dict(self.receipt, hard_deadline_monotonic=86400)
        with self.assertRaises(ValueError):
            check_receipt(self.result, self.request, previous)

    def test_next_acknowledged_request_advances_sequence(self):
        self.probe._observe.return_value = self.result
        self.client.renew()
        self.probe._observe.return_value = dict(self.result, receipt=dict(self.receipt, sequence=2))
        self.assertEqual(self.client.renew()['sequence'], 2)
