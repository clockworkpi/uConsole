import io
import json
import unittest
from unittest.mock import patch

import forge_recovery_restore_worker as worker


class Duplex(io.BytesIO):
    def __init__(self, data=b''):
        super().__init__(data)
        self.output = io.BytesIO()

    def write(self, data):
        return self.output.write(data)


class RestoreWorkerTests(unittest.TestCase):
    def setUp(self):
        self.binding = dict(boot_id='01234567-1234-1234-1234-0123456789ab', lease_owner='d'*64)
        self.wire = dict(plan_sha256='a'*64, manifest_sha256='b'*64,
                         boot_id=self.binding['boot_id'], attempt='c'*32)
        patcher = patch.object(worker, 'budget', return_value=240)
        self.budget = patcher.start()
        self.addCleanup(patcher.stop)

    def reply(self, kind='approve', phase='acquire', sequence=1, lease_sequence=1):
        value = dict(type='control-accepted', **self.wire, kind=kind, phase=phase,
                     sequence=sequence, lease=dict(sequence=lease_sequence))
        if kind == 'approve': value['restore'] = self.wire['plan_sha256']
        return value

    def control(self, *replies):
        channel = Duplex(b''.join(json.dumps(value).encode()+b'\n' for value in replies))
        return worker.Control(channel, self.binding, self.wire), channel

    def approve(self, control, phase='acquire'):
        return control.approve(phase, self.wire['plan_sha256'], self.binding)

    def test_approval_checks_local_lease_before_accepting_remote_acknowledgement(self):
        control, channel = self.control(self.reply())
        result = self.approve(control)
        self.assertEqual(result['restore'], self.wire['plan_sha256'])
        self.budget.assert_called_once_with(self.binding, 'd'*64, {'sequence': 1})
        message = json.loads(channel.output.getvalue())
        self.assertEqual(message['type'], 'control-required')
        self.assertEqual(message['sequence'], 1)

    def test_cross_attempt_or_unapproved_ack_is_terminal(self):
        for key, value in (('attempt', 'e'*32), ('restore', 'f'*64), ('sequence', True), ('phase', 'write')):
            reply = dict(self.reply(), **{key: value})
            control, _ = self.control(reply)
            with self.subTest(key=key), self.assertRaises(ValueError): self.approve(control)
            self.assertTrue(control.failed)
            with self.assertRaises(RuntimeError): self.approve(control)
        self.budget.assert_not_called()

    def test_local_liveness_rejection_does_not_adopt_remote_receipt(self):
        self.budget.side_effect = ValueError('Local lease differs')
        control, _ = self.control(self.reply())
        with self.assertRaises(ValueError): self.approve(control)
        self.assertIsNone(control.accepted)
        self.assertTrue(control.failed)

    def test_inspection_suspends_renewal_until_unmounted(self):
        control, channel = self.control(self.reply(phase='inspect'))
        self.approve(control, 'inspect')
        before = channel.output.getvalue()
        with self.assertRaises(RuntimeError): control.exchange('renew', 'verification')
        self.assertEqual(channel.output.getvalue(), before)
        self.assertEqual(control.live_budget(), 240)
        control.unmounted()
        self.assertFalse(control.mounted)
        self.assertIn(b'"type": "unmounted"', channel.output.getvalue())

    def test_long_readback_progress_renews_on_same_control_channel(self):
        control, channel = self.control(self.reply(), self.reply('renew', 'verification', 2, 2))
        self.approve(control)
        control.last_renewal -= 61
        control.progress('restored-root', 64*1024*1024, 128*1024*1024)
        messages = [json.loads(line) for line in channel.output.getvalue().splitlines()]
        self.assertEqual([value['type'] for value in messages], ['control-required', 'control-required', 'progress'])
        self.assertEqual(control.accepted['sequence'], 2)

    def test_stale_renewal_does_not_extend_worker_state(self):
        control, _ = self.control(self.reply(), self.reply('renew', 'verification', 2, 1))
        self.approve(control)
        with self.assertRaisesRegex(ValueError, 'fresh acknowledged'):
            control.exchange('renew', 'verification')
        self.assertTrue(control.failed)
        self.assertEqual(self.budget.call_count, 1)

    def test_close_input_prevents_late_progress_or_control_exchange(self):
        control, channel = self.control(self.reply())
        self.approve(control)
        control.finish_input()
        self.assertIn(b'"type": "close-input"', channel.output.getvalue())
        with self.assertRaises(RuntimeError): control.progress('root', 1, 2)
        with self.assertRaises(RuntimeError): control.live_budget()

    def test_unexpected_unmount_or_changed_approval_binding_rejected(self):
        control, channel = self.control()
        with self.assertRaises(RuntimeError): control.unmounted()
        with self.assertRaises(ValueError): control.approve('write', 'f'*64, self.binding)
        self.assertEqual(channel.output.getvalue(), b'')

    def test_run_does_not_emit_completion_when_executor_fails(self):
        request = dict(protocol=1, plan=dict(binding=self.binding, source_manifest_sha256='b'*64),
                       pin='a'*64, inputs={}, attempt='c'*32)
        channel = Duplex()
        with patch.object(worker, 'execute', side_effect=ValueError('uncertain root')):
            with self.assertRaises(ValueError): worker.run(request, channel)
        self.assertEqual(channel.output.getvalue(), b'')

    def test_run_marks_ledger_replay_as_historical(self):
        request = dict(protocol=1, plan=dict(binding=self.binding, source_manifest_sha256='b'*64),
                       pin='a'*64, inputs={}, attempt='c'*32)
        channel = Duplex()
        with patch.object(worker, 'execute', return_value={'status': 'retained-receipt'}):
            worker.run(request, channel)
        self.assertTrue(json.loads(channel.output.getvalue())['historical_replay'])


if __name__ == '__main__': unittest.main()
