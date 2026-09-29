import ast
import copy
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import Mock, patch

from forge_recovery_lease import Lease
from forge_recovery_session import Session, prepare
from forge_recovery_session_probe import run as probe_session
from validate_recovery_watchdog import run as validate


class RecoverySessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'session'
        self.boot = '11111111-2222-3333-4444-555555555555'
        self.owner = 'b'*64
        self.probe = Mock(host='fixture', port=2222, nonce='a'*32, kernel='6.12.62-v8+',
                          serial='100000007b961d25', mode='physical', _pins=('c'*64, 'd'*64))
        self.target = Lease.start(self.probe.nonce, self.boot, self.owner, 10)
        self.clock = 100
        self.commands = []
        self.lose_reply = False
        self.probe._observe.side_effect = self.observe
        self.record = prepare(self.directory, self.probe, self.boot, self.owner)
        self.pin = self.record['binding_sha256']

    def observe(self, command):
        source = shlex.split(command)[-1]
        request = ast.literal_eval(ast.parse(source).body[1].value)
        self.assertEqual(json.loads((self.directory/Session.names(request['sequence'])[0]).read_text()), request)
        self.commands.append(command)
        self.clock += 1
        self.target = self.target.renew(request, self.clock)
        if self.lose_reply:
            raise ConnectionError('injected lost reply after target renewal')
        return dict(receipt=self.target.receipt(), now=self.clock)

    def session(self, **changes):
        values = dict(directory=self.directory, pin=self.pin, probe=self.probe, boot_id=self.boot, owner=self.owner)
        values.update(changes)
        return Session(**values)

    def test_preparation_and_open_are_local_and_non_authorizing(self):
        self.assertEqual(self.record['status'], 'prepared-not-contacted')
        self.assertFalse(self.record['root_write_authorized'])
        self.assertFalse(self.record['normal_boot_release_authorized'])
        with self.session() as session:
            self.assertIsNone(session.accepted)
            self.assertIsNone(session.pending)
            self.assertIs(session.probe, self.probe)
        self.probe._observe.assert_not_called()
        self.probe.inspect.assert_not_called()

    def test_durable_intent_precedes_rpc_and_reopen_advances_only_acknowledged_sequence(self):
        with self.session() as session:
            first = session.renew()
            self.assertEqual(first['sequence'], 1)
            self.assertIs(session.probe, self.probe)
        with self.session() as session:
            self.assertEqual(session.accepted, first)
            second = session.renew(200)
            self.assertEqual(second['sequence'], 2)
        self.assertEqual(len(self.commands), 2)
        saved = json.loads((self.directory/Session.names(2)[1]).read_text())
        self.assertEqual(saved, dict(receipt=second, now=self.clock))

    def test_lost_reply_requires_explicit_exact_retry_after_restart(self):
        self.lose_reply = True
        with self.session() as session:
            with self.assertRaises(ConnectionError): session.renew(210)
            pending = copy.deepcopy(session.pending)
            with self.assertRaisesRegex(RuntimeError, 'explicit retry'): session.renew()
        self.assertEqual(len(self.commands), 1)
        before_deadline = self.target.deadline
        self.lose_reply = False
        with self.session() as session:
            self.assertEqual(session.pending, pending)
            with self.assertRaisesRegex(RuntimeError, 'explicit retry'): session.renew(210)
            accepted = session.retry_pending()
            self.assertIsNone(session.pending)
            self.assertEqual(accepted['deadline_monotonic'], before_deadline)
            with self.assertRaisesRegex(ValueError, 'No pending'): session.retry_pending()
        self.assertEqual(self.commands[0], self.commands[1])

    def test_reboot_postcheck_loss_never_records_acknowledgment(self):
        self.probe.inspect.side_effect = [None, ValueError('boot changed')]
        with self.session() as session:
            with self.assertRaisesRegex(ValueError, 'boot changed'): session.renew()
            self.assertTrue(session.unresolved)
            self.assertIsNone(session.accepted)
        self.assertFalse((self.directory/Session.names(1)[1]).exists())
        with self.session() as session:
            self.assertTrue(session.unresolved)
        self.assertEqual(len(self.commands), 1)

    def test_journal_lock_spans_owner_job_and_releases_on_close(self):
        with self.session():
            with self.assertRaises(BlockingIOError): self.session()
        with self.session(): pass
        self.probe._observe.assert_not_called()

    def test_different_pin_boot_owner_and_credential_binding_refused_without_target_contact(self):
        for changes in (dict(pin='0'*64), dict(boot_id='22222222-2222-3333-4444-555555555555'),
                        dict(owner='e'*64)):
            with self.assertRaises(ValueError): self.session(**changes)
        self.probe._pins = ('e'*64, 'd'*64)
        with self.assertRaises(ValueError): self.session()
        self.probe._observe.assert_not_called()

    def test_rejected_receipt_keeps_pending_not_accepted(self):
        original = self.probe._observe.side_effect
        def altered(command):
            value = original(command)
            value['receipt']['root_write_authorized'] = True
            return value
        self.probe._observe.side_effect = altered
        with self.session() as session:
            with self.assertRaises(ValueError): session.renew()
            self.assertIsNone(session.accepted)
            self.assertTrue(session.unresolved)
        self.assertFalse((self.directory/Session.names(1)[1]).exists())

    def test_intent_disk_failure_prevents_rpc_and_poisoned_reuse(self):
        with self.session() as session:
            with patch('forge_recovery_session.write_record', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): session.renew()
            with self.assertRaisesRegex(RuntimeError, 'journal failed'): session.renew()
        self.probe._observe.assert_not_called()

    def test_receipt_disk_failure_keeps_original_intent_for_explicit_reopen_retry(self):
        from forge_recovery_session import write_record
        def failing(fd, name, value):
            if name.endswith('-reply.json'): raise OSError('disk full')
            return write_record(fd, name, value)
        with self.session() as session:
            with patch('forge_recovery_session.write_record', side_effect=failing):
                with self.assertRaises(OSError): session.renew()
            self.assertIsNone(session.accepted)
            with self.assertRaisesRegex(RuntimeError, 'journal failed'): session.retry_pending()
        with self.session() as session:
            self.assertTrue(session.unresolved)
            self.assertEqual(session.retry_pending()['sequence'], 1)
        self.assertEqual(self.commands[0], self.commands[1])

    def test_unknown_or_gapped_record_refused(self):
        path = self.directory/'unexpected.json'
        path.write_text('{}')
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'gap or unexpected'): self.session()
        self.probe._observe.assert_not_called()

    def test_tampered_historical_receipt_refused(self):
        with self.session() as session: session.renew()
        path = self.directory/Session.names(1)[1]
        value = json.loads(path.read_text())
        value['receipt']['owner'] = 'e'*64
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError): self.session()
        self.assertEqual(len(self.commands), 1)

    def test_closed_session_cannot_renew(self):
        session = self.session()
        session.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'): session.renew()
        self.probe._observe.assert_not_called()

    def test_existing_advanced_target_sequence_is_not_adopted(self):
        for sequence in (1, 2):
            self.target = self.target.renew(dict(schema=1, purpose='offline-backup', nonce=self.probe.nonce,
                boot_id=self.boot, owner=self.owner, sequence=sequence, seconds=300), 20+sequence)
        with self.session() as session:
            with self.assertRaisesRegex(ValueError, 'out-of-order'): session.renew()
            self.assertEqual(session.pending['sequence'], 1)
            self.assertIsNone(session.accepted)
        self.assertEqual(len(self.commands), 1)

    def test_fault_probe_refuses_physical_target_before_any_action(self):
        with self.assertRaisesRegex(ValueError, 'disposable emulator'):
            probe_session(self.probe, None, self.boot, self.owner)
        self.probe._observe.assert_not_called()

    def test_vm_session_requires_explicit_lease_selection(self):
        for value in (True, 1, 'yes'):
            with self.assertRaisesRegex(ValueError, 'Durable session'):
                validate(None, None, None, None, None, None, None, durable_lease=value)


if __name__ == '__main__':
    unittest.main()
