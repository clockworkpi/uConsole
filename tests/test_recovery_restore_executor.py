from contextlib import contextmanager, nullcontext
import copy
import hashlib
import io
import os
import unittest
from unittest.mock import Mock, patch

from forge_recovery_bootplan import digest
from forge_recovery_claim import RootClaim
import forge_recovery_restore_executor as executor
from forge_recovery_restore_plan import INPUT_NAMES
from forge_recovery_restore_protocol import Sender
from forge_recovery_restore_source import stream
import test_recovery_restore_plan


class RestoreExecutorTests(unittest.TestCase):
    """Real stream/writer/range hashes/ledger; mocked hardware-only boundaries."""
    def setUp(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.attempt = '9'*32
        self.before = b'0'*len(fixture.source.root_bytes)
        fixture.hashes['digests']['root']['sha256'] = hashlib.sha256(self.before).hexdigest()
        fixture.prefix['sha256'] = hashlib.sha256(fixture.source.data[:fixture.prefix['bytes']]).hexdigest()
        fixture.hashes['digests']['prefix'] = copy.deepcopy(fixture.prefix)
        fixture.hold_pin = digest(fixture.hold)
        fixture.inspection.update(prefix=copy.deepcopy(fixture.prefix), plan_sha256=fixture.hold_pin)
        self.plan = fixture.compile()
        self.pin = digest(self.plan)
        self.inputs = dict(zip(INPUT_NAMES, fixture.arguments()))
        self.path = fixture.source.root/'executor-root.img'
        self.path.write_bytes(self.before)
        self.fd = os.open(self.path, os.O_RDWR)
        self.addCleanup(os.close, self.fd)
        self.card = fixture.source.root/'executor-card.img'
        self.card.write_bytes(fixture.source.data)
        self.card_fd = os.open(self.card, os.O_RDONLY)
        self.addCleanup(os.close, self.card_fd)
        self.events = []
        self.boot = self.plan['binding']['boot_id']
        self.ledger_root = fixture.source.root/'ledger'
        self.ledger_dir = self.ledger_root/self.boot
        self.ledger_dir.mkdir(parents=True, mode=0o700)
        self.check = Mock(return_value=self.plan['binding']['extent'])
        self.budget = Mock(return_value=240)
        self.inspect = Mock(return_value={key: fixture.inspection[key]
                            for key in ('status', 'files', 'image', 'stage')})
        self.acks = []
        for name, value in (
            ('checker', Mock(return_value=self.check)),
            ('ensure_lock_directory', Mock()),
            ('target_lock', self.lock),
            ('claim_restore_root', self.claim),
            ('mounted_boot', self.mount),
            ('commit_timer', lambda seconds: nullcontext()),
            ('inspect_files', self.inspect)):
            patcher = patch.object(executor, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (('ROOT', self.ledger_root), ('check_boot', Mock()),
                            ('provision', Mock(return_value=self.ledger_dir))):
            patcher = patch.object(executor.ledger, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def lock(self, path):
        self.events.append('locked')
        try:
            yield
        finally:
            self.events.append('unlocked')

    @contextmanager
    def mount(self, device, token, *, read_only):
        self.assertEqual(device, '/dev/mmcblk0p1')
        self.assertTrue(read_only)
        self.events.append('mounted')
        try:
            yield '/fixture-boot'
        finally:
            self.events.append('unmounted')

    @contextmanager
    def claim(self, device, extent, guards, *, pin, boot_id, check, authorize, progress):
        self.assertIn('locked', self.events)
        self.assertEqual(authorize(pin, boot_id), dict(restore=pin, boot_id=boot_id))
        claim = RootClaim(self.fd, self.card_fd, extent)
        claim.verify_guards(guards)
        self.events.append('claimed')
        try:
            yield claim
            claim.verify_guards({key: guards[key] for key in ('prefix', 'suffix')})
            self.events.append('protected-verified')
        finally:
            claim.active = False
            self.events.append('released')

    def approve(self, phase, pin, binding):
        self.events.append('approve-'+phase)
        return dict(restore=pin, boot_id=binding['boot_id'], phase=phase)

    def wire(self):
        output = io.BytesIO()
        sender = Sender(output, self.fixture.manifest, self.fixture.source_pin,
                        dict(plan_sha256=self.pin, manifest_sha256=self.fixture.source_pin,
                             attempt=self.attempt, boot_id=self.boot))
        sender.finish(stream(self.fixture.source.source, self.fixture.manifest,
                             self.fixture.source_pin, sender.chunk))
        return output.getvalue()

    def run_restore(self, wire=None, approve=None, **hooks):
        source = hooks.pop('source') if 'source' in hooks else io.BytesIO(self.wire() if wire is None else wire)
        return executor.execute(self.plan, self.pin, self.inputs, self.attempt,
                                source,
                                approve=approve or self.approve, live_budget=self.budget,
                                acknowledge=self.acks.append,
                                unmounted=lambda: self.events.append('renewal-permitted'), **hooks)

    def test_stream_restores_real_file_and_receipt_follows_protected_checks(self):
        result = self.run_restore()
        self.assertEqual(self.path.read_bytes(), self.fixture.source.root_bytes)
        self.assertEqual(result['root'], self.plan['root_after'])
        self.assertTrue(result['protected_ranges_verified'])
        self.assertFalse(result['normal_boot_release_authorized'])
        self.assertFalse(result['physical_restore_qualified'])
        self.assertEqual(self.events, ['locked', 'approve-acquire', 'claimed', 'approve-inspect',
                         'mounted', 'unmounted', 'renewal-permitted', 'approve-write',
                         'protected-verified', 'released', 'unlocked'])
        self.assertEqual(len(self.acks), 2)

    def test_changed_evidence_is_rejected_before_lock_or_claim(self):
        self.inputs['ram_boot_observation']['bootloader']['tryboot'] = 1
        with self.assertRaises(ValueError): self.run_restore()
        self.assertEqual(self.events, [])
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_conflicting_hold_unmounts_without_root_write_and_fences_retry(self):
        self.inspect.return_value = dict(self.inspect.return_value, image='conflict')
        with self.assertRaisesRegex(ValueError, 'hold dependencies'): self.run_restore()
        self.assertIn('unmounted', self.events)
        self.assertNotIn('approve-write', self.events)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.inspect.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'incomplete'): self.run_restore()
        self.inspect.assert_not_called()

    def test_source_disconnect_retains_partial_bytes_and_no_successful_retry(self):
        wire = self.wire()
        end = wire.index(b'\n')+1+self.fixture.manifest['chunks'][0]['bytes']
        with self.assertRaises(ValueError): self.run_restore(wire[:end])
        self.assertEqual(len(self.acks), 1)
        self.assertNotEqual(self.path.read_bytes(), self.before)
        self.assertNotIn('protected-verified', self.events)
        with self.assertRaisesRegex(RuntimeError, 'incomplete'): self.run_restore()

    def test_missing_owner_write_approval_prevents_chunks(self):
        def approve(phase, pin, binding):
            return {} if phase == 'write' else self.approve(phase, pin, binding)
        with self.assertRaisesRegex(ValueError, 'owner restore approval'): self.run_restore(approve=approve)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.acks, [])

    def test_expired_lease_before_first_chunk_prevents_write(self):
        self.budget.side_effect = [240, 240, 240, 0]
        with self.assertRaisesRegex(ValueError, 'budget'): self.run_restore()
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.acks, [])

    def test_prefix_corruption_after_chunk_blocks_completion(self):
        self.acks = Mock()
        receipts = []
        def corrupt(value):
            receipts.append(value)
            with self.card.open('r+b') as output: output.write(b'bad')
        self.acks.append = corrupt
        with self.assertRaisesRegex(ValueError, 'Protected digest'): self.run_restore()
        self.assertEqual(len(receipts), 2)
        self.assertNotIn('protected-verified', self.events)
        self.assertFalse((self.ledger_dir/('receipt-'+self.attempt+'.json')).exists())

    def test_changed_identity_before_lock_never_creates_intent(self):
        self.check.return_value = {}
        with self.assertRaisesRegex(ValueError, 'identity or layout'): self.run_restore()
        self.assertEqual(self.events, [])
        self.assertEqual(list(self.ledger_dir.iterdir()), [])

    def test_approval_cannot_mutate_frozen_binding_or_plan(self):
        def approve(phase, pin, binding):
            response = self.approve(phase, pin, binding)
            binding['extent']['length_bytes'] = 512
            self.plan['root_after']['sha256'] = '0'*64
            return response
        result = self.run_restore(approve=approve)
        self.assertEqual(result['root'], self.fixture.source.expected)
        self.assertEqual(self.path.read_bytes(), self.fixture.source.root_bytes)

    def test_bad_inspection_budget_never_mounts_or_writes(self):
        self.budget.side_effect = [240, 179]
        with self.assertRaisesRegex(ValueError, 'budget'): self.run_restore()
        self.assertNotIn('mounted', self.events)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_completed_replay_is_historical_and_does_not_repeat_writes(self):
        result = self.run_restore()
        self.path.write_bytes(self.before)
        self.inspect.reset_mock()
        replay = self.run_restore()
        self.assertEqual(replay, result)
        self.inspect.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertFalse(replay['normal_boot_release_authorized'])

    def test_control_channel_remains_open_through_readback_and_protected_checks(self):
        source = io.BytesIO(self.wire()+b'{"renewed":true}\n')
        renewed = []
        def progress(name, done, total):
            if name == 'restored-root' and not renewed:
                self.assertEqual(source.readline(4096), b'{"renewed":true}\n')
                renewed.append(True)
        def finish_input():
            self.assertEqual(renewed, [True])
            self.assertIn('protected-verified', self.events)
            self.assertIn('released', self.events)
            self.events.append('input-close-requested')
        result = self.run_restore(source=source, progress=progress, finish_input=finish_input)
        self.assertEqual(result['status'], 'root-restore-verified')
        self.assertEqual(self.events[-2:], ['input-close-requested', 'unlocked'])

    def test_deferred_eof_still_rejects_trailing_bytes_without_receipt(self):
        with self.assertRaisesRegex(ValueError, 'control-channel completion'):
            self.run_restore(wire=self.wire()+b'!', finish_input=lambda: None)
        self.assertIn('protected-verified', self.events)
        self.assertFalse((self.ledger_dir/('receipt-'+self.attempt+'.json')).exists())
        with self.assertRaisesRegex(RuntimeError, 'incomplete'): self.run_restore()

    def test_missing_final_half_close_never_journals_success(self):
        def finish_input():
            raise TimeoutError('Host did not close input')
        with self.assertRaises(TimeoutError): self.run_restore(finish_input=finish_input)
        self.assertIn('protected-verified', self.events)
        self.assertFalse((self.ledger_dir/('receipt-'+self.attempt+'.json')).exists())


if __name__ == '__main__': unittest.main()
