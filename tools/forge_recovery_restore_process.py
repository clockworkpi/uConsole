"""Bounded local process/SSH pipes for the installed-owner restore transport.

No command construction, retry, journal creation or target authorization. The
caller supplies an authenticated argv and private diagnostic sink. A socket
stdin permits half-close without invalidating the descriptor's lifecycle.
"""
import math
import os
import select
import socket
import subprocess
import sys
import time

from forge_recovery_restore_io import PipeIO

MAX_ERRORS = 65536
MAX_OUTPUT = 64*1024*1024


class ProcessChannel(PipeIO):
    def __init__(self, process, outgoing, errors, **deadlines):
        super().__init__(process.stdout.fileno(), outgoing.fileno(), **deadlines)
        self.process, self.outgoing, self.errors = process, outgoing, errors
        self.error_fd = process.stderr.fileno()
        self.error_blocking = os.get_blocking(self.error_fd)
        self.error_eof = False
        self.error_bytes = self.output_bytes = 0
        self.output_closed = False
        try:
            os.set_blocking(self.error_fd, False)
        except BaseException:
            super().close()
            raise

    def close(self):
        if not self.closed:
            try:
                os.set_blocking(self.error_fd, self.error_blocking)
            finally:
                super().close()

    def drain_error(self):
        try:
            data = os.read(self.error_fd, 65536)
        except BlockingIOError:
            return
        if not data:
            self.error_eof = True
            return
        allowed = MAX_ERRORS-self.error_bytes
        if allowed:
            saved = data[:allowed]
            count = self.errors.write(saved)
            if count != len(saved): raise OSError('Incomplete restore diagnostic journal write')
            self.error_bytes += len(saved)
        if len(data) > allowed:
            raise ValueError('Restore worker diagnostics exceed bound')

    def wait(self, fd, *, writing, until):
        while True:
            self.active()
            remaining = min(self.deadline, until)-time.monotonic()
            if remaining <= 0: raise TimeoutError('Restore process progress deadline')
            reads = ([] if writing else [fd]) + ([] if self.error_eof else [self.error_fd])
            readable, writable, _ = select.select(reads, [fd] if writing else [], [], remaining)
            self.active()
            if self.error_fd in readable: self.drain_error()
            if fd in (writable if writing else readable): return
            # Diagnostic output never resets the protocol's progress deadline.

    def pull(self, until):
        data = super().pull(until)
        self.output_bytes += len(data)
        if self.output_bytes > MAX_OUTPUT:
            raise ValueError('Restore worker protocol output exceeds bound')
        return data

    def write(self, data):
        if self.output_closed: raise RuntimeError('Restore process input already half-closed')
        return super().write(data)

    def half_close(self):
        self.active()
        if self.output_closed: raise RuntimeError('Restore process input already half-closed')
        self.outgoing.shutdown(socket.SHUT_WR)
        self.output_closed = True

    def wait_success(self):
        self.active()
        if not self.eof: raise ValueError('Restore worker output EOF must precede exit acceptance')
        until = min(self.deadline, time.monotonic()+self.idle_timeout)
        while not self.error_eof:
            self.active()
            remaining = until-time.monotonic()
            if remaining <= 0: raise TimeoutError('Restore diagnostic EOF deadline')
            ready, _, _ = select.select([self.error_fd], [], [], remaining)
            if ready: self.drain_error()
        remaining = until-time.monotonic()
        if remaining <= 0: raise TimeoutError('Restore process exit deadline')
        if self.process.wait(timeout=remaining):
            raise RuntimeError('Restore worker/SSH exited unsuccessfully; completion uncertain')
        self.errors.flush()


def run(argv, operation, errors, *, timeout=82800, idle_timeout=90):
    """Own one child through EOF/status verification or bounded termination.

    operation(channel, half_close, wait_success) normally invokes the restore
    exchange. errors must be a private host journal stream. Its final fsync and
    the overall attempt's durable completion belong to the dispatch layer.
    """
    if (not isinstance(argv, (list, tuple)) or not argv or
            any(not isinstance(arg, str) or not arg or '\0' in arg for arg in argv) or
            not callable(operation) or not callable(getattr(errors, 'write', None)) or
            not callable(getattr(errors, 'flush', None)) or
            type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 82800 or
            type(idle_timeout) not in (int, float) or not math.isfinite(idle_timeout) or
            not 0 < idle_timeout <= 180):
        raise ValueError('Invalid bounded restore process request')
    started = time.monotonic()
    host, child = socket.socketpair()
    process = None
    try:
        process = subprocess.Popen(argv, stdin=child, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        child.close()
        remaining = timeout-(time.monotonic()-started)
        if remaining <= 0: raise TimeoutError('Restore process startup deadline')
        with ProcessChannel(process, host, errors, timeout=remaining, idle_timeout=idle_timeout) as channel:
            result = operation(channel, channel.half_close, channel.wait_success)
            channel.wait_success()  # Do not trust a callback to skip exit verification.
            return result
    finally:
        failing = sys.exc_info()[0] is not None
        host.close()
        child.close()
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            finally:
                process.stdout.close()
                process.stderr.close()
        try:
            errors.flush()
        except BaseException:
            if not failing: raise
