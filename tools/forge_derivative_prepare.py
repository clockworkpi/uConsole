"""Owner review of a forge export's immutable rollback lineage, offline only."""
import os
from pathlib import Path
import stat

from forge_backup_source import _create
from forge_recovery_archive import fingerprint
from forge_recovery_derivative import prepare as prepare_derivative
from forge_recovery_restore_source import opened, validate
from forge_target_journal import private_directory, read_record, write_record


def inputs(backup_directory, source_directory, source_pin, image):
    backup, source, image = (Path(path).absolute() for path in (backup_directory, source_directory, image))
    fd = private_directory(source)
    try:
        original = validate(read_record(fd, 'manifest.json'), source_pin)
        accepted = read_record(fd, 'acceptance.json')
        if accepted != dict(status='verified-root-chunk-source', manifest_sha256=source_pin,
                            chunk_count=len(original['chunks']), target_write_authorized=False,
                            normal_boot_release_authorized=False):
            raise ValueError('Expected completed pinned original-backup source preparation')
    finally:
        os.close(fd)
    with opened(backup, original['root']) as (_, binding):
        if any(binding[key] != original[key] for key in binding):
            raise ValueError('Backup differs from the pinned original source')
    parent = private_directory(image.parent)
    try:
        fd = os.open(image.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or
                    info.st_nlink != 1 or info.st_size != original['card']['bytes']):
                raise PermissionError('Export must be private, owned, single-link and preserve card size')
            identity = list(fingerprint(info))
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    return dict(backup_directory=str(backup), source_directory=str(source), source_sha256=source_pin,
                original=original, image=str(image), image_fingerprint=identity)


def prepare(output, reviewed):
    current = inputs(reviewed['backup_directory'], reviewed['source_directory'],
                     reviewed['source_sha256'], reviewed['image'])
    if current != reviewed:
        raise ValueError('Export or rollback source changed since owner review')
    output = Path(output).absolute()
    resolved = output.resolve()
    for name in ('backup_directory', 'source_directory'):
        source = Path(reviewed[name]).resolve(strict=True)
        if resolved == source or source in resolved.parents or resolved in source.parents:
            raise ValueError('Derivative output must not overlap retained rollback evidence')
    fd = _create(output)
    try:
        write_record(fd, 'request.json', reviewed)
        result = prepare_derivative(reviewed['backup_directory'], reviewed['original'],
            reviewed['source_sha256'], reviewed['image'], output/'derivative')
        if inputs(reviewed['backup_directory'], reviewed['source_directory'],
                  reviewed['source_sha256'], reviewed['image']) != reviewed:
            raise ValueError('Export or rollback source changed during preparation')
        result = dict(result, status='prepared-export-derivative',
                      original_manifest_sha256=reviewed['source_sha256'],
                      target_contacted=False, filesystem_consistency_qualified=False,
                      native_boot_qualified=False, lease_acquired=False)
        write_record(fd, 'acceptance.json', result)
        return result
    except BaseException as exc:
        write_record(fd, 'failure.json', dict(error_type=type(exc).__name__,
            target_contacted=False, target_written=False, preserve_evidence=True))
        raise
    finally:
        os.close(fd)
