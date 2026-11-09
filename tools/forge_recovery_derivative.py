"""Qualify a private root-only derivative without target access or authority.

The original backup remains immutable. This is byte lineage, not filesystem,
kernel, boot compatibility, or permission to deploy. Boot/layout modifications
require a separate workflow and are deliberately rejected here.
"""
import copy
import gzip
import hashlib
import os
from pathlib import Path
import stat

from forge_recovery_archive import fingerprint
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_source import CHUNK_BYTES, digest, opened, root_checked, sha, validate as validate_original
from forge_target_journal import private_directory, read_record, write_record


def validate(manifest, pin):
    """Validate complete byte lineage without accessing host or target files."""
    if not sha(pin) or not isinstance(manifest, dict) or digest(manifest) != pin:
        raise ValueError('Derivative manifest differs from pinned digest')
    fields = {'schema', 'kind', 'original_manifest', 'original_manifest_sha256',
              'backup_plan_sha256', 'backup_acceptance_sha256', 'original_card', 'original_root',
              'image_fingerprint', 'card', 'root', 'prefix', 'suffix', 'chunk_bytes', 'chunks',
              'changed_chunks', 'target_write_authorized', 'normal_boot_release_authorized',
              'filesystem_consistency_qualified', 'native_boot_qualified'}
    if (set(manifest) != fields or type(manifest['schema']) is not int or manifest['schema'] != 1 or
            manifest['kind'] != 'root-only-image-derivative' or
            any(manifest[key] is not False for key in ('target_write_authorized',
                'normal_boot_release_authorized', 'filesystem_consistency_qualified', 'native_boot_qualified'))):
        raise ValueError('Unsupported derivative manifest')
    original = manifest['original_manifest']
    validate_original(original, manifest['original_manifest_sha256'])
    for key in ('backup_plan_sha256', 'backup_acceptance_sha256'):
        if manifest[key] != original[key]:
            raise ValueError('Derivative backup lineage differs')
    if not exact(manifest['original_card'], original['card']) or not exact(manifest['original_root'], original['root']):
        raise ValueError('Derivative original-image lineage differs')
    card = manifest['card']
    if (not isinstance(card, dict) or set(card) != {'bytes', 'sha256'} or
            type(card['bytes']) is not int or card['bytes'] != original['card']['bytes'] or not sha(card['sha256'])):
        raise ValueError('Derivative card geometry differs')
    root = manifest['root']
    root_checked(root, card['bytes'])
    if any(root[key] != original['root'][key] for key in ('offset', 'bytes')):
        raise ValueError('Derivative root geometry differs')
    for key, offset, size in (('prefix', 0, root['offset']),
                             ('suffix', root['offset']+root['bytes'], card['bytes']-root['offset']-root['bytes'])):
        value = manifest[key]
        if (not isinstance(value, dict) or set(value) != {'offset', 'bytes', 'sha256'} or
                type(value['offset']) is not int or value['offset'] != offset or
                type(value['bytes']) is not int or value['bytes'] != size or not sha(value['sha256'])):
            raise ValueError('Invalid derivative protected range')
    identity = manifest['image_fingerprint']
    if (not isinstance(identity, list) or len(identity) != 8 or
            any(type(value) is not int or value < 0 for value in identity) or identity[2] != card['bytes']):
        raise ValueError('Invalid derivative file identity')
    if type(manifest['chunk_bytes']) is not int or manifest['chunk_bytes'] != CHUNK_BYTES:
        raise ValueError('Invalid derivative chunk size')
    chunks = manifest['chunks']
    if not isinstance(chunks, list) or len(chunks) != len(original['chunks']):
        raise ValueError('Derivative chunk count differs')
    changed = []
    for index, (chunk, before) in enumerate(zip(chunks, original['chunks'])):
        if (not isinstance(chunk, dict) or set(chunk) != {'offset', 'bytes', 'sha256'} or
                any(type(chunk[key]) is not int or chunk[key] != before[key] for key in ('offset', 'bytes')) or
                not sha(chunk['sha256'])):
            raise ValueError('Invalid derivative chunk map')
        if chunk != before:
            changed.append(index)
    if not exact(manifest['changed_chunks'], changed):
        raise ValueError('Derivative changed-chunk index differs')
    if not changed and (not exact(card, original['card']) or not exact(root, original['root'])):
        raise ValueError('Unchanged derivative has inconsistent image hashes')
    return manifest


def load(directory, pin):
    """Require the sealed manifest, its original inputs and completed receipt."""
    fd = private_directory(directory)
    try:
        manifest = validate(read_record(fd, 'manifest.json'), pin)
        inputs = read_record(fd, 'inputs.json')
        accepted = read_record(fd, 'acceptance.json')
    finally:
        os.close(fd)
    if (not isinstance(inputs, dict) or set(inputs) != {'backup_directory', 'original_manifest',
            'original_manifest_sha256', 'image', 'image_fingerprint'} or
            not exact(inputs['original_manifest'], manifest['original_manifest']) or
            inputs['original_manifest_sha256'] != manifest['original_manifest_sha256'] or
            not exact(inputs['image_fingerprint'], manifest['image_fingerprint'])):
        raise ValueError('Derivative retained inputs differ')
    for key in ('backup_directory', 'image'):
        path = inputs[key]
        if (not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute() or
                '..' in Path(path).parts or any(ord(char) < 32 for char in path)):
            raise ValueError('Invalid derivative source path')
    expected = dict(status='verified-root-only-derivative', manifest_sha256=pin,
        changed_chunks=len(manifest['changed_chunks']), card=manifest['card'], root=manifest['root'],
        target_written=False, target_write_authorized=False, normal_boot_release_authorized=False)
    if not exact(accepted, expected):
        raise ValueError('Derivative qualification is incomplete or differs')
    return manifest, inputs


def stream(directory, pin, consume, *, heartbeat=None):
    """Deliver verified derived root chunks; never claim a target was restored.

    The rollback archive must still match its original identity and receipts.
    Prefix verification precedes chunk delivery. Full root/card hashes, suffix,
    EOF and source stability are required before returning stream completion.
    """
    if not callable(consume) or heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected consumer and optional heartbeat callables')
    manifest, inputs = load(directory, pin)
    original = manifest['original_manifest']
    image = Path(inputs['image'])
    parent = private_directory(image.parent)
    fd = None
    try:
        with opened(inputs['backup_directory'], original['root']) as (_, binding):
            if any(not exact(binding[key], original[key]) for key in binding):
                raise ValueError('Rollback backup differs from qualified derivative lineage')
            fd = os.open(image.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            initial = os.fstat(fd)
            if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid() or
                    initial.st_mode & 0o077 or initial.st_nlink != 1 or
                    not exact(list(fingerprint(initial)), manifest['image_fingerprint'])):
                raise ValueError('Derivative source identity differs before delivery')
            whole, root = hashlib.sha256(), hashlib.sha256()
            def read(offset, count):
                if heartbeat is not None:
                    heartbeat()
                data = os.pread(fd, count, offset)
                if len(data) != count:
                    raise ValueError('Derivative source ended early')
                whole.update(data)
                return data
            def protected(name):
                value = manifest[name]
                hashed, count = hashlib.sha256(), 0
                while count < value['bytes']:
                    data = read(value['offset']+count, min(1048576, value['bytes']-count))
                    hashed.update(data)
                    count += len(data)
                if hashed.hexdigest() != value['sha256']:
                    raise ValueError('Derivative protected '+name+' checksum differs')
            protected('prefix')
            for chunk in manifest['chunks']:
                data = read(manifest['root']['offset']+chunk['offset'], chunk['bytes'])
                if hashlib.sha256(data).hexdigest() != chunk['sha256']:
                    raise ValueError('Derivative chunk differs before delivery')
                root.update(data)
                consume(dict(chunk), data)
            protected('suffix')
            if (whole.hexdigest() != manifest['card']['sha256'] or
                    root.hexdigest() != manifest['root']['sha256'] or
                    os.pread(fd, 1, initial.st_size)):
                raise ValueError('Derivative full-image checksum or EOF differs')
            if heartbeat is not None:
                heartbeat()
            if (fingerprint(initial) != fingerprint(os.fstat(fd)) or
                    fingerprint(initial) != fingerprint(os.stat(image.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError('Derivative changed during delivery')
        return dict(status='verified-derivative-stream', manifest_sha256=pin,
                    chunks=len(manifest['chunks']), root=manifest['root'],
                    target_restore_verified=False, normal_boot_release_authorized=False)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def prepare(backup_directory, original_manifest, original_pin, image, destination, *, heartbeat=None):
    """Compare every byte against the pinned archive and seal root chunk hashes.

    No writable image descriptor, mount, repair, target transport or deployment
    API is used. Failures retain incomplete evidence, not a success receipt.
    """
    if heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected heartbeat callable')
    original = copy.deepcopy(original_manifest)
    validate_original(original, original_pin)
    image = Path(os.path.abspath(image))
    backup_directory = Path(os.path.abspath(backup_directory))
    if any(len(str(path)) > 4096 or any(ord(char) < 32 for char in str(path))
           for path in (image, backup_directory)):
        raise ValueError('Invalid derivative source path')
    parent = private_directory(image.parent)
    image_fd = output = None
    try:
        image_fd = os.open(image.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        initial = os.fstat(image_fd)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid() or
                initial.st_mode & 0o077 or initial.st_nlink != 1 or
                initial.st_size != original['card']['bytes']):
            raise PermissionError('Derivative must be private, owned, single-link and preserve card size')
        with opened(backup_directory, original['root']) as (archive_fd, binding):
            if any(binding[key] != original[key] for key in binding):
                raise ValueError('Original backup differs from pinned source manifest')
            destination = Path(destination).absolute()
            destination.mkdir(mode=0o700)
            directory_parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_parent)
            finally:
                os.close(directory_parent)
            output = private_directory(destination)
            result = dict(status='incomplete', target_written=False,
                          target_write_authorized=False, normal_boot_release_authorized=False)
            try:
                write_record(output, 'inputs.json', dict(
                    backup_directory=str(Path(backup_directory).absolute()),
                    original_manifest=original, original_manifest_sha256=original_pin,
                    image=str(image), image_fingerprint=list(fingerprint(initial))))
                whole_original, whole_derived = hashlib.sha256(), hashlib.sha256()
                root_original, root_derived = hashlib.sha256(), hashlib.sha256()
                prefix, suffix = hashlib.sha256(), hashlib.sha256()
                root = original['root']
                ranges = [('prefix', 0, root['offset']),
                          ('root', root['offset'], root['bytes']),
                          ('suffix', root['offset']+root['bytes'],
                           initial.st_size-root['offset']-root['bytes'])]
                chunks, changed, index = [], [], 0
                with os.fdopen(os.dup(archive_fd), 'rb') as raw, gzip.GzipFile(fileobj=raw, mode='rb') as source:
                    for name, start, length in ranges:
                        offset = 0
                        while offset < length:
                            if heartbeat is not None:
                                heartbeat()
                            count = min(CHUNK_BYTES if name == 'root' else 1048576, length-offset)
                            baseline = source.read(count)
                            candidate = os.pread(image_fd, count, start+offset)
                            if len(baseline) != count or len(candidate) != count:
                                raise ValueError('Original or derivative ended early')
                            whole_original.update(baseline)
                            whole_derived.update(candidate)
                            if name != 'root':
                                if baseline != candidate:
                                    raise ValueError('Derivative changed protected '+name+' bytes')
                                (prefix if name == 'prefix' else suffix).update(candidate)
                            else:
                                before = dict(offset=offset, bytes=count, sha256=hashlib.sha256(baseline).hexdigest())
                                if before != original['chunks'][index]:
                                    raise ValueError('Original backup chunk differs from pinned source')
                                chunk = dict(offset=offset, bytes=count, sha256=hashlib.sha256(candidate).hexdigest())
                                chunks.append(chunk)
                                if chunk != before:
                                    changed.append(index)
                                root_original.update(baseline)
                                root_derived.update(candidate)
                                index += 1
                            offset += count
                    if source.read(1) or os.pread(image_fd, 1, initial.st_size):
                        raise ValueError('Original or derivative exceeds approved card size')
                if (whole_original.hexdigest() != original['card']['sha256'] or
                        root_original.hexdigest() != root['sha256'] or index != len(original['chunks'])):
                    raise ValueError('Original backup checksum differs from pinned source')
                if heartbeat is not None:
                    heartbeat()
                if (fingerprint(initial) != fingerprint(os.fstat(image_fd)) or
                        fingerprint(initial) != fingerprint(os.stat(image.name, dir_fd=parent, follow_symlinks=False))):
                    raise ValueError('Derivative changed during qualification')
                manifest = dict(schema=1, kind='root-only-image-derivative',
                    original_manifest=original,
                    original_manifest_sha256=original_pin,
                    backup_plan_sha256=original['backup_plan_sha256'],
                    backup_acceptance_sha256=original['backup_acceptance_sha256'],
                    original_card=original['card'], original_root=root,
                    image_fingerprint=list(fingerprint(initial)),
                    card=dict(bytes=initial.st_size, sha256=whole_derived.hexdigest()),
                    root=dict(offset=root['offset'], bytes=root['bytes'], sha256=root_derived.hexdigest()),
                    prefix=dict(offset=0, bytes=root['offset'], sha256=prefix.hexdigest()),
                    suffix=dict(offset=root['offset']+root['bytes'], bytes=ranges[2][2], sha256=suffix.hexdigest()),
                    chunk_bytes=CHUNK_BYTES, chunks=chunks, changed_chunks=changed,
                    target_write_authorized=False, normal_boot_release_authorized=False,
                    filesystem_consistency_qualified=False, native_boot_qualified=False)
            except BaseException as exc:
                result['error'] = type(exc).__name__+': '+str(exc)
                write_record(output, 'acceptance.json', result)
                raise
        # opened() validates archive identity and retained receipts on exit.
        # Only then may successful lineage evidence become visible.
        if (fingerprint(initial) != fingerprint(os.fstat(image_fd)) or
                fingerprint(initial) != fingerprint(os.stat(image.name, dir_fd=parent, follow_symlinks=False))):
            raise ValueError('Derivative changed during qualification')
        validate(manifest, digest(manifest))
        manifest_pin = write_record(output, 'manifest.json', manifest)
        result.update(status='verified-root-only-derivative', manifest_sha256=manifest_pin,
                      changed_chunks=len(changed), card=manifest['card'], root=manifest['root'])
        write_record(output, 'acceptance.json', result)
        return result
    except BaseException as exc:
        # A failure in the source context's exit must remain incomplete too.
        if output is not None:
            try:
                write_record(output, 'acceptance.json', dict(status='incomplete',
                    error=type(exc).__name__+': '+str(exc), target_written=False,
                    target_write_authorized=False, normal_boot_release_authorized=False))
            except BaseException:
                pass
        raise
    finally:
        if output is not None:
            os.close(output)
        if image_fd is not None:
            os.close(image_fd)
        os.close(parent)
