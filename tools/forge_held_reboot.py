"""Owner-only acknowledged hold to fresh persistent RAM enrollment, without retry."""
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import uuid

from forge_backup_policy import inputs as enrolled
from forge_recovery_bootcommit import validate
from forge_recovery_commit_protocol import exact
from forge_recovery_commit_reconcile import expected_receipt
from forge_recovery_commit_worker import BOOTSTRAP, payload
from forge_recovery_session import Session
from forge_recovery_stage_review import load as load_staging
from forge_session_enrollment import inputs as enrollment_inputs, prepare as enroll
from forge_target_journal import private_directory, read_record, write_record


def reviewed(source, value, directory, pin):
    if enrolled(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts) != source:
        raise ValueError('Source enrollment changed')
    rebound = enrollment_inputs(value.staging, value.staging_pin, source.probe.host,
        source.probe.key, source.probe.known_hosts, source.probe.kernel, source.probe.serial, None, 0)
    if value != rebound or value.owner != source.owner or value.machine_id != source.machine_id:
        raise ValueError('Held reboot requires fresh persistent enrollment of the same owner and target')
    fd = private_directory(source.directory)
    try: original = read_record(fd, 'acceptance.json')
    finally: os.close(fd)
    if original['staging_sha256'] != value.staging_pin:
        raise ValueError('Held reboot staging differs from source enrollment')
    fd = private_directory(directory)
    try:
        if os.path.lexists(Path(directory)/'held-reboot-attempt.json'):
            raise ValueError('Held reboot already attempted; inspect and enroll, never repeat')
        plan = validate(read_record(fd, 'plan.json'), pin)
    finally: os.close(fd)
    b = plan['binding']
    staging = load_staging(value.staging, value.staging_pin)
    if (plan['hold_review_sha256'] != staging['acceptance']['hold_review_sha256'] or
            plan['operation'] != 'install-hold' or b['mode'] != 'physical' or
            b['boot_id'] != source.boot_id or b.get('lease_owner') != source.owner or
            plan['before']['machine_id'] != source.machine_id or
            any(b[key] != getattr(source.probe, key) for key in ('nonce', 'kernel', 'serial'))):
        raise ValueError('Hold differs from enrolled physical session')
    fd = private_directory(Path(directory)/'commit-attempt')
    try:
        dispatch, accepted = read_record(fd, 'dispatch.json'), read_record(fd, 'acceptance.json')
        if os.path.lexists(Path(directory)/'commit-attempt/failure.json'):
            raise ValueError('Contradictory hold failure requires reconciliation')
    finally: os.close(fd)
    attempt = dispatch.get('attempt')
    from forge_recovery_lease import token
    token(attempt, '[0-9a-f]{32}')
    if (not exact(dispatch, dict(plan_sha256=pin, attempt=attempt, binding=b,
                                lease_owner=source.owner, worker_protocol=2)) or
            not exact(accepted, dict(status='acknowledged', plan_sha256=pin, attempt=attempt,
                                     root_written=False, receipt=expected_receipt(plan, pin)))):
        raise ValueError('Require an acknowledged exact hold, not an uncertain commit')
    return plan


def reboot_transport(source, plan, pin, lease, query):
    request = payload(plan, pin, source.owner, query)
    base = Path(__file__).parent
    for name in ('forge_recovery_commit_inspect', 'forge_held_reboot_worker'):
        request['modules'].append((name, (base/(name+'.py')).read_text()))
    request.update(lease=lease, query=query)
    bootstrap = BOOTSTRAP.replace('from forge_recovery_commit_worker import run',
                                  'from forge_held_reboot_worker import run')
    # Explicit payload owns stdin; never inherit the enclosing MCP input pipe.
    try:
        reply = subprocess.run(source.probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(bootstrap)),
            input=json.dumps(request)+'\n', text=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=150)
        return dict(returncode=reply.returncode, reboot_verified=False)
    except subprocess.TimeoutExpired:
        return dict(transport_timeout=True, reboot_verified=False, do_not_repeat=True)


def boot_and_enroll(output, source, value, directory, pin, *, timeout=600):
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('Invalid recovery observation bound')
    plan = reviewed(source, value, directory, pin)
    output, directory = Path(output).resolve(), Path(directory).resolve()
    for path in (source.directory, value.staging, directory):
        path = Path(path).resolve(strict=True)
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Reboot evidence must not overlap its inputs')
    fd, out = private_directory(directory), None
    dispatched = renewed = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with Session(source.directory/'session', source.session_pin, source.probe,
                     source.boot_id, source.owner) as session:
            if session.unresolved:
                raise ValueError('Resolve the existing uncertain lease before reboot')
            if reviewed(source, value, directory, pin) != plan:
                raise ValueError('Hold changed before reboot submission')
            output.mkdir(mode=0o700)
            out = private_directory(output)
            query = uuid.uuid4().hex
            write_record(fd, 'held-reboot-attempt.json', dict(output=str(output), plan_sha256=pin,
                boot_id=source.boot_id, query=query, automatic_retry_authorized=False))
            try:
                write_record(out, 'request.json', dict(plan_sha256=pin, boot_id=source.boot_id,
                    staging_sha256=value.staging_pin, query=query, root_write_authorized=False))
                source.probe.inspect(expected_boot_id=source.boot_id)
                lease = session.renew()
                renewed = True
                write_record(out, 'reboot-intent.json', dict(boot_id=source.boot_id, persistent_recovery=True))
                dispatched = True
                write_record(out, 'reboot-transport.json', reboot_transport(source, plan, pin, lease, query))
                deadline, attempt = time.monotonic()+timeout, 0
                while True:
                    attempt += 1
                    try:
                        observed = value.probe.inspect()
                        if observed['verification']['boot_id'] == source.boot_id:
                            raise ValueError('Still observing the previous recovery boot')
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                        write_record(out, f'waiting-{attempt:04d}.json', dict(error_type=type(exc).__name__))
                        if time.monotonic() >= deadline:
                            raise RuntimeError('New held boot not verified; do not repeat reboot') from exc
                        time.sleep(min(2, max(0, deadline-time.monotonic())))
                        continue
                    break
                write_record(out, 'observed-ram.json', observed)
                boot_id = observed['verification']['boot_id']
                result = dict(status='held-boot-enrolled-not-leased', boot_id=boot_id,
                    plan_sha256=pin, enrollment=enroll(output/'enrollment', replace(value, boot_id=boot_id)),
                    previous_session_lease_renewed=True, lease_acquired=False,
                    reboot_request_dispatched=True, root_write_authorized=False,
                    normal_boot_release_authorized=False, automatic_retry_performed=False,
                    recovery_fallback_qualified=False)
                write_record(out, 'acceptance.json', result)
                return result
            except BaseException as exc:
                write_record(out, 'failure.json', dict(error_type=type(exc).__name__,
                    reboot_may_have_started=dispatched, previous_session_lease_renewed=renewed,
                    automatic_retry_performed=False, preserve_journals=True))
                raise
    finally:
        if out is not None: os.close(out)
        os.close(fd)
