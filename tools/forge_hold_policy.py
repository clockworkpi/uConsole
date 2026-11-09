"""Offline owner preparation of an install-hold plan and unapproved job policy."""
import os
from pathlib import Path
import re

from forge_backup_policy import inputs as enrolled
from forge_backup_source import _create
from forge_recovery_bootplan import compile_transition, digest, prepare as prepare_plan
from forge_recovery_derivative import load as load_derivative
from forge_recovery_hold_jobs import load as load_hold_job
from forge_recovery_jobs import RecoveryJobs, path as checked_path
from forge_recovery_restore_source import opened
from forge_recovery_session import Session
from forge_recovery_source_contract import BACKUP, DERIVATIVE, validate as validate_source
from forge_recovery_stage_prepare import pinned_record
from forge_recovery_stage_review import load as load_staging
from forge_target_journal import private_directory, read_record, write_record


def inputs(source, staging, staging_pin, hashes, hashes_pin, manifest_directory, manifest_pin, source_kind):
    if enrolled(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts) != source:
        raise ValueError('Enrolled recovery session changed')
    accepted = pinned_record(source.directory/'acceptance.json', source.acceptance_pin)
    reviewed = load_staging(staging, staging_pin)
    if (accepted['staging_sha256'] != staging_pin or reviewed['acceptance']['machine_id'] != source.machine_id or
            reviewed['request']['nonce'] != source.probe.nonce or
            reviewed['request']['lease_owner'] != source.owner):
        raise ValueError('Staging differs from the enrolled target or owner')
    staging, hashes, manifest_directory = (Path(path).absolute() for path in (staging, hashes, manifest_directory))
    hold = pinned_record(staging/'hold-review.json', reviewed['acceptance']['hold_review_sha256'])
    observation = pinned_record(hashes/'acceptance.json', hashes_pin)
    fd = private_directory(hashes)
    try: hash_plan = read_record(fd, 'plan.json')
    finally: os.close(fd)
    manifest = pinned_record(manifest_directory/'manifest.json', manifest_pin)
    manifest = validate_source(manifest, manifest_pin, expected_kind=source_kind)
    if source_kind == DERIVATIVE:
        _, retained_inputs = load_derivative(manifest_directory, manifest_pin)
        original, backup = manifest['original_manifest'], retained_inputs['backup_directory']
    elif source_kind == BACKUP:
        fd = private_directory(manifest_directory)
        try:
            receipt = read_record(fd, 'acceptance.json')
            retained_inputs = read_record(fd, 'request.json')
        finally: os.close(fd)
        if receipt != dict(status='verified-root-chunk-source', manifest_sha256=manifest_pin,
                           chunk_count=len(manifest['chunks']), target_write_authorized=False,
                           normal_boot_release_authorized=False):
            raise ValueError('Original root source preparation is incomplete')
        original, backup = manifest, retained_inputs['source']
        if retained_inputs.get('expected_root') != original['root']:
            raise ValueError('Backup source request has a different root')
    # A historical checksum alone must not let the guided workflow persist a
    # hold draft after losing its original rollback archive. Recheck the
    # actual private archive's frozen identity; do not silently recreate it.
    checked_path(backup)
    with opened(backup, original['root']) as (_, binding):
        if any(binding[key] != original[key] for key in binding):
            raise ValueError('Original rollback archive differs from the selected source')
        if source_kind == BACKUP and any(retained_inputs.get(key) != binding[key] for key in binding):
            raise ValueError('Backup source request differs from its retained archive')
    compiled = compile_transition(hold, hash_plan, observation, manifest['root'], 'install-hold')
    binding = compiled['binding']
    if (binding.get('boot_id') != source.boot_id or binding.get('lease_owner') != source.owner or
            binding.get('mode') != 'physical' or compiled['before']['machine_id'] != source.machine_id or
            any(binding.get(key) != getattr(source.probe, key) for key in ('nonce', 'kernel', 'serial'))):
        raise ValueError('Hash evidence differs from the enrolled physical session')
    return dict(staging=str(staging), staging_sha256=staging_pin, hashes=str(hashes), hashes_sha256=hashes_pin,
                manifest_directory=str(manifest_directory), manifest_sha256=manifest_pin, source_kind=source_kind,
                backup_directory=backup, source_inputs_sha256=digest(retained_inputs),
                hold_review=hold, hash_plan=hash_plan, hash_observation=observation, source_manifest=manifest)


def prepare(output, source, reviewed, workspace):
    if not isinstance(workspace, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', workspace):
        raise ValueError('Hold policy requires a registered workspace name')
    if inputs(source, reviewed['staging'], reviewed['staging_sha256'], reviewed['hashes'],
              reviewed['hashes_sha256'], reviewed['manifest_directory'], reviewed['manifest_sha256'],
              reviewed['source_kind']) != reviewed:
        raise ValueError('Hold inputs changed since owner review')
    output = Path(output).resolve()
    for path in (source.directory, reviewed['staging'], reviewed['hashes'], reviewed['manifest_directory']):
        path = Path(path).resolve(strict=True)
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Hold output must not overlap enrolled or retained input evidence')
    # This lock prevents conflict with an active owner job but does not contact
    # the device, renew a lease, reset a sequence, or adopt an uncertain session.
    with Session(source.directory/'session', source.session_pin, source.probe, source.boot_id, source.owner) as lease:
        if lease.unresolved:
            raise ValueError('Resolve the existing uncertain lease before preparing a hold')
        fd = _create(output)
        try:
            write_record(fd, 'request.json', dict(inputs=reviewed, enrollment_sha256=source.acceptance_pin,
                                                session_sha256=source.session_pin, workspace=workspace))
            root = reviewed['source_manifest']['root']
            plan = prepare_plan(output/'hold', reviewed['hold_review'], reviewed['hash_plan'],
                                reviewed['hash_observation'], root, 'install-hold')
            policy = dict(schema=1, jobs=dict(hold=dict(workspace=workspace, machine_id=source.machine_id,
                session=str(source.directory/'session'), session_sha256=source.session_pin,
                key=str(source.probe.key), known_hosts=str(source.probe.known_hosts), operation='install-hold',
                arguments=dict(journal=str(output/'hold'), plan_sha256=plan['plan_sha256'],
                               root_sha256=root['sha256'], root_bytes=root['bytes']))))
            pin = write_record(fd, 'policy.json', policy)
            registry = RecoveryJobs(output/'policy.json', pin, {workspace: output})
            load_hold_job(registry.get('hold', workspace))
            result = dict(status='prepared-hold-policy-not-approved', policy_sha256=pin,
                plan_sha256=plan['plan_sha256'], staging_sha256=reviewed['staging_sha256'],
                source_manifest_sha256=reviewed['manifest_sha256'], source_kind=reviewed['source_kind'],
                hash_observation_sha256=reviewed['hashes_sha256'], root_sha256=root['sha256'], root_bytes=root['bytes'],
                target_written=False, lease_acquired=False, policy_approved=False, hold_installed=False,
                root_write_authorized=False, normal_boot_release_authorized=False)
            write_record(fd, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(fd, 'failure.json', dict(error_type=type(exc).__name__, target_written=False,
                                                lease_acquired=False, preserve_evidence=True))
            raise
        finally:
            os.close(fd)
