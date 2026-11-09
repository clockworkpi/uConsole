import copy
import json
import unittest
from unittest.mock import patch

from forge_recovery_bootplan import digest
from forge_recovery_restore_host_protocol import Protocol
from forge_recovery_restore_protocol import completion
import test_recovery_restore_plan
from forge_recovery_restore_worker import Control


class RestoreHostProtocolTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.plan, self.manifest = fixture.compile(), fixture.manifest
        self.pin = digest(self.plan)
        self.protocol = Protocol(self.plan, self.pin, self.manifest, 'c'*32)

    def message(self, message_type, **fields):
        return dict(type=message_type, **self.protocol.wire, **fields)

    def lease(self):
        binding = self.plan['binding']
        return dict(schema=1, purpose='offline-backup', nonce=binding['nonce'],
                    boot_id=binding['boot_id'], owner=binding['lease_owner'],
                    sequence=self.protocol.lease_sequence+1, deadline_monotonic=300,
                    hard_deadline_monotonic=86400, root_write_authorized=False,
                    normal_boot_release_authorized=False)

    def control(self, phase, kind='approve'):
        self.assertEqual(self.protocol.consume(self.message('control-required', kind=kind,
            phase=phase, sequence=self.protocol.sequence+1)), 'control')
        return self.protocol.respond(self.lease())

    def ready(self):
        self.control('acquire')
        self.control('inspect')
        self.protocol.consume(self.message('unmounted'))
        self.control('write')

    def ack(self, index, written=None):
        chunk = self.manifest['chunks'][index]
        return self.message('chunk-verified', index=index, result=dict(status='verified-root-chunk',
            manifest_sha256=self.plan['source_manifest_sha256'], index=index, chunk=chunk,
            bytes_written=chunk['bytes'] if written is None else written,
            synchronized=True, normal_boot_release_authorized=False))

    def streamed(self):
        self.ready()
        for index in range(len(self.manifest['chunks'])):
            self.protocol.chunk_sent(index)
            self.protocol.consume(self.ack(index))
        self.protocol.source_finished(completion(self.manifest, self.plan['source_manifest_sha256']))

    def result(self):
        return dict(status='root-restore-verified', plan_sha256=self.pin,
            source_manifest_sha256=self.plan['source_manifest_sha256'], boot_id=self.plan['binding']['boot_id'],
            root=self.plan['root_after'], prefix=self.plan['prefix_guard'], suffix=self.plan['suffix_guard'],
            bytes_written=self.protocol.written, protected_ranges_verified=True, boot_unmounted=True,
            normal_boot_release_authorized=False, physical_restore_qualified=False)

    def close_input(self):
        size = self.plan['root_after']['bytes']
        self.protocol.consume(self.message('progress', range='restored-root', bytes=size, total=size))
        self.protocol.consume(self.message('close-input'))
        self.protocol.input_closed()

    def test_complete_requires_approvals_ordered_chunks_readback_and_half_close(self):
        self.streamed()
        self.control('verification', 'renew')
        self.close_input()
        self.assertEqual(self.protocol.consume(self.message('complete', result=self.result(),
                         historical_replay=False)), 'complete')
        self.assertEqual(self.protocol.result['root'], self.plan['root_after'])
        with self.assertRaises(RuntimeError): self.protocol.consume(self.message('close-input'))

    def test_no_chunks_before_write_approval(self):
        self.control('acquire')
        with self.assertRaises(ValueError): self.protocol.chunk_sent(0)
        self.assertTrue(self.protocol.failed)

    def test_mounted_phase_cannot_renew(self):
        self.control('acquire')
        self.control('inspect')
        with self.assertRaises(ValueError): self.control('verification', 'renew')

    def test_phase_skipping_and_foreign_attempt_are_terminal(self):
        with self.assertRaises(ValueError): self.control('write')
        self.protocol = Protocol(self.plan, self.pin, self.manifest, 'c'*32)
        message = self.message('control-required', kind='approve', phase='acquire', sequence=1)
        message['attempt'] = 'e'*32
        with self.assertRaises(ValueError): self.protocol.consume(message)

    def test_reply_must_be_acknowledged_before_worker_continues(self):
        self.protocol.consume(self.message('control-required', kind='approve', phase='acquire', sequence=1))
        with self.assertRaises(ValueError): self.protocol.consume(self.message('unmounted'))

    def test_renewal_receipt_cannot_change_owner_or_grant_write_authority(self):
        for field, value in (('owner', 'e'*64), ('root_write_authorized', True), ('sequence', True),
                             ('deadline_monotonic', float('nan'))):
            self.protocol = Protocol(self.plan, self.pin, self.manifest, 'c'*32)
            self.protocol.consume(self.message('control-required', kind='approve', phase='acquire', sequence=1))
            lease = dict(self.lease(), **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError): self.protocol.respond(lease)

    def test_next_chunk_cannot_overtake_previous_ack(self):
        self.ready()
        self.protocol.chunk_sent(0)
        with self.assertRaises(ValueError): self.protocol.chunk_sent(1)

    def test_chunk_receipt_must_match_source_digest_and_synchronized_write_size(self):
        for mutate in ('hash', 'size', 'sync', 'index'):
            self.protocol = Protocol(self.plan, self.pin, self.manifest, 'c'*32)
            self.ready()
            self.protocol.chunk_sent(0)
            ack = copy.deepcopy(self.ack(0))
            if mutate == 'hash': ack['result']['chunk']['sha256'] = '0'*64
            elif mutate == 'size': ack['result']['bytes_written'] = 512
            elif mutate == 'sync': ack['result']['synchronized'] = False
            else: ack['index'] = True
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.protocol.consume(ack)

    def test_early_source_completion_or_early_half_close_rejected(self):
        self.ready()
        with self.assertRaises(ValueError):
            self.protocol.source_finished(completion(self.manifest, self.plan['source_manifest_sha256']))
        self.protocol = Protocol(self.plan, self.pin, self.manifest, 'c'*32)
        self.streamed()
        with self.assertRaises(ValueError): self.protocol.consume(self.message('close-input'))

    def test_historical_or_wrong_byte_count_cannot_complete_fresh_attempt(self):
        for historical in (True, False):
            self.protocol = Protocol(self.plan, self.pin, self.manifest, 'c'*32)
            self.streamed()
            self.close_input()
            result = self.result()
            if not historical: result['bytes_written'] -= 512
            with self.assertRaises(ValueError):
                self.protocol.consume(self.message('complete', result=result, historical_replay=historical))

    def test_wrong_progress_geometry_and_unclosed_input_rejected(self):
        self.streamed()
        with self.assertRaises(ValueError):
            self.protocol.consume(self.message('progress', range='restored-root', bytes=1, total=1))
        self.protocol = Protocol(self.plan, self.pin, self.manifest, 'c'*32)
        self.streamed()
        with self.assertRaises(ValueError):
            self.protocol.consume(self.message('complete', result=self.result(), historical_replay=False))

    def test_actual_worker_hooks_interoperate_with_host_state_machine(self):
        outer = self
        class Channel:
            reply = b''
            def write(self, data):
                message = json.loads(bytes(data))
                action = outer.protocol.consume(message)
                if action == 'control':
                    # Synthetic owner/lease response only; real transport must
                    # durably authorize and renew over authenticated SSH.
                    self.reply = json.dumps(outer.protocol.respond(outer.lease())).encode()+b'\n'
                return len(data)
            def readline(self, limit):
                value, self.reply = self.reply, b''
                return value
            def flush(self): pass
        control = Control(Channel(), self.plan['binding'], self.protocol.wire)
        with patch('forge_recovery_restore_worker.budget', return_value=240):
            for phase in ('acquire', 'inspect'):
                control.approve(phase, self.pin, self.plan['binding'])
            control.unmounted()
            control.approve('write', self.pin, self.plan['binding'])
            for index in range(len(self.manifest['chunks'])):
                self.protocol.chunk_sent(index)
                control.acknowledge(self.ack(index))
            self.protocol.source_finished(completion(self.manifest, self.plan['source_manifest_sha256']))
            control.last_renewal -= 61
            size = self.plan['root_after']['bytes']
            control.progress('restored-root', size, size)
            control.finish_input()
            self.protocol.input_closed()
            self.protocol.consume(self.message('complete', result=self.result(), historical_replay=False))
        self.assertEqual(self.protocol.phase, 'complete')

    def test_pinned_root_manifest_mismatch_is_rejected(self):
        self.plan['root_after']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'source root differs'):
            Protocol(self.plan, digest(self.plan), self.manifest, 'c'*32)

    def test_input_mutation_does_not_change_expected_chunk_or_result(self):
        self.manifest['chunks'][0]['sha256'] = '0'*64
        self.plan['root_after']['sha256'] = '0'*64
        self.assertNotEqual(self.protocol.manifest['chunks'][0]['sha256'], '0'*64)
        self.assertNotEqual(self.protocol.plan['root_after']['sha256'], '0'*64)


if __name__ == '__main__': unittest.main()
