import json
from pathlib import Path
import tempfile
import unittest

from forge_recovery_journal import prepare, dispatch, inspect_operation, reconcile, acknowledgement


class RecoveryJournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'journal'
        self.plan = dict(schema=2, kind='private-recovery-image', host='clockworkpi.local',
                         machine_id='a'*32, fstab_sha256='b'*64, boot_source='/dev/mmcblk0p1',
                         source='/private/recovery.img', destination='/boot/firmware/forge-recovery-'+'c'*32+'.img',
                         sha256='d'*64, size=123, stage_token='c'*32, preimage={'kind': 'absent'})
        self.digest = prepare(self.path, self.plan)['plan_sha256']

    def transport(self, plan, direction, nonce, digest):
        latest = sorted(self.path.glob('event-*.json'))[-1]
        self.assertEqual(json.loads(latest.read_text())['state'], 'dispatch')
        return dict(direction=direction, nonce=nonce, plan_sha256=digest, machine_id=plan['machine_id'],
                    sha256=plan['sha256'], size=plan['size'], status='published' if direction=='apply' else 'removed')

    def test_intent_precedes_transport_and_restore_requires_apply(self):
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'restore', self.digest, self.transport)
        dispatch(self.path, 'apply', self.digest, self.transport)
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'apply', self.digest, self.transport)
        dispatch(self.path, 'restore', self.digest, self.transport)
        self.assertEqual(len(list(self.path.glob('event-*.json'))), 4)

    def test_wrong_approval_never_dispatches(self):
        with self.assertRaises(PermissionError):
            dispatch(self.path, 'apply', '0'*64, self.transport)
        self.assertEqual(list(self.path.glob('event-*.json')), [])

    def test_legacy_plan_cannot_be_dispatched_or_newly_prepared(self):
        plan = dict(self.plan, schema=1)
        with self.assertRaises(ValueError):
            prepare(self.path.parent / 'legacy', plan)
        (self.path / 'plan.json').write_text(json.dumps(plan))
        with self.assertRaises(ValueError):
            dispatch(self.path, 'apply', self.digest, self.transport)
        with self.assertRaises(ValueError):
            reconcile(self.path, self.digest, self.transport)
        self.assertEqual(list(self.path.glob('event-*.json')), [])

    def test_lost_ack_blocks_both_retry_and_reverse(self):
        def lost(*args):
            raise TimeoutError('could already have published')
        with self.assertRaises(TimeoutError):
            dispatch(self.path, 'apply', self.digest, lost)
        for direction in ('apply', 'restore'):
            with self.assertRaises(RuntimeError):
                dispatch(self.path, direction, self.digest, self.transport)
        self.assertEqual(len(list(self.path.glob('event-*.json'))), 2)

    def test_wrong_nonce_is_uncertain(self):
        def mismatch(*args):
            result = self.transport(*args)
            result['nonce'] = 'f'*32
            return result
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'apply', self.digest, mismatch)
        self.assertEqual(json.loads((self.path/'event-000001.json').read_text())['state'], 'uncertain')

    def test_damaged_stored_ack_cannot_authorize_removal(self):
        dispatch(self.path, 'apply', self.digest, self.transport)
        path = self.path / 'event-000001.json'
        outcome = json.loads(path.read_text())
        del outcome['result']
        path.write_text(json.dumps(outcome))
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'restore', self.digest, self.transport)
        self.assertEqual(len(list(self.path.glob('event-*.json'))), 2)

    def test_crash_after_intent_prevents_redispatch(self):
        (self.path / 'event-000000.json').write_text(json.dumps(
            dict(state='dispatch', direction='apply', nonce='e'*32)))
        (self.path / 'event-000000.json').chmod(0o600)
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'apply', self.digest, self.transport)

    def test_inspection_does_not_clear_uncertain_history(self):
        def lost(*args):
            raise TimeoutError('lost')
        with self.assertRaises(TimeoutError):
            dispatch(self.path, 'apply', self.digest, lost)
        history = [p.read_bytes() for p in sorted(self.path.glob('event-*.json'))]
        def observation(plan, direction, nonce, digest):
            self.assertEqual(direction, 'inspect')
            return dict(kind='inspection', nonce=nonce, plan_sha256=digest, machine_id=plan['machine_id'],
                        boot_id='12345678-1234-1234-1234-123456789abc', policy={'valid': False},
                        files={'destination': {'state': 'absent'}, 'scratch': {'state': 'absent'}},
                        mutation_performed=False, retry_authorized=False)
        inspect_operation(self.path, self.digest, observation)
        self.assertEqual(history, [p.read_bytes() for p in sorted(self.path.glob('event-*.json'))])
        self.assertEqual(len(list(self.path.glob('inspection-*.json'))), 1)
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'apply', self.digest, self.transport)

    def lose_ack(self):
        with self.assertRaises(TimeoutError):
            dispatch(self.path, 'apply', self.digest, lambda *args: (_ for _ in ()).throw(TimeoutError()))

    def fence_transport(self, status):
        def reply(plan, direction, nonce, digest):
            self.assertEqual(direction, 'fence-apply')
            request = dict(plan_sha256=digest, direction='apply', nonce=nonce)
            self.assertIn(request, [json.loads(p.read_text()) for p in self.path.glob('fence-intent-*.json')])
            outcome = dict(status=status, request=request)
            if status == 'completed':
                outcome['result'] = acknowledgement(plan, 'apply', nonce, digest)
            return dict(kind='fence', nonce=nonce, plan_sha256=digest,
                        machine_id=plan['machine_id'], outcome=outcome)
        return reply

    def test_completed_receipt_allows_restore_preserving_history(self):
        self.lose_ack()
        before = [p.read_bytes() for p in sorted(self.path.glob('event-*.json'))]
        reconcile(self.path, self.digest, self.fence_transport('completed'))
        self.assertEqual(before, [p.read_bytes() for p in sorted(self.path.glob('event-*.json'))])
        with self.assertRaises(RuntimeError):
            reconcile(self.path, self.digest, self.fence_transport('completed'))
        dispatch(self.path, 'restore', self.digest, self.transport)

    def test_fenced_no_start_allows_new_apply(self):
        self.lose_ack()
        reconcile(self.path, self.digest, self.fence_transport('fenced-not-started'))
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'restore', self.digest, self.transport)
        dispatch(self.path, 'apply', self.digest, self.transport)

    def test_incomplete_remains_blocked(self):
        self.lose_ack()
        reconcile(self.path, self.digest, self.fence_transport('incomplete'))
        self.assertEqual(list(self.path.glob('resolution-*.json')), [])
        for direction in ('apply', 'restore'):
            with self.assertRaises(RuntimeError):
                dispatch(self.path, direction, self.digest, self.transport)

    def test_crash_only_dispatch_can_be_fenced(self):
        self.lose_ack()
        (self.path / 'event-000001.json').unlink()
        reconcile(self.path, self.digest, self.fence_transport('fenced-not-started'))
        dispatch(self.path, 'apply', self.digest, self.transport)

    def test_invalid_fence_response_cannot_resolve(self):
        self.lose_ack()
        for field in ('nonce', 'plan_sha256', 'machine_id', 'kind', 'outcome'):
            def bad(*args):
                result = self.fence_transport('completed')(*args)
                result[field] = 'wrong'
                return result
            with self.assertRaises(RuntimeError):
                reconcile(self.path, self.digest, bad)
        self.assertEqual(list(self.path.glob('resolution-*.json')), [])

    def test_corrupt_resolution_blocks_dispatch(self):
        self.lose_ack()
        reconcile(self.path, self.digest, self.fence_transport('completed'))
        path = next(self.path.glob('resolution-*.json'))
        result = json.loads(path.read_text())
        result['outcome']['result']['nonce'] = '0' * 32
        path.write_text(json.dumps(result))
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'restore', self.digest, self.transport)

    def test_fence_request_binding_and_receipt_are_exact(self):
        self.lose_ack()
        for field in ('nonce', 'direction', 'plan_sha256'):
            def bad(*args):
                result = self.fence_transport('completed')(*args)
                result['outcome']['request'][field] = 'wrong'
                return result
            with self.assertRaises(RuntimeError):
                reconcile(self.path, self.digest, bad)
        def wrong_receipt(*args):
            result = self.fence_transport('completed')(*args)
            result['outcome']['result']['sha256'] = '0' * 64
            return result
        with self.assertRaises(RuntimeError):
            reconcile(self.path, self.digest, wrong_receipt)
        with self.assertRaises(PermissionError):
            reconcile(self.path, '0' * 64, self.fence_transport('completed'))
        self.assertEqual(list(self.path.glob('resolution-*.json')), [])

    def test_fenced_restore_keeps_apply_state(self):
        dispatch(self.path, 'apply', self.digest, self.transport)
        def lost(*args):
            raise TimeoutError()
        with self.assertRaises(TimeoutError):
            dispatch(self.path, 'restore', self.digest, lost)
        def fence_restore(plan, direction, nonce, digest):
            self.assertEqual(direction, 'fence-restore')
            return dict(kind='fence', nonce=nonce, plan_sha256=digest, machine_id=plan['machine_id'],
                        outcome=dict(status='fenced-not-started', request=dict(
                            plan_sha256=digest, direction='restore', nonce=nonce)))
        reconcile(self.path, self.digest, fence_restore)
        with self.assertRaises(RuntimeError):
            dispatch(self.path, 'apply', self.digest, self.transport)
        dispatch(self.path, 'restore', self.digest, self.transport)
