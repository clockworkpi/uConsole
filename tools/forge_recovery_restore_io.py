"""Deadline-bounded binary pipes for a dedicated restore worker.

No authority, lease renewal, buffering of whole images, or automatic retry.
The worker owns these descriptors exclusively; do not mix buffered Python I/O
with this adapter. Leaving the context restores their original blocking flags
but does not close them. A failed operation permanently invalidates the adapter.
"""
import math
import os
import select
import stat
import time

BLOCK = 65536


class PipeIO:
    def __init__(self, input_fd, output_fd, *, timeout=82800, idle_timeout=90):
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 82800 or
                type(idle_timeout) not in (int, float) or not math.isfinite(idle_timeout) or
                not 0 < idle_timeout <= 180):
            raise ValueError('Invalid restore pipe deadlines')
        if (any(type(fd) is not int or fd < 0 for fd in (input_fd, output_fd)) or input_fd == output_fd):
            raise ValueError('Expected distinct worker pipe descriptors')
        for fd in (input_fd, output_fd):
            mode = os.fstat(fd).st_mode
            if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                raise ValueError('Restore transport requires pipes or sockets')
        self.input_fd, self.output_fd = input_fd, output_fd
        self.blocking = [os.get_blocking(fd) for fd in (input_fd, output_fd)]
        self.deadline = time.monotonic() + timeout
        self.idle_timeout = idle_timeout
        self.buffer = bytearray()
        self.failed = self.closed = self.eof = False
        os.set_blocking(input_fd, False)
        try:
            os.set_blocking(output_fd, False)
        except BaseException:
            os.set_blocking(input_fd, self.blocking[0])
            raise

    def __enter__(self):
        self.active()
        return self

    def __exit__(self, *exception):
        self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                os.set_blocking(self.input_fd, self.blocking[0])
            finally:
                os.set_blocking(self.output_fd, self.blocking[1])

    def active(self):
        if self.failed or self.closed:
            raise RuntimeError('Restore pipe is terminal; reconcile before retry')
        if time.monotonic() >= self.deadline:
            self.failed = True
            raise TimeoutError('Restore pipe total deadline')

    def wait(self, fd, *, writing, until):
        self.active()
        remaining = min(self.deadline, until) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Restore pipe progress deadline')
        readable, writable, _ = select.select([] if writing else [fd], [fd] if writing else [], [], remaining)
        self.active()
        if not (writable if writing else readable):
            raise TimeoutError('Restore pipe progress deadline')

    def pull(self, until):
        if self.eof:
            return b''
        while True:
            self.wait(self.input_fd, writing=False, until=until)
            try:
                data = os.read(self.input_fd, BLOCK)
            except BlockingIOError:
                continue
            self.eof = not data
            return data

    def read(self, size):
        self.active()
        try:
            if type(size) is not int or not 0 <= size <= BLOCK:
                raise ValueError('Restore pipe read must be bounded to one block')
            if size == 0:
                return b''
            if not self.buffer:
                self.buffer.extend(self.pull(time.monotonic() + self.idle_timeout))
            data = bytes(self.buffer[:size])
            del self.buffer[:size]
            return data
        except BaseException:
            self.failed = True
            raise

    def readline(self, limit):
        self.active()
        try:
            if type(limit) is not int or not 0 < limit <= BLOCK:
                raise ValueError('Restore pipe line must have an explicit bound')
            until = time.monotonic() + self.idle_timeout
            while True:
                newline = self.buffer.find(b'\n', 0, limit)
                if newline >= 0 or len(self.buffer) >= limit or self.eof:
                    size = newline + 1 if newline >= 0 else min(limit, len(self.buffer))
                    data = bytes(self.buffer[:size])
                    del self.buffer[:size]
                    return data
                data = self.pull(until)
                self.buffer.extend(data)
                # At most one prefetched block beyond the bounded header.
                if data:
                    until = time.monotonic() + self.idle_timeout
        except BaseException:
            self.failed = True
            raise

    def write(self, data):
        self.active()
        try:
            data = memoryview(data)
            if data.ndim != 1 or data.itemsize != 1:
                raise ValueError('Expected binary restore output')
            if not data:
                return 0
            until = time.monotonic() + self.idle_timeout
            while True:
                self.wait(self.output_fd, writing=True, until=until)
                try:
                    count = os.write(self.output_fd, data[:BLOCK])
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise OSError('Restore output made no progress')
                return count
        except BaseException:
            self.failed = True
            raise

    def flush(self):
        # All writes go directly to the descriptor; no userspace output buffer.
        self.active()
