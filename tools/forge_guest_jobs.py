"""Host-side guest process-group jobs over short serialized shell transactions."""
import base64
import json
from pathlib import Path
import re
import shlex
import tempfile
import threading
import time
import uuid

from uconsole_agent import (GuestChannelUncertain, GuestTransferCancelled, guest_put,
                            require_success, serial_exec)


class GuestJobCancelled(Exception):
    """The guest acknowledged termination of the job's process group."""


def execute(port, script, timeout=60, cancel=None, on_started=None):
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 300:
        raise ValueError('Guest job timeout must be in (0, 300] seconds')
    cancel = cancel if cancel is not None else threading.Event()
    if cancel.is_set():
        raise GuestJobCancelled('Cancelled before guest job launch')
    marker = 'UC_JOB_DIRECTORY_' + uuid.uuid4().hex + ':'
    reply = require_success(serial_exec(port,
        'set -eu; test -x /usr/bin/python3; '
        'mountpoint -q /proc || mount -t proc proc /proc; '
        'd=$(mktemp -d /tmp/uconsole-forge-job.XXXXXXXXXXXX); '
        f'printf "\\n{marker}%s\\n" "$d"', timeout=10))['stdout']
    directories = [line.removeprefix(marker) for line in reply.splitlines() if line.startswith(marker)]
    if len(directories) != 1 or not re.fullmatch(r'/tmp/uconsole-forge-job\.[A-Za-z0-9]{12}', directories[0]):
        raise GuestChannelUncertain('Guest did not return a valid private job directory')
    directory = directories[0]
    certain = True
    launched = False
    try:
        guest_put(port, Path(__file__).with_name('forge_guest_worker.py'), directory + '/worker.py',
                  cancel=cancel)
        with tempfile.TemporaryDirectory(prefix='uc-job-source-') as host:
            source = Path(host) / 'script'
            source.write_text(script)
            guest_put(port, source, directory + '/script', cancel=cancel)
        if cancel.is_set():
            raise GuestJobCancelled('Cancelled before guest job launch')
        certain = False
        launched = True
        require_success(serial_exec(port,
            f'/usr/bin/python3 {directory}/worker.py {directory} {timeout} '
            f'</dev/null >{directory}/worker.log 2>&1 &', timeout=10))
        deadline = time.monotonic() + timeout + 20
        requested = False
        while time.monotonic() < deadline:
            if cancel.is_set() and not requested:
                require_success(serial_exec(port, f': > {directory}/cancel', timeout=10))
                requested = True
            reply = require_success(serial_exec(port,
                f'if [ -f {directory}/result.json ]; then '
                f'printf "UC_JOB_RESULT:%s\\n" "$(base64 {directory}/result.json | tr -d "\\n")"; '
                f'elif [ -f {directory}/started ]; then '
                f'printf "UC_JOB_RUNNING:%s\\n" "$(cat {directory}/started)"; fi',
                timeout=10))['stdout']
            # Kernel console messages can appear inside an acknowledged shell
            # response. Only consume our explicitly framed state line.
            running = next((line for line in reply.splitlines()
                            if line.startswith('UC_JOB_RUNNING:')), None)
            encoded = next((line.removeprefix('UC_JOB_RESULT:') for line in reply.splitlines()
                            if line.startswith('UC_JOB_RESULT:')), None)
            if running:
                group = running.removeprefix('UC_JOB_RUNNING:')
                if not group.isdecimal() or int(group) < 2:
                    raise GuestChannelUncertain('Guest supervisor returned an invalid process group')
                if on_started:
                    on_started({'guest_directory': directory, 'process_group': int(group)})
            if encoded:
                result = json.loads(base64.b64decode(encoded, validate=True))
                if not isinstance(result, dict) or result.get('process_group_terminated') is not True:
                    raise GuestChannelUncertain('Guest job did not confirm process-group termination')
                if result.get('status') not in ('completed', 'timeout', 'cancelled'):
                    raise GuestChannelUncertain('Guest job returned an invalid completion status')
                certain = True
                if result['status'] == 'cancelled':
                    raise GuestJobCancelled('Guest process group terminated; committed effects and '
                                            'services outside that group are not rolled back.')
                return result
            # This event wait responds promptly to a new cancellation request;
            # after sending it, pace polls rather than spinning on a set event.
            if requested:
                time.sleep(0.1)
            else:
                cancel.wait(0.1)
        raise GuestChannelUncertain('Guest job supervisor did not acknowledge completion before its deadline')
    except GuestChannelUncertain:
        certain = False
        raise
    except GuestJobCancelled:
        raise
    except GuestTransferCancelled as exc:
        raise GuestJobCancelled('Cancelled during guest job preparation; no job was launched.') from exc
    except (Exception, KeyboardInterrupt) as exc:
        if launched and not certain:
            raise GuestChannelUncertain('Guest job supervision failed; completion is unknown') from exc
        raise
    finally:
        if certain:
            # Explicit owned names only: no recursive removal of guest paths.
            files = ' '.join(shlex.quote(directory + '/' + name) for name in
                             ('worker.py', 'script', 'worker.log', 'started', 'started.partial', 'cancel',
                              'result.json', 'result.partial'))
            require_success(serial_exec(port, f'rm -f {files}; rmdir {directory}', timeout=10))
