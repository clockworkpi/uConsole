"""Owner-reviewed offline backup-to-root-source preparation; no target access."""
import os
from pathlib import Path
import shutil

from forge_recovery_archive import RESERVE_BYTES, materialize
from forge_recovery_archive_hash import capture
from forge_recovery_filesystems import descriptor_directory, filesystem_tool, inspect_filesystems
from forge_recovery_restore_source import digest, opened, prepare as prepare_source, sha
from forge_target_journal import private_directory, read_record, write_record


def inputs(directory, acceptance_pin):
    directory = Path(directory).absolute()
    fd = private_directory(directory)
    try:
        accepted = read_record(fd, 'acceptance.json')
        plan = read_record(fd, 'plan.json')
    finally:
        os.close(fd)
    if not sha(acceptance_pin) or digest(accepted) != acceptance_pin:
        raise ValueError('Backup receipt differs from owner-reviewed pin')
    extent = plan.get('extent', {})
    # opened() checks receipt, geometry, permissions and the archive identity,
    # without confusing the placeholder root digest with byte verification.
    root = dict(offset=extent.get('offset_bytes'), bytes=extent.get('length_bytes'), sha256='0'*64)
    with opened(directory, root) as (_, binding):
        if binding['backup_plan_sha256'] != digest(plan):
            raise ValueError('Backup plan changed during review')
        if binding['backup_acceptance_sha256'] != acceptance_pin:
            raise ValueError('Backup receipt changed during review')
    return dict(directory=str(directory), acceptance_sha256=acceptance_pin,
                plan_sha256=digest(plan), archive_fingerprint=binding['archive_fingerprint'],
                card=accepted['card'], root_offset=root['offset'], root_bytes=root['bytes'])


def _destination(output, reviewed):
    if inputs(reviewed['directory'], reviewed['acceptance_sha256']) != reviewed:
        raise ValueError('Backup changed since owner review')
    output = Path(output).absolute()
    source = Path(reviewed['directory']).resolve(strict=True)
    resolved = output.resolve()
    if resolved == source or source in resolved.parents or resolved in source.parents:
        raise ValueError('Preparation output must not overlap the retained backup')
    return output, source


def _create(output):
    output.mkdir(mode=0o700)
    parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return private_directory(output)


def prepare(output, reviewed):
    """Read twice: derive independent range digests, then verify chunk mapping."""
    output, source = _destination(output, reviewed)
    fd = _create(output)
    try:
        write_record(fd, 'request.json', reviewed)
        ranges = capture(source, output/'ranges')
        mapped = prepare_source(source, output/'source', ranges['digests']['root'])
        if inputs(reviewed['directory'], reviewed['acceptance_sha256']) != reviewed:
            raise ValueError('Backup changed during source preparation')
        result = dict(status='prepared-backup-root-source',
            backup_acceptance_sha256=reviewed['acceptance_sha256'],
            backup_plan_sha256=reviewed['plan_sha256'],
            ranges_sha256=digest(ranges), manifest_sha256=mapped['manifest_sha256'],
            root=ranges['digests']['root'], chunk_count=mapped['chunk_count'],
            target_contacted=False, target_written=False, lease_acquired=False,
            filesystem_consistency_qualified=False, restore_authorized=False,
            normal_boot_release_authorized=False)
        write_record(fd, 'acceptance.json', result)
        return result
    except BaseException as exc:
        write_record(fd, 'failure.json', dict(error_type=type(exc).__name__,
            target_contacted=False, target_written=False, preserve_evidence=True))
        raise
    finally:
        os.close(fd)


def check_filesystems(output, reviewed):
    """Retain a byte-identical card image and read-only FAT/ext4 check copies."""
    output, source = _destination(output, reviewed)
    descriptor_directory()
    if not all(filesystem_tool(tool) for tool in ('fsck.fat', 'e2fsck')):
        raise RuntimeError('Filesystem checks require fsck.fat and e2fsck; run make deps')
    # One whole image plus both partition copies. Reserve conservatively for
    # two entire cards, including gaps; lower-level writers recheck free space.
    required = 2 * reviewed['card']['bytes'] + RESERVE_BYTES
    if shutil.disk_usage(output.parent).free < required:
        raise OSError(f'Backup filesystem inspection requires {required} free bytes for retained copies')
    fd = _create(output)
    try:
        write_record(fd, 'request.json', dict(backup=reviewed, required_free_bytes=required))
        image = materialize(source, output/'image')
        health = inspect_filesystems(output/'image', source, output/'health')
        if inputs(reviewed['directory'], reviewed['acceptance_sha256']) != reviewed:
            raise ValueError('Backup changed during filesystem inspection')
        result = dict(status='checked-backup-filesystems',
            backup_acceptance_sha256=reviewed['acceptance_sha256'],
            backup_plan_sha256=reviewed['plan_sha256'],
            image_acceptance_sha256=digest(image), health_sha256=digest(health),
            image=image['image'], check_returncodes=health['check_returncodes'],
            filesystem_consistency_qualified=health['filesystem_consistency_qualified'],
            target_contacted=False, target_written=False, lease_acquired=False,
            repair_performed=False, restore_authorized=False, normal_boot_release_authorized=False)
        write_record(fd, 'acceptance.json', result)
        return result
    except BaseException as exc:
        write_record(fd, 'failure.json', dict(error_type=type(exc).__name__,
            target_contacted=False, target_written=False, preserve_evidence=True))
        raise
    finally:
        os.close(fd)
