"""Owner-only offline reconciliation policy drafts; never retry an old write."""
import fcntl
import os
from pathlib import Path
import re

from forge_backup_policy import inputs as enrolled
from forge_backup_source import _create
from forge_recovery_bootcommit import validate as validate_hold
from forge_recovery_bootplan import digest
from forge_recovery_commit_protocol import exact
from forge_recovery_jobs import RecoveryJobs
from forge_recovery_lease import token
from forge_recovery_operation_contract import load as load_root
from forge_recovery_restore_reconcile_host import original_attempt
from forge_recovery_session import Session
from forge_target_journal import private_directory, read_record, write_record


def inputs(source, journal, pin, kind):
    if kind not in ('hold', 'root'):
        raise ValueError('Select hold or root reconciliation explicitly')
    if enrolled(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts) != source:
        raise ValueError('Enrolled recovery source changed')
    journal = Path(journal).absolute()
    fd = private_directory(journal)
    try:
        # An active host writer owns this same lock. Observation policy drafting
        # cannot be used as a pretext to interfere with or restart that writer.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if kind == 'hold':
            plan = validate_hold(read_record(fd, 'plan.json'), pin)
            attempt_fd = private_directory(journal/'commit-attempt')
            try: attempt = read_record(attempt_fd, 'dispatch.json')
            finally: os.close(attempt_fd)
            token(attempt.get('attempt'), '[0-9a-f]{32}')
            if not exact(attempt, dict(plan_sha256=pin, attempt=attempt['attempt'], binding=plan['binding'],
                                      lease_owner=source.owner, worker_protocol=2)):
                raise ValueError('Original hold dispatch binding differs')
            machine = plan['before']['machine_id']
        else:
            plan = load_root(journal, pin)
            retained = read_record(fd, 'inputs.json')
            attempt = original_attempt(journal, plan, pin, retained)
            machine = plan['held_files']['machine_id']
    finally:
        os.close(fd)
    b = plan['binding']
    if (b.get('mode') != 'physical' or b.get('lease_owner') != source.owner or machine != source.machine_id or
            any(b.get(key) != getattr(source.probe, key) for key in ('mode', 'nonce', 'kernel', 'serial'))):
        raise ValueError('Attempt differs from enrolled physical target or owner')
    # Deliberately permit a different, explicitly enrolled boot. Reconciliation
    # fences the old attempt rather than importing its lease or completion.
    arguments = dict(journal=str(journal), plan_sha256=pin)
    if kind == 'hold':
        arguments.update(root_sha256=plan['root_guard']['sha256'], root_bytes=plan['root_guard']['bytes'],
                         transition=plan['operation'])
    return dict(kind=kind, arguments=arguments, original_boot_id=b['boot_id'],
                observed_boot_id=source.boot_id, dispatch_sha256=digest(attempt),
                plan_sha256=pin, target_identity=source.machine_id)


def prepare(output, source, reviewed, workspace):
    if not isinstance(workspace, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', workspace):
        raise ValueError('Reconciliation requires a registered workspace name')
    if inputs(source, reviewed['arguments']['journal'], reviewed['plan_sha256'], reviewed['kind']) != reviewed:
        raise ValueError('Reconciliation evidence changed since review')
    output = Path(output).resolve()
    for path in (source.directory, reviewed['arguments']['journal']):
        path = Path(path).resolve(strict=True)
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Policy output must not overlap retained evidence')
    with Session(source.directory/'session', source.session_pin, source.probe, source.boot_id, source.owner) as lease:
        if lease.unresolved:
            raise ValueError('Resolve the existing uncertain lease before preparing reconciliation')
        fd = _create(output)
        try:
            write_record(fd, 'request.json', dict(inputs=reviewed, enrollment_sha256=source.acceptance_pin,
                                                session_sha256=source.session_pin, workspace=workspace))
            operation = 'reconcile-'+reviewed['kind']
            policy = dict(schema=1, jobs=dict(reconcile=dict(workspace=workspace, machine_id=source.machine_id,
                session=str(source.directory/'session'), session_sha256=source.session_pin,
                key=str(source.probe.key), known_hosts=str(source.probe.known_hosts),
                operation=operation, arguments=reviewed['arguments'])))
            pin = write_record(fd, 'policy.json', policy)
            job = RecoveryJobs(output/'policy.json', pin, {workspace: output}).get('reconcile', workspace)
            if job.operation != operation or job.boot_id != source.boot_id:
                raise ValueError('Reconciliation draft differs from reviewed session')
            result = dict(status='prepared-reconciliation-policy-not-approved', operation=operation,
                policy_sha256=pin, plan_sha256=reviewed['plan_sha256'], original_boot_id=reviewed['original_boot_id'],
                observed_boot_id=source.boot_id, target_contacted=False, lease_acquired=False,
                policy_approved=False, root_write_authorized=False, normal_boot_release_authorized=False,
                retry_authorized=False)
            write_record(fd, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(fd, 'failure.json', dict(error_type=type(exc).__name__, target_contacted=False,
                                                lease_acquired=False, preserve_evidence=True))
            raise
        finally:
            os.close(fd)
