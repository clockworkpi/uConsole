"""Independent software expiry consumer for the recovery deadline protocol.

No reboot command is embedded: the verified RAM-only launcher must supply its
fixed reboot action. This module is not yet enabled in the initramfs.
"""
import time

from forge_recovery_deadline import DeadlineFile
from forge_recovery_lease import clock
from forge_recovery_lease_socket import LeaseEndpoint


class SoftwareTimer:
    def __init__(self, directory, initial, expire, *, now=time.monotonic):
        self.reader = DeadlineFile(directory, initial)
        self.expire = expire
        self.now = now
        self.previous = initial.started
        self.terminal = False

    def close(self):
        self.reader.close()

    def step(self):
        """Read shared state independently; any uncertainty ends the session."""
        if self.terminal:
            raise RuntimeError('Software recovery timer has expired')
        try:
            sampled = clock(self.now())
            if sampled < self.previous:
                raise ValueError('Timer clock reversed')
            self.previous = sampled
            # A concurrent publisher may sample a newer valid lease after our
            # initial clock read. Validate the snapshot against time sampled
            # after reading it, while still rejecting actual clock reversal.
            snapshot_time=sampled
            def snapshot_clock():
                nonlocal snapshot_time
                snapshot_time=clock(self.now())
                return snapshot_time
            lease = self.reader.read(sampled,finished=snapshot_clock)
            # Include file-read time; a slow read cannot grant extra lifetime.
            finished = clock(self.now())
            if finished < snapshot_time:
                raise ValueError('Timer clock reversed during read')
            lease.check(finished)
            self.previous = finished
            return min(0.2, lease.deadline - finished)
        except Exception:
            self.terminal = True
            self.expire()
            # If reboot returns, do not silently continue with a dead lease.
            raise RuntimeError('Recovery expiry action returned')

    def run(self, *, wait=time.sleep):
        """Run in a separate process from networking and the renewal endpoint."""
        while True:
            delay = self.step()
            try:
                wait(delay)
            except Exception:
                self.terminal = True
                self.expire()
                raise RuntimeError('Recovery expiry action returned after wait failure')


class RenewalService:
    """Compose exclusive publication and the authenticated local endpoint.

    The caller must start the independent software timer and watchdog consumer
    before advertising readiness. This service cannot create a new lease after
    a crash or renew a session without a request from the bound owner.
    """
    def __init__(self, directory, initial, *, now=time.monotonic):
        self.writer = DeadlineFile(directory, initial)
        self.endpoint = None
        self.now = now
        try:
            self.writer.publish(initial, now())
            from pathlib import Path
            self.endpoint = LeaseEndpoint(Path(directory)/'lease.sock', initial,
                                          self.publish, now=now)
        except BaseException:
            self.close()
            raise

    def publish(self, candidate):
        self.writer.publish(candidate, self.now())

    def poll(self):
        self.endpoint.poll()

    def close(self):
        if self.endpoint is not None:
            self.endpoint.close()
        self.writer.close()
