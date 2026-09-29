#!/usr/bin/env python3
"""Guest-local bounded command supervisor; no network listener or host execution.

Copied into a private temporary guest directory, not installed as a service.
Cancellation covers the launched process group, not services/daemons that
deliberately leave it. Already committed guest effects are never rolled back.
"""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

TAIL = 16384


def members(group):
    """Live group members, excluding zombies awaiting another parent's reap."""
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            # comm can contain spaces and ')'; fields after its last ')' begin
            # with state, ppid, pgrp. Do not split the complete stat line.
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] not in ('Z', 'X'):
                found.append(int(entry.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def signal_group(group, signum):
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        pass


def run(directory, timeout):
    directory = Path(directory)
    info = directory.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Job directory must be private and owned by the supervisor user')
    if not 0 < timeout <= 300:
        raise ValueError('Job timeout must be in (0, 300] seconds')
    environment = dict(os.environ, SYSTEMD_PAGER='cat', PAGER='cat', SYSTEMD_COLORS='0')
    child = subprocess.Popen(['/bin/bash', str(directory / 'script')],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True, env=environment)
    group = child.pid
    (directory / 'started.partial').write_text(str(group))
    (directory / 'started.partial').replace(directory / 'started')
    output = {'stdout': b'', 'stderr': b''}
    selector = selectors.DefaultSelector()
    for name, stream in (('stdout', child.stdout), ('stderr', child.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout
    reason = None
    stopping = None
    killed = False
    try:
        while True:
            for key, _ in selector.select(timeout=0.05):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    output[key.data] = (output[key.data] + chunk)[-TAIL:]
                else:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
            now = time.monotonic()
            # Keep the leader unreaped until group cleanup is complete, so
            # its PID/PGID cannot be recycled and signalled as another job.
            exited = os.waitid(os.P_PID, child.pid,
                               os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            if reason is None:
                if (directory / 'cancel').exists():
                    reason = 'cancelled'
                elif now >= deadline:
                    reason = 'timeout'
                elif exited:
                    reason = 'completed'
                if reason is not None:
                    # On ordinary shell exit, also stop background processes
                    # still in its group before acknowledging completion.
                    signal_group(group, signal.SIGTERM)
                    stopping = now
            if stopping is not None:
                if not members(group):
                    child.wait(timeout=1)
                    # Drain buffered output after writers close. Escaped
                    # daemons may retain a pipe; do not wait for them forever.
                    for key in list(selector.get_map().values()):
                        for _ in range(16):
                            try:
                                chunk = os.read(key.fileobj.fileno(), 65536)
                            except BlockingIOError:
                                break
                            if not chunk:
                                break
                            output[key.data] = (output[key.data] + chunk)[-TAIL:]
                    break
                if now - stopping >= 1 and not killed:
                    signal_group(group, signal.SIGKILL)
                    killed = True
                if now - stopping >= 6:
                    raise TimeoutError('Guest process group did not terminate; completion unknown')
        return {'status': reason, 'exit_code': 124 if reason == 'timeout' else child.returncode,
                'process_returncode': child.returncode, 'process_group_terminated': True,
                'timed_out': reason == 'timeout',
                **{name: data.decode(errors='replace') for name, data in output.items()}}
    finally:
        selector.close()
        for stream in (child.stdout, child.stderr):
            stream.close()


def main():
    directory = Path(sys.argv[1])
    try:
        result = run(directory, float(sys.argv[2]))
    except Exception as exc:
        result = {'status': 'uncertain', 'error': str(exc)}
    stage = directory / 'result.partial'
    stage.write_text(json.dumps(result))
    stage.replace(directory / 'result.json')


if __name__ == '__main__':
    main()
