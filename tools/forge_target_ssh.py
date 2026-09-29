"""Journaled SSH file transitions. Not service/package or whole-image deployment."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import uuid

import forge_target_journal as journal
from forge_target_files import apply_file


class TargetUncertain(RuntimeError):
    """Target may have changed; reconcile the same direction before reversing."""


@contextmanager
def target_lock(lock_path):
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise PermissionError('Unsafe target transaction lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def perform(plan, direction, nonce, *, lock_path='/run/lock/uconsole-forge-target.lock', guard=None):
    journal.validate_plan(plan)
    if direction not in ('apply', 'restore'):
        raise ValueError('Invalid transition direction')
    identity = Path('/etc/machine-id').read_text().strip()
    if identity != plan['before']['machine_id']:
        raise ValueError('SSH target machine identity differs from backup')
    with target_lock(lock_path):
        if guard is not None: guard()
        before, after = plan['before']['files'], plan['after']['files']
        if direction == 'restore':
            before, after = after, before
        results = [apply_file(old, new, stage_token=token) for old, new, token in
                   zip(before, after, plan['stage_tokens'][direction])]
        if guard is not None: guard()
        return {'nonce': nonce, 'direction': direction, 'machine_id': identity, 'files': results}


# Source modules come from the installed owner code, never the client workspace.
# stdin is used so file contents do not appear in process arguments or logs.
BOOTSTRAP = '''import json, sys, types
request = json.load(sys.stdin)
for name, source in request['modules']:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, '<forge-owner:' + name + '>', 'exec'), module.__dict__)
from forge_target_ssh import perform
print(json.dumps(perform(request['plan'], request['direction'], request['nonce'])))
'''


def payload(plan, direction, nonce):
    root = Path(__file__).resolve().parent
    modules = [(name, (root / (name + '.py')).read_text()) for name in
               ('forge_target_backup', 'forge_target_files', 'forge_target_journal', 'forge_target_ssh')]
    return json.dumps({'modules': modules, 'plan': plan, 'direction': direction, 'nonce': nonce})


def dispatch(directory, direction, *, timeout=60, approved_plan_sha256=None):
    if direction not in ('apply', 'restore'):
        raise ValueError('Choose apply or restore')
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError('SSH transaction timeout must be 1..300 seconds')
    with journal.locked(directory) as (fd, plan):
        digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        if approved_plan_sha256 is not None and digest != approved_plan_sha256:
            raise PermissionError('Target plan differs from the owner-approved digest')
        history = journal.events(fd)
        if history and history[-1]['state'] != 'acknowledged' and history[-1]['direction'] != direction:
            raise TargetUncertain('Reconcile the pending direction before reversing the transaction')
        nonce = uuid.uuid4().hex
        data = payload(plan, direction, nonce)
        event = {'state': 'dispatch', 'direction': direction, 'nonce': nonce}
        journal.append_event(fd, event)  # Must be durable before starting SSH.
        try:
            result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                                     plan['host'], 'sudo -n python3 -c ' + shlex.quote(BOOTSTRAP)],
                                    input=data, text=True, capture_output=True, timeout=timeout)
            if result.returncode:
                raise TargetUncertain('SSH transition did not acknowledge success: ' + result.stderr[-2000:])
            observed = json.loads(result.stdout)
            paths = [item['path'] for item in plan['before']['files']]
            if (observed.get('nonce') != nonce or observed.get('direction') != direction or
                    observed.get('machine_id') != plan['before']['machine_id'] or
                    [item['path'] for item in observed.get('files', [])] != paths or
                    any(item.get('status') not in ('applied', 'already-applied') for item in observed['files'])):
                raise TargetUncertain('Invalid target acknowledgement')
            journal.append_event(fd, dict(event, state='acknowledged', result=observed))
            return observed
        except BaseException as exc:
            # Never claim rollback, including timeout, cancellation or host IO
            # failure after target publication. The preceding dispatch survives.
            journal.append_event(fd, dict(event, state='uncertain', error=str(exc)))
            raise TargetUncertain('Target completion uncertain; retain journal and reconcile: ' + str(exc)) from exc


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('direction', choices=('apply', 'restore'))
    cli.add_argument('--journal', type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(dispatch(args.journal, args.direction)))


if __name__ == '__main__':
    main()
