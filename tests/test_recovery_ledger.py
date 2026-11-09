import json
from pathlib import Path
import tempfile
import unittest
import multiprocessing
from unittest.mock import Mock

from forge_recovery_ledger import run, fence


def delayed_worker(directory, nonce, request, ready, proceed, results):
    ready.set()
    if not proceed.wait(10):
        raise RuntimeError('Test did not release worker')
    def effect():
        (Path(directory) / 'effect-happened').write_text('unexpected')
        return {'done': True}
    try:
        run(directory, nonce, request, effect)
    except RuntimeError as exc:
        results.put(str(exc))
    else:
        results.put('unexpected success')


class RecoveryLedgerTests(unittest.TestCase):
    def test_separate_delayed_process_cannot_publish_after_fence(self):
        context = multiprocessing.get_context('spawn')
        ready, proceed = context.Event(), context.Event()
        results = context.Queue()
        worker = context.Process(target=delayed_worker,
                                 args=(self.directory, self.nonce, self.request, ready, proceed, results))
        worker.start()
        try:
            self.assertTrue(ready.wait(10))
            self.assertEqual(fence(self.directory, self.nonce, self.request)['status'], 'fenced-not-started')
            proceed.set()
            self.assertIn('fenced before starting', results.get(timeout=10))
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
            self.assertFalse((self.directory / 'effect-happened').exists())
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
            results.close()
            results.join_thread()

    def test_fence_blocks_late_worker_and_is_repeatable(self):
        result = fence(self.directory, self.nonce, self.request)
        self.assertEqual(result['status'], 'fenced-not-started')
        self.assertEqual(fence(self.directory, self.nonce, self.request), result)
        effect = Mock()
        with self.assertRaises(RuntimeError):
            run(self.directory, self.nonce, self.request, effect)
        effect.assert_not_called()

    def test_fence_does_not_call_started_work_cancelled(self):
        def crash():
            raise RuntimeError('effect may have happened')
        with self.assertRaises(RuntimeError):
            run(self.directory, self.nonce, self.request, crash)
        self.assertEqual(fence(self.directory, self.nonce, self.request)['status'], 'incomplete')
        self.assertFalse((self.directory / ('fence-' + self.nonce + '.json')).exists())

    def test_fence_returns_completed_receipt_without_reversing(self):
        run(self.directory, self.nonce, self.request, lambda: {'done': True})
        result = fence(self.directory, self.nonce, self.request)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result'], {'done': True})

    def test_running_effect_cannot_be_fenced(self):
        def effect():
            with self.assertRaises(BlockingIOError):
                fence(self.directory, self.nonce, self.request)
            return {'done': True}
        run(self.directory, self.nonce, self.request, effect)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.nonce = 'a' * 32
        self.request = dict(plan_sha256='b'*64, direction='apply', nonce=self.nonce)

    def test_durable_receipt_replays_without_repeating_effect(self):
        def effect():
            intent = self.directory / ('intent-' + self.nonce + '.json')
            self.assertEqual(json.loads(intent.read_text()), self.request)
            return {'completed': True}
        self.assertEqual(run(self.directory, self.nonce, self.request, effect), {'completed': True})
        forbidden = Mock(side_effect=AssertionError('effect repeated'))
        self.assertEqual(run(self.directory, self.nonce, self.request, forbidden), {'completed': True})
        forbidden.assert_not_called()

    def test_partial_attempt_blocks_same_and_different_nonce(self):
        def crash():
            raise RuntimeError('after possible publication')
        with self.assertRaises(RuntimeError):
            run(self.directory, self.nonce, self.request, crash)
        for nonce in (self.nonce, 'c'*32):
            effect = Mock()
            with self.assertRaises(RuntimeError):
                run(self.directory, nonce, dict(self.request, nonce=nonce), effect)
            effect.assert_not_called()

    def test_nonce_reuse_for_other_request_is_rejected(self):
        run(self.directory, self.nonce, self.request, lambda: {'completed': True})
        effect = Mock()
        with self.assertRaises(ValueError):
            run(self.directory, self.nonce, dict(self.request, direction='restore'), effect)
        effect.assert_not_called()

    def test_invalid_receipt_cannot_be_replayed(self):
        run(self.directory, self.nonce, self.request, lambda: {'completed': True})
        receipt = self.directory / ('receipt-' + self.nonce + '.json')
        receipt.write_text('{}')
        with self.assertRaises(ValueError):
            run(self.directory, self.nonce, self.request, Mock())
        with self.assertRaises(ValueError):
            run(self.directory, 'c'*32, dict(self.request, nonce='c'*32), Mock())
