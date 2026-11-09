"""Offline owner drafts for enhanced-root deployment or original-root restore."""
import os
from pathlib import Path
import re
import stat

from forge_backup_policy import inputs as enrolled
from forge_backup_source import _create
from forge_recovery_archive import fingerprint
from forge_recovery_bootcommit import validate as validate_hold
from forge_recovery_bootplan import digest
from forge_recovery_commit_reconcile import check_fence, check_inspection
from forge_recovery_deploy_plan import compile_plan as compile_deploy, prepare as prepare_deploy
from forge_recovery_derivative import load as load_derivative
from forge_recovery_derivative_health import read_health as derivative_health
from forge_recovery_jobs import RecoveryJobs, path as checked_path
from forge_recovery_restore_dispatch import read_health as backup_health
from forge_recovery_restore_plan import compile_plan as compile_restore, prepare as prepare_restore
from forge_recovery_restore_source import opened, validate as validate_original
from forge_recovery_session import Session
from forge_recovery_stage_prepare import pinned_record
from forge_reconcile_policy import inputs as reconcile_inputs
from forge_target_journal import private_directory, read_record, write_record


def record(directory, name):
    fd = private_directory(directory)
    try: return read_record(fd, name)
    finally: os.close(fd)


def inputs(source, hold_directory, hold_pin, reconciliation, reconciliation_pin,
           manifest_directory, manifest_pin, health_directory, health_pin, operation):
    if operation not in ('deploy-root', 'restore-root'):
        raise ValueError('Select enhanced deployment or original restoration explicitly')
    if enrolled(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts) != source:
        raise ValueError('Enrolled physical session changed')
    paths = [Path(path).absolute() for path in (hold_directory, reconciliation, manifest_directory, health_directory)]
    hold_directory, reconciliation, manifest_directory, health_directory = paths
    reconcile_inputs(source, hold_directory, hold_pin, 'hold')  # Includes old dispatch and active-writer exclusion.
    hold = validate_hold(record(hold_directory, 'plan.json'), hold_pin)
    accepted = pinned_record(reconciliation/'acceptance.json', reconciliation_pin)
    if (accepted.get('status') != 'reconciled-after' or accepted.get('plan_sha256') != hold_pin or
            any(accepted.get(key) is not False for key in ('deployment_authorized', 'root_written', 'normal_boot_release_authorized')) or
            Path(accepted.get('evidence_directory', '')).resolve() != reconciliation.resolve() or
            reconciliation.parent.resolve() != hold_directory.resolve() or
            reconciliation.name != 'reconcile-'+accepted.get('query', '')):
        raise ValueError('Require the completed pinned hold reconciliation journal')
    dispatch = record(hold_directory/'commit-attempt', 'dispatch.json')
    if accepted.get('attempt') != dispatch['attempt']:
        raise ValueError('Reconciliation belongs to another hold attempt')
    fence = check_fence(accepted['fence'], hold, hold_pin, dispatch['attempt'], observed_boot_id=source.boot_id)
    inspection = check_inspection(accepted['inspection'], hold, hold_pin, observed_boot_id=source.boot_id)
    hashes = record(reconciliation/'hashes', 'acceptance.json')
    hash_plan = record(reconciliation/'hashes', 'plan.json')
    if (record(reconciliation, 'fence.json') != fence or record(reconciliation, 'inspect.json') != inspection or
            accepted['digests'] != hashes['digests'] or hashes['digests']['root'] != hold['root_guard'] or
            hashes['digests']['suffix'] != hold['suffix_guard']):
        raise ValueError('Retained reconciliation observations differ')
    selection = record(source.directory, 'selection.json')['observation']
    if operation == 'deploy-root':
        manifest, retained = load_derivative(manifest_directory, manifest_pin)
        original, backup = manifest['original_manifest'], checked_path(retained['backup_directory'])
        image = Path(retained['image'])
        fd = private_directory(image.parent)
        try:
            image_fd = os.open(image.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                info = os.fstat(image_fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or
                        info.st_nlink != 1 or list(fingerprint(info)) != manifest['image_fingerprint']):
                    raise ValueError('Enhanced image differs from verified lineage')
            finally: os.close(image_fd)
        finally: os.close(fd)
        health = derivative_health(health_directory, health_pin, manifest, manifest_pin)
    else:
        manifest = original = validate_original(record(manifest_directory, 'manifest.json'), manifest_pin)
        retained = record(manifest_directory, 'request.json')
        if record(manifest_directory, 'acceptance.json') != dict(status='verified-root-chunk-source',
                manifest_sha256=manifest_pin, chunk_count=len(manifest['chunks']),
                target_write_authorized=False, normal_boot_release_authorized=False):
            raise ValueError('Original root source is not completely verified')
        backup = checked_path(retained['source'])
        health = backup_health(health_directory, health_pin, manifest)
    with opened(backup, original['root']) as (_, binding):
        if any(binding[key] != original[key] for key in binding):
            raise ValueError('Original rollback archive no longer matches its manifest')
    backup_plan = record(backup, 'plan.json')
    arguments = [hold, hold_pin, manifest, manifest_pin, backup_plan, hash_plan, hashes, inspection, selection]
    if operation == 'deploy-root': arguments += [health, health_pin]
    compiled = (compile_deploy if operation == 'deploy-root' else compile_restore)(*arguments)
    b = compiled['binding']
    if (b['boot_id'] != source.boot_id or b['lease_owner'] != source.owner or
            compiled['held_files']['machine_id'] != source.machine_id or
            any(b[key] != getattr(source.probe, key) for key in ('mode', 'nonce', 'kernel', 'serial'))):
        raise ValueError('Root plan differs from enrolled physical session')
    return dict(operation=operation, hold_directory=str(hold_directory), hold_pin=hold_pin,
        reconciliation=str(reconciliation), reconciliation_pin=reconciliation_pin,
        manifest_directory=str(manifest_directory), manifest_pin=manifest_pin,
        health_directory=str(health_directory), health_pin=health_pin, backup_directory=str(backup),
        arguments=arguments, source_inputs_sha256=digest(retained), plan_sha256=digest(compiled),
        root_before=compiled['root_before'], root_after=compiled['root_after'],
        source_filesystem_errors=(operation == 'restore-root' and not health['filesystem_consistency_qualified']))


def prepare(output, source, reviewed, workspace, *, accept_filesystem_errors=False):
    if (type(accept_filesystem_errors) is not bool or not isinstance(workspace, str) or
            not re.fullmatch('[A-Za-z0-9_-]{1,64}', workspace)):
        raise ValueError('Explicit health decision and registered workspace required')
    current = inputs(source, *(reviewed[key] for key in ('hold_directory', 'hold_pin', 'reconciliation',
        'reconciliation_pin', 'manifest_directory', 'manifest_pin', 'health_directory', 'health_pin', 'operation')))
    if current != reviewed:
        raise ValueError('Root preparation evidence changed since owner review')
    if accept_filesystem_errors and reviewed['operation'] != 'restore-root':
        raise ValueError('Enhanced deployment cannot accept filesystem errors')
    if reviewed['source_filesystem_errors'] and not accept_filesystem_errors:
        raise ValueError('Restoring known source filesystem errors requires explicit owner acceptance')
    output = Path(output).resolve()
    for path in (source.directory, *(reviewed[key] for key in ('hold_directory', 'manifest_directory',
                                                             'health_directory', 'backup_directory'))):
        path = Path(path).resolve(strict=True)
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Root output must not overlap retained evidence or backup')
    with Session(source.directory/'session', source.session_pin, source.probe, source.boot_id, source.owner) as lease:
        if lease.unresolved:
            raise ValueError('Resolve the existing uncertain lease before preparing root transfer')
        fd = _create(output)
        try:
            write_record(fd, 'request.json', dict(inputs=reviewed, enrollment_sha256=source.acceptance_pin,
                session_sha256=source.session_pin, accept_filesystem_errors=accept_filesystem_errors))
            derived = reviewed['operation'] == 'deploy-root'
            prepared = (prepare_deploy if derived else prepare_restore)(output/'root-plan', *reviewed['arguments'])
            if prepared['plan_sha256'] != reviewed['plan_sha256']:
                raise ValueError('Published root plan differs from reviewed evidence')
            arguments = dict(journal=str(output/'root-plan'), plan_sha256=prepared['plan_sha256'],
                source_directory=reviewed['manifest_directory'] if derived else reviewed['backup_directory'],
                health_directory=reviewed['health_directory'], health_sha256=reviewed['health_pin'])
            if not derived: arguments['accept_filesystem_errors'] = accept_filesystem_errors
            policy = dict(schema=1, jobs=dict(transfer=dict(workspace=workspace, machine_id=source.machine_id,
                session=str(source.directory/'session'), session_sha256=source.session_pin,
                key=str(source.probe.key), known_hosts=str(source.probe.known_hosts),
                operation=reviewed['operation'], arguments=arguments)))
            pin = write_record(fd, 'policy.json', policy)
            job = RecoveryJobs(output/'policy.json', pin, {workspace: output}).get('transfer', workspace)
            if job.operation != reviewed['operation']: raise ValueError('Root policy operation differs')
            result = dict(status='prepared-root-policy-not-approved', operation=reviewed['operation'],
                policy_sha256=pin, plan_sha256=prepared['plan_sha256'], root_before=reviewed['root_before'],
                root_after=reviewed['root_after'], source_filesystem_errors=reviewed['source_filesystem_errors'],
                accept_filesystem_errors=accept_filesystem_errors, target_contacted=False, lease_acquired=False,
                policy_approved=False, root_write_authorized=False, normal_boot_release_authorized=False)
            write_record(fd, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(fd, 'failure.json', dict(error_type=type(exc).__name__, target_contacted=False,
                                                lease_acquired=False, preserve_evidence=True))
            raise
        finally:
            os.close(fd)
