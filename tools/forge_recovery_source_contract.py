"""Explicit source-kind contract for future shared root-transfer consumers.

Backup restoration and derivative deployment retain different manifests and
completion receipts. Neither receipt proves target writes or bootability. This
module performs no I/O and does not enable either kind in an existing worker.
"""
import copy

from forge_recovery_commit_protocol import exact
from forge_recovery_derivative import validate as validate_derivative
from forge_recovery_restore_source import validate as validate_backup

BACKUP = 'backup-root-chunk-source'
DERIVATIVE = 'root-only-image-derivative'


def validate(manifest, pin, *, expected_kind):
    """Require the plan-selected kind, never infer authority from client input."""
    if expected_kind not in (BACKUP, DERIVATIVE):
        raise ValueError('Unsupported owner-selected root source kind')
    if not isinstance(manifest, dict) or manifest.get('kind') != expected_kind:
        raise ValueError('Root source kind differs from owner-selected operation')
    checker = validate_backup if expected_kind == BACKUP else validate_derivative
    checker(manifest, pin)
    return copy.deepcopy(manifest)


def completion(manifest, pin, *, expected_kind):
    value = validate(manifest, pin, expected_kind=expected_kind)
    return dict(status='verified-source-stream' if expected_kind == BACKUP else 'verified-derivative-stream',
                manifest_sha256=pin, chunks=len(value['chunks']), root=value['root'],
                target_restore_verified=False, normal_boot_release_authorized=False)


def check_completion(receipt, manifest, pin, *, expected_kind):
    if not exact(receipt, completion(manifest, pin, expected_kind=expected_kind)):
        raise ValueError('Root source completion differs from selected source contract')
    return copy.deepcopy(receipt)


def rollback_manifest(manifest, pin, *, expected_kind):
    """Return original backup lineage without relabeling derivative bytes."""
    value = validate(manifest, pin, expected_kind=expected_kind)
    if expected_kind == BACKUP:
        return value, pin
    return value['original_manifest'], value['original_manifest_sha256']
