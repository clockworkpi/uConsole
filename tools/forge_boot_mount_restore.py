"""Final owner reboot and independently verified original boot permissions."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import uuid

from forge_boot_privacy import verify_mount
from forge_boot_privacy_restore import validate_privacy, applied
from forge_boot_observation import normal_boot, verify as verify_boot
from forge_recovery_bootplan import digest
from forge_recovery_cleanup import inputs as cleanup_inputs
from forge_recovery_stage_dispatch import MODULES
from forge_target_journal import locked, events, private_directory, read_record, write_record


def load(fd, directory, plan, pin):
    attempt = read_record(fd, 'privacy-restore-attempt.json')
    frozen = attempt['request']
    clean = frozen['cleanup']
    if (frozen['journal'] != str(directory) or frozen['plan'] != plan or frozen['plan_sha256'] != pin or
            cleanup_inputs(clean['staging'], clean['staging_sha256'], clean['boot_id']) != clean):
        raise ValueError('Original policy restoration differs from its pinned journals')
    validate_privacy(plan, pin, clean)
    history = events(fd)
    if len(history) != 4: raise ValueError('Require acknowledged policy restore; inspect uncertain attempts separately')
    applied(history[:2], plan)
    observed = read_record(fd, 'privacy-restore-observation.json')
    ack = observed['acknowledgement']
    if (history[2] != dict(state='dispatch', direction='restore', nonce=attempt['nonce']) or
            history[3] != dict(history[2], state='acknowledged', result=ack) or
            ack.get('nonce') != attempt['nonce'] or ack.get('direction') != 'restore' or
            ack.get('machine_id') != clean['machine_id'] or
            [v.get('path') for v in ack.get('files', [])] != ['/etc/fstab'] or
            any(v.get('status') not in ('applied', 'already-applied') for v in ack['files']) or
            observed.get('boot_id') != clean['boot_id'] or not isinstance(observed.get('inventory'), list)):
        raise ValueError('Original policy restore acknowledgement differs')
    expected = dict(status='original-fstab-restored-awaiting-fresh-mount', plan_sha256=pin,
        boot_id=clean['boot_id'], private_mount_retained=True, original_mount_verified=False,
        reboot_performed=False, automatic_retry_performed=False, inventory_sha256=digest(observed['inventory']))
    if digest(read_record(fd, 'privacy-restore-acceptance.json')) != digest(expected):
        raise ValueError('Original policy restore receipt differs')
    return dict(frozen, inventory=observed['inventory'])


def inputs(directory, pin):
    directory = Path(directory).absolute()
    with locked(directory) as (fd, plan): return load(fd, directory, plan, pin)


BOOTSTRAP = '''import json,sys,types
r=json.load(sys.stdin)
for name,source in r.pop('modules'):
    m=types.ModuleType(name);sys.modules[name]=m
    exec(compile(source,'<original-mount:'+name+'>','exec'),m.__dict__)
from forge_boot_mount_worker import run
print(json.dumps(run(r)))
'''


def transport(frozen, operation):
    if operation not in ('reboot', 'inspect'): raise ValueError('Unsupported final mount operation')
    base = Path(__file__).parent
    names = (*MODULES, 'forge_boot_artifacts', 'forge_boot_mount_worker')
    request = dict(frozen=frozen, operation=operation,
                   modules=[(name, (base/(name+'.py')).read_text()) for name in names])
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=10', frozen['plan']['host'], 'sudo -n python3 -c '+shlex.quote(BOOTSTRAP)],
        input=json.dumps(request), text=True, capture_output=True, timeout=300, check=True)
    return json.loads(result.stdout)


def checked(observed, frozen):
    clean = frozen['cleanup']
    boot = normal_boot(observed['boot'], clean['machine_id'])
    verify_boot(boot, dict(clean['original_boot'], boot_id=clean['boot_id']), clean['machine_id'])
    expected = verify_mount(observed['mount'], clean['image_plan']['boot_source'], False)
    if (observed.get('verification') != expected or observed.get('inventory') != frozen['inventory'] or
            observed.get('mutation_performed') is not False or observed.get('reboot_requested') is not False):
        raise ValueError('Original mount observation differs')
    return boot


def verify(frozen, *, reboot=False, timeout=600):
    if type(reboot) is not bool or type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('Invalid final mount observation bound')
    directory = Path(frozen['journal'])
    with locked(directory) as (fd, plan):
        if load(fd, directory, plan, frozen['plan_sha256']) != frozen:
            raise ValueError('Original mount inputs changed after owner review')
        name = 'original-mount-reboot' if reboot else 'original-mount-verification-'+uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=fd)
        os.fsync(fd)
        output = directory/name
        out = private_directory(output)
        try:
            write_record(out, 'request.json', dict(plan_sha256=frozen['plan_sha256'],
                previous_boot_id=frozen['cleanup']['boot_id'], reboot_authorized=reboot))
            if reboot:
                # Exclusive durable directory is the one-use claim. Transport
                # uncertainty leads only to observations, never another reboot.
                write_record(out, 'reboot-intent.json', dict(automatic_retry_authorized=False))
                try: reply = transport(frozen, 'reboot')
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    reply = dict(error_type=type(exc).__name__, reboot_observed=False)
                write_record(out, 'reboot-transport.json', reply)
            deadline, attempt = time.monotonic()+timeout, 0
            while True:
                attempt += 1
                try:
                    observed = transport(frozen, 'inspect')
                    boot = checked(observed, frozen)
                    break
                except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
                    write_record(out, f'observation-{attempt:04d}.json', dict(error_type=type(exc).__name__))
                    if not reboot or time.monotonic() >= deadline:
                        raise RuntimeError('Original fresh mount not verified; do not repeat reboot') from exc
                    time.sleep(min(2, max(0, deadline-time.monotonic())))
            write_record(out, 'observation.json', observed)
            result = dict(status='verified-original-boot-mount', boot_id=boot['boot_id'],
                plan_sha256=frozen['plan_sha256'], inventory_sha256=digest(observed['inventory']),
                original_mount_verified=True, recovery_artifacts_absent=True,
                reboot_request_dispatched=reboot, automatic_retry_performed=False,
                filesystem_health_qualified=False, native_application_qualified=False)
            write_record(out, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(out, 'failure.json', dict(error_type=type(exc).__name__, reboot_may_have_started=reboot,
                automatic_retry_performed=False, preserve_journals=True))
            raise
        finally: os.close(out)
