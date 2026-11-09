"""Prepare a normal-selector release only from independently reconciled root evidence."""
import os
from pathlib import Path
import re

from forge_backup_source import _create
from forge_reconcile_policy import inputs as reconcile_inputs
from forge_recovery_bootplan import compile_transition, digest, prepare as prepare_plan
from forge_recovery_commit_protocol import exact
from forge_recovery_hold_jobs import load as load_hold
from forge_recovery_jobs import RecoveryJobs, path as checked_path
from forge_recovery_operation_contract import load, source_kind
from forge_recovery_restore_reconcile import classify
from forge_recovery_restore_reconcile_host import original_attempt
from forge_recovery_restore_source import opened
from forge_recovery_session import Session
from forge_recovery_source_contract import DERIVATIVE
from forge_recovery_stage_prepare import pinned_record
from forge_root_policy import record
from forge_target_journal import write_record


def inputs(source, journal, pin, reconciliation, reconciliation_pin, review_directory, review_pin, backup_directory):
    journal, reconciliation, review_directory = (Path(p).absolute() for p in (journal, reconciliation, review_directory))
    backup = checked_path(str(backup_directory))
    reconcile_inputs(source, journal, pin, 'root')
    plan, retained = load(journal, pin), record(journal, 'inputs.json')
    original = original_attempt(journal, plan, pin, retained)
    accepted = pinned_record(reconciliation/'acceptance.json', reconciliation_pin)
    query = accepted.get('query')
    if (not isinstance(query, str) or not re.fullmatch('[0-9a-f]{32}', query) or
            reconciliation.parent.resolve() != journal.resolve() or reconciliation.name != 'reconcile-'+query or
            Path(accepted.get('evidence_directory', '')).resolve() != reconciliation.resolve()):
        raise ValueError('Require the retained root reconciliation journal')
    hashes = record(reconciliation/'hashes', 'acceptance.json')
    hash_plan = record(reconciliation/'hashes', 'plan.json')
    classified = classify(plan, pin, original['attempt'], retained['hold_plan'], retained['hold_pin'],
        record(reconciliation, 'fence.json'), record(reconciliation, 'inspect.json'), hash_plan, hashes)
    if not exact(accepted, dict(classified, query=query, root_written=False, evidence_directory=str(reconciliation))):
        raise ValueError('Root reconciliation differs from independently reclassified evidence')
    if (classified['status'] != 'reconciled-source-matched' or classified['requires_new_recovery_boot'] or
            classified['boot_id'] != source.boot_id or classified['conflicts']):
        raise ValueError('Release requires matching approved root in a safely fenced current boot')
    review = pinned_record(review_directory/'hold-review.json', review_pin)
    held = retained['hold_plan']
    if (review_pin != held['hold_review_sha256'] or review['before'] != held['before'] or
            review['after'] != held['after'] or review['image_dependency'] != held['image_dependency']):
        raise ValueError('Normal-selector preimages differ from the original held plan')
    compiled = compile_transition(review, hash_plan, hashes, plan['root_after'], 'release-hold')
    if (compiled['before'] != plan['held_files'] or compiled['before']['machine_id'] != source.machine_id or
            compiled['binding']['lease_owner'] != source.owner):
        raise ValueError('Release differs from the enrolled target or approved held files')
    derived = source_kind(plan) == DERIVATIVE
    manifest = retained['derivative']['original_manifest'] if derived else retained['source_manifest']
    with opened(backup, manifest['root']) as (_, binding):
        if any(binding[key] != manifest[key] for key in binding):
            raise ValueError('Retained original rollback backup differs')
    health = record(journal/'restore-attempt', 'source-health.json')
    if digest(health) != original['source_health_sha256']:
        raise ValueError('Source health changed during release review')
    return dict(journal=str(journal), plan_sha256=pin, reconciliation=str(reconciliation),
        reconciliation_sha256=reconciliation_pin, review_directory=str(review_directory), review_sha256=review_pin,
        backup_directory=str(backup), hold_review=review, hash_plan=hash_plan, hash_observation=hashes,
        root=plan['root_after'], source_health_sha256=original['source_health_sha256'],
        source_filesystem_errors=(not derived and not health['filesystem_consistency_qualified']),
        original_attempt_completion_verified=classified['original_attempt_completion_verified'])


def prepare(output, source, reviewed, workspace, *, accept_filesystem_errors=False):
    if (type(accept_filesystem_errors) is not bool or not isinstance(workspace, str) or
            not re.fullmatch('[A-Za-z0-9_-]{1,64}', workspace)):
        raise ValueError('Explicit health decision and registered workspace required')
    if inputs(source, *(reviewed[key] for key in ('journal', 'plan_sha256', 'reconciliation',
            'reconciliation_sha256', 'review_directory', 'review_sha256', 'backup_directory'))) != reviewed:
        raise ValueError('Release evidence changed since owner review')
    if reviewed['source_filesystem_errors'] and not accept_filesystem_errors:
        raise ValueError('Normal boot of known original filesystem errors requires explicit owner acceptance')
    output = Path(output).resolve()
    for path in (source.directory, *(reviewed[key] for key in ('journal', 'review_directory', 'backup_directory'))):
        path = Path(path).resolve(strict=True)
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Release output must not overlap its retained evidence')
    with Session(source.directory/'session', source.session_pin, source.probe, source.boot_id, source.owner) as lease:
        if lease.unresolved:
            raise ValueError('Resolve the existing uncertain lease before preparing release')
        fd = _create(output)
        try:
            write_record(fd, 'request.json', dict(inputs=reviewed, enrollment_sha256=source.acceptance_pin,
                session_sha256=source.session_pin, accept_filesystem_errors=accept_filesystem_errors))
            root = reviewed['root']
            plan = prepare_plan(output/'release', reviewed['hold_review'], reviewed['hash_plan'],
                                reviewed['hash_observation'], root, 'release-hold')
            policy = dict(schema=1, jobs=dict(release=dict(workspace=workspace, machine_id=source.machine_id,
                session=str(source.directory/'session'), session_sha256=source.session_pin,
                key=str(source.probe.key), known_hosts=str(source.probe.known_hosts), operation='release-hold',
                arguments=dict(journal=str(output/'release'), plan_sha256=plan['plan_sha256'],
                               root_sha256=root['sha256'], root_bytes=root['bytes']))))
            pin = write_record(fd, 'policy.json', policy)
            load_hold(RecoveryJobs(output/'policy.json', pin, {workspace: output}).get('release', workspace))
            result = dict(status='prepared-release-policy-not-approved', policy_sha256=pin,
                plan_sha256=plan['plan_sha256'], root=root, source_health_sha256=reviewed['source_health_sha256'],
                source_filesystem_errors=reviewed['source_filesystem_errors'], accept_filesystem_errors=accept_filesystem_errors,
                original_attempt_completion_verified=reviewed['original_attempt_completion_verified'],
                target_contacted=False, lease_acquired=False, policy_approved=False, reboot_performed=False,
                root_write_authorized=False, normal_boot_release_authorized=False)
            write_record(fd, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(fd, 'failure.json', dict(error_type=type(exc).__name__, target_contacted=False,
                                                lease_acquired=False, preserve_evidence=True))
            raise
        finally:
            os.close(fd)
