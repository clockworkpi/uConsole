"""Opt-in RAM recovery lease supervisor. Never invoke on the normal host."""
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time

from forge_ram_identity import verify
from forge_recovery_lease import Lease
from forge_recovery_lease_timer import RenewalService, SoftwareTimer
from forge_recovery_lease_watchdog import LinuxWatchdog, LeaseWatchdog, observe


DIRECTORY = Path('/run/forge-lease')


def binding(observed):
    tokens = observed['cmdline'].split()
    def option(name, pattern):
        values = [item[len(name)+1:] for item in tokens if item.startswith(name+'=')]
        if len(values) != 1 or not re.fullmatch(pattern, values[0]):
            raise ValueError('Missing or ambiguous recovery lease option')
        return values[0]
    nonce = option('uconsole.forge_trial', '[0-9a-f]{32}')
    owner = option('uconsole.recovery_owner', '[0-9a-f]{64}')
    option('uconsole.recovery_lease', '1')
    option('uconsole.recovery_watchdog', '1')
    mode = 'emulated' if 'uconsole.emulator=1' in tokens else 'physical'
    verify(observed, nonce, observed['kernel'], observed['serial'], mode=mode)
    return nonce, owner, mode


def reboot():
    report_failure('reboot',sys.exc_info()[1])
    subprocess.run(['/sbin/reboot', '-f'], check=False, timeout=10)
    raise RuntimeError('Recovery reboot returned')


def report_failure(role, error):
    """Bounded console reason codes survive reboot without exposing state."""
    reasons={'Recovery lease expired or clock reversed':'expired-or-clock-reversed',
             'Invalid deadline bounds':'invalid-bounds',
             'Invalid deadline file metadata':'unsafe-metadata',
             'Deadline changed while being read':'snapshot-changed',
             'Deadline reader clock reversed':'reader-clock-reversed',
             'Independent recovery timer exited':'timer-exited'}
    if role not in ('reboot','software','hardware'): role='unknown'
    message='Forge recovery lease failure: role='+role+' reason='+reasons.get(str(error),'other')+'\n'
    try:
        fd=os.open('/dev/console',os.O_WRONLY|os.O_NOCTTY|os.O_NONBLOCK)
        try: os.write(fd,message.encode('ascii'))
        finally: os.close(fd)
    except OSError:
        pass  # Diagnostics must never postpone expiry or reset.


def ready_file(path, record):
    with path.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())


def await_children(readers, timeout=10):
    deadline = time.monotonic() + timeout
    pending = set(readers)
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Recovery timer startup deadline')
        available, _, _ = select.select(list(pending), [], [], remaining)
        if not available:
            raise TimeoutError('Recovery timer startup deadline')
        for fd in available:
            if os.read(fd, 2) != b'R':
                raise RuntimeError('Recovery timer failed before readiness')
            pending.remove(fd)


def run_child(role, initial, observed, mode, write_fd):
    """Never let a timer child fall through into its parent's supervisor code."""
    consumer = None
    try:
        if role == 'software':
            consumer = SoftwareTimer(DIRECTORY, initial, reboot)
        elif role == 'hardware':
            device = LinuxWatchdog(initial, observed['kernel'], observed['serial'], mode=mode)
            consumer = LeaseWatchdog(DIRECTORY, initial, device)
        else:
            raise ValueError('Unknown recovery timer role')
        consumer.step()
        os.write(write_fd, b'R')
        os.close(write_fd)
        consumer.run()
    except BaseException as exc:
        report_failure(role,exc)
        raise
    finally:
        try:
            if consumer is not None:
                consumer.close()
        finally:
            # A return, exception, or cleanup error is terminal. No interpreter
            # teardown or parent finally blocks may unlink the shared endpoint.
            os._exit(1)


def main():
    observed = observe()
    nonce, owner, mode = binding(observed)  # No side effects before RAM identity check.
    initial = Lease.start(nonce, observed['boot_id'], owner, time.monotonic())
    os.umask(0o077)
    DIRECTORY.mkdir(mode=0o700)  # Exclusive startup; never adopt stale state.
    service = RenewalService(DIRECTORY, initial)
    children, readers = [], []
    try:
        for role in ('software', 'hardware'):
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                for inherited in readers:
                    os.close(inherited)
                # Drop inherited descriptors, but never unlink the parent's socket.
                service.endpoint.listener.close()
                service.writer.close()
                run_child(role, initial, observed, mode, write_fd)
            os.close(write_fd)
            readers.append(read_fd)
            children.append(pid)
        await_children(readers)
        for pid in children:
            if os.waitpid(pid, os.WNOHANG) != (0, 0):
                raise RuntimeError('Recovery timer exited during startup')
        ready_file(Path('/run/forge-lease.ready'), initial.receipt())
        ready_file(Path('/run/forge-watchdog.ready'),
                   dict(pid=children[1], maximum_lifetime=86400, lease=True))
        while True:
            for pid in children:
                if os.waitpid(pid, os.WNOHANG) != (0, 0):
                    raise RuntimeError('Independent recovery timer exited')
            service.poll()
    except BaseException:
        reboot()
        raise
    finally:
        for fd in readers:
            os.close(fd)
        service.close()


if __name__ == '__main__':
    main()
