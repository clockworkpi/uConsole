"""Compile modified-root deployment evidence; no transport or write authority."""
import copy
import os
from pathlib import Path

from forge_recovery_commit_protocol import exact
from forge_recovery_derivative import validate
from forge_recovery_derivative_health import check_health
from forge_recovery_restore_plan import compile_plan as compile_rollback
from forge_recovery_restore_source import digest, sha
from forge_target_journal import private_directory, read_record, write_record

INPUT_NAMES = ('hold_plan', 'hold_pin', 'derivative', 'derivative_pin', 'backup_plan',
               'hash_plan', 'hash_observation', 'hold_inspection', 'ram_boot_observation',
               'health', 'health_pin')


def compile_plan(hold_plan, hold_pin, derivative, derivative_pin, backup_plan,
                 hash_plan, hash_observation, hold_inspection, ram_boot_observation,
                 health, health_pin):
    """Bind a healthy root derivative to an intact backup and fresh physical hold.

    Reuse the physical rollback compiler's identity, boot-selection, held-file,
    layout and fresh-hash checks. A deployment is deliberately a different plan
    kind: existing backup-only workers must reject it until their explicit
    derivative support is qualified. The original root must still match the
    backup. Partial/prior deployments require reconciliation, not this shortcut.
    """
    derivative, health = copy.deepcopy((derivative, health))
    validate(derivative, derivative_pin)
    check_health(health, health_pin, derivative, derivative_pin)
    if not health['root_filesystem_consistency_qualified']:
        raise ValueError('Modified-root deployment requires clean derivative root evidence')
    original = derivative['original_manifest']
    rollback = compile_rollback(hold_plan, hold_pin, original,
        derivative['original_manifest_sha256'], backup_plan, hash_plan,
        hash_observation, hold_inspection, ram_boot_observation)
    if not exact(rollback['root_before'], original['root']):
        raise ValueError('Current root differs from retained original; reconcile before deployment')
    if not exact(rollback['suffix_guard'], derivative['suffix']):
        raise ValueError('Current protected suffix differs from original image')
    if not derivative['changed_chunks']:
        raise ValueError('Modified-root deployment requires an actual image change')
    plan = copy.deepcopy(rollback)
    plan.update(kind='guarded-derived-root-deploy-plan', operation='deploy-derived-root',
                source_manifest_sha256=derivative_pin, root_after=derivative['root'],
                rollback_manifest_sha256=derivative['original_manifest_sha256'],
                rollback_root=original['root'], source_health_sha256=health_pin,
                derivative_root_filesystem_qualified=True,
                native_boot_qualified=False)
    plan['required_gates'] = [gate for gate in plan['required_gates']
                              if gate != 'reviewed-source-filesystem-health'] + [
        'verified-root-only-derivative-lineage', 'clean-pinned-derivative-root-health',
        'verified-original-root-rollback-source', 'explicit-owner-derived-root-deployment-approval']
    return plan


def prepare(directory, *arguments):
    """Retain a private exclusive review journal, never start a target action."""
    arguments = copy.deepcopy(arguments)
    plan = compile_plan(*arguments)
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    try:
        write_record(fd, 'inputs.json', dict(zip(INPUT_NAMES, arguments)))
        pin = write_record(fd, 'plan.json', plan)
    finally:
        os.close(fd)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return dict(status='prepared-not-dispatched', journal=str(directory), plan_sha256=pin,
                root_write_authorized=False, normal_boot_release_authorized=False)


def load(directory, pin):
    """Recompile all retained inputs under an independently supplied plan pin."""
    fd = private_directory(directory)
    try:
        plan = read_record(fd, 'plan.json')
        inputs = read_record(fd, 'inputs.json')
    finally:
        os.close(fd)
    if not sha(pin) or digest(plan) != pin:
        raise ValueError('Deployment journal differs from owner plan pin')
    if not isinstance(inputs, dict) or set(inputs) != set(INPUT_NAMES):
        raise ValueError('Deployment source evidence set differs')
    rebuilt = compile_plan(*(inputs[name] for name in INPUT_NAMES))
    if not exact(plan, rebuilt):
        raise ValueError('Deployment plan differs from its retained evidence')
    return plan
