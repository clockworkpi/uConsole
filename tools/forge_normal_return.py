"""Owner-only acknowledged selector release to verified native boot, without retry."""
from contextlib import nullcontext
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import uuid

from forge_backup_policy import inputs as enrolled
from forge_backup_source import _create
from forge_boot_observation import capture, normal_boot, verify as verify_boot
from forge_recovery_bootcommit import validate
from forge_recovery_commit_protocol import exact
from forge_recovery_commit_reconcile import expected_receipt
from forge_recovery_commit_worker import BOOTSTRAP, payload
from forge_recovery_lease import token
from forge_recovery_session import Session
from forge_recovery_stage_review import load as load_staging
from forge_root_policy import record
from forge_target_journal import private_directory, write_record


def inputs(source, staging, staging_pin, directory, pin):
    if enrolled(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts) != source:
        raise ValueError('Source enrollment changed')
    staging, directory = Path(staging).absolute(), Path(directory).absolute()
    sealed = load_staging(staging, staging_pin)
    if (record(source.directory, 'acceptance.json')['staging_sha256'] != staging_pin or
            sealed['request']['lease_owner'] != source.owner or
            sealed['request']['nonce'] != source.probe.nonce or
            sealed['acceptance']['machine_id'] != source.machine_id):
        raise ValueError('Normal return staging differs from enrollment')
    plan = validate(record(directory, 'plan.json'), pin)
    b = plan['binding']
    if (plan['operation'] != 'release-hold' or b['mode'] != 'physical' or b['boot_id'] != source.boot_id or
            b.get('lease_owner') != source.owner or plan['before']['machine_id'] != source.machine_id or
            any(b[key] != getattr(source.probe, key) for key in ('nonce', 'kernel', 'serial')) or
            plan['hold_review_sha256'] != sealed['acceptance']['hold_review_sha256'] or
            plan['after'] != record(staging, 'planned-staged.json')):
        raise ValueError('Normal return differs from enrolled release and sealed boot preimages')
    dispatch = record(directory/'commit-attempt', 'dispatch.json')
    accepted = record(directory/'commit-attempt', 'acceptance.json')
    token(dispatch.get('attempt'), '[0-9a-f]{32}')
    if (os.path.lexists(directory/'commit-attempt/failure.json') or
            not exact(dispatch, dict(plan_sha256=pin, attempt=dispatch['attempt'], binding=b,
                                    lease_owner=source.owner, worker_protocol=2)) or
            not exact(accepted, dict(status='acknowledged', plan_sha256=pin, attempt=dispatch['attempt'],
                                    root_written=False, receipt=expected_receipt(plan, pin)))):
        raise ValueError('Normal reboot requires an acknowledged exact release; reconcile uncertainty')
    return dict(staging=str(staging), staging_sha256=staging_pin, directory=str(directory), plan_sha256=pin,
                plan=plan, normal_host=sealed['request']['image_plan']['host'], original_boot=sealed['original_boot'])


def reboot_transport(source, frozen, lease, query):
    request = payload(frozen['plan'], frozen['plan_sha256'], source.owner, query)
    base = Path(__file__).parent
    for name in ('forge_recovery_commit_inspect', 'forge_held_reboot_worker', 'forge_normal_reboot_worker'):
        request['modules'].append((name, (base/(name+'.py')).read_text()))
    request.update(lease=lease, query=query)
    bootstrap = BOOTSTRAP.replace('from forge_recovery_commit_worker import run',
                                  'from forge_normal_reboot_worker import run')
    try:
        reply = subprocess.run(source.probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(bootstrap)),
            input=json.dumps(request)+'\n', text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=150)
        return dict(returncode=reply.returncode, reboot_verified=False)
    except subprocess.TimeoutExpired:
        return dict(transport_timeout=True, reboot_verified=False, do_not_repeat=True)


NATIVE = '''import json,os
from pathlib import Path
print(json.dumps(dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    kernel=os.uname().release,serial=Path('/proc/device-tree/serial-number').read_text().rstrip('\\x00\\n'))))
'''


def native_identity(host):
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=10', host, 'python3 -c '+shlex.quote(NATIVE)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=20)
    return json.loads(result.stdout)


def observe(source, frozen):
    before = normal_boot(capture(frozen['normal_host']), source.machine_id)
    verified = verify_boot(before, frozen['original_boot'], source.machine_id)
    if before['boot_id'] == source.boot_id:
        raise ValueError('Normal return must be newer than recovery')
    native = native_identity(frozen['normal_host'])
    if not exact(native, dict(boot_id=before['boot_id'], kernel=source.probe.kernel, serial=source.probe.serial)):
        raise ValueError('Native return kernel, serial or boot differs')
    after = normal_boot(capture(frozen['normal_host']), source.machine_id)
    if not exact(before, after): raise ValueError('Native boot changed during return verification')
    return dict(before=before, identity=native, after=after, verification=verified)


def return_to_normal(output, source, frozen, *, reboot=False, timeout=600):
    if type(reboot) is not bool or type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('Explicit reboot choice and bounded observation required')
    def recheck():
        if inputs(source, frozen['staging'], frozen['staging_sha256'], frozen['directory'], frozen['plan_sha256']) != frozen:
            raise ValueError('Normal return inputs changed since owner review')
    recheck()
    output = Path(output).resolve()
    for path in (source.directory, frozen['staging'], frozen['directory']):
        path = Path(path).resolve(strict=True)
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Normal return evidence must not overlap its inputs')
    fd, out = private_directory(frozen['directory']), None
    dispatched = renewed = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if reboot and os.path.lexists(Path(frozen['directory'])/'normal-reboot-attempt.json'):
            raise ValueError('Normal reboot already attempted; verify read-only, never repeat')
        context = Session(source.directory/'session', source.session_pin, source.probe,
                          source.boot_id, source.owner) if reboot else nullcontext()
        with context as session:
            if reboot and session.unresolved:
                raise ValueError('Resolve the existing uncertain lease before normal reboot')
            recheck()
            out = _create(output)
            query = uuid.uuid4().hex
            try:
                write_record(out, 'request.json', dict(inputs=frozen, reboot_requested=reboot,
                    old_boot_id=source.boot_id, query=query, automatic_retry_authorized=False))
                if reboot:
                    write_record(fd, 'normal-reboot-attempt.json', dict(output=str(output),
                        plan_sha256=frozen['plan_sha256'], boot_id=source.boot_id, query=query))
                    source.probe.inspect(expected_boot_id=source.boot_id)
                    lease = session.renew()
                    renewed = True
                    write_record(out, 'reboot-intent.json', dict(boot_id=source.boot_id, normal_boot=True))
                    dispatched = True
                    write_record(out, 'reboot-transport.json', reboot_transport(source, frozen, lease, query))
                deadline, attempt = time.monotonic()+timeout, 0
                while True:
                    attempt += 1
                    try:
                        observed = observe(source, frozen)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                        write_record(out, f'waiting-{attempt:04d}.json', dict(error_type=type(exc).__name__))
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Native return not verified; retain evidence and never retry reboot') from exc
                        time.sleep(min(2, max(0, deadline-time.monotonic())))
                        continue
                    break
                write_record(out, 'normal-return.json', observed)
                result = dict(status='verified-normal-return', boot_id=observed['verification']['boot_id'],
                    plan_sha256=frozen['plan_sha256'], reboot_request_dispatched=dispatched,
                    previous_session_lease_renewed=renewed, read_only=not reboot, automatic_retry_performed=False,
                    root_write_authorized=False, cleanup_performed=False,
                    filesystem_health_qualified=False, native_application_qualified=False)
                write_record(out, 'acceptance.json', result)
                return result
            except BaseException as exc:
                write_record(out, 'failure.json', dict(error_type=type(exc).__name__, reboot_may_have_started=dispatched,
                    previous_session_lease_renewed=renewed, automatic_retry_performed=False, preserve_evidence=True))
                raise
    finally:
        if out is not None: os.close(out)
        os.close(fd)
