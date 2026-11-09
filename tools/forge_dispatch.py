"""Bounded handoff from local client threads to the owning GUI thread."""
from concurrent.futures import Future, TimeoutError
import queue
import threading


class OwnerQueue:
    def __init__(self, limit=32):
        self.pending = queue.Queue(maxsize=limit)
        self.mutex = threading.Lock()
        self.closed = False

    def call(self, function, timeout=10):
        future = Future()
        with self.mutex:
            if self.closed:
                raise ValueError('Workbench attachment is closing')
            try:
                self.pending.put_nowait((future, function))
            except queue.Full as exc:
                raise ValueError('Workbench request queue is full') from exc
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            if future.cancel():
                raise TimeoutError('Workbench did not accept the request; no operation started')
            # Once accepted, do not falsely report that nothing happened.
            return future.result()

    def drain(self):
        for _ in range(self.pending.maxsize):
            try:
                future, function = self.pending.get_nowait()
            except queue.Empty:
                return
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(function())
                except BaseException as exc:
                    future.set_exception(exc)

    def close(self):
        with self.mutex:
            self.closed = True
            while True:
                try:
                    future, _ = self.pending.get_nowait()
                except queue.Empty:
                    break
                if future.set_running_or_notify_cancel():
                    future.set_exception(ValueError('Workbench closed before accepting request'))
