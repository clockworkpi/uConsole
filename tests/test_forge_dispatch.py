"""GUI handoff never executes abandoned queued work or loses accepted results."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_dispatch import OwnerQueue


class OwnerQueueTests(unittest.TestCase):
    def test_expired_request_is_not_executed_later(self):
        queue = OwnerQueue()
        effects = []
        with self.assertRaisesRegex(TimeoutError, 'no operation started'):
            queue.call(lambda: effects.append('wrong'), timeout=0.01)
        queue.drain()
        self.assertEqual(effects, [])

    def test_close_wakes_pending_client_and_rejects_new_requests(self):
        queue = OwnerQueue()
        with ThreadPoolExecutor(1) as executor:
            # Observe actual enqueue without polling/sleeping.
            entered = threading.Event()
            put = queue.pending.put_nowait
            def enqueue(item):
                put(item)
                entered.set()
            queue.pending.put_nowait = enqueue
            client = executor.submit(queue.call, lambda: self.fail('closed request executed'))
            self.assertTrue(entered.wait(2))
            queue.close()
            with self.assertRaisesRegex(ValueError, 'before accepting'):
                client.result(timeout=2)
        with self.assertRaisesRegex(ValueError, 'closing'):
            queue.call(lambda: None)

    def test_operation_runs_only_on_owner_thread(self):
        queue = OwnerQueue()
        entered = threading.Event()
        put = queue.pending.put_nowait
        def enqueue(item):
            put(item)
            entered.set()
        queue.pending.put_nowait = enqueue
        owner_thread = threading.get_ident()
        with ThreadPoolExecutor(1) as executor:
            client = executor.submit(queue.call, threading.get_ident)
            self.assertTrue(entered.wait(2))
            queue.drain()
            self.assertEqual(client.result(timeout=2), owner_thread)


if __name__ == '__main__':
    unittest.main()
