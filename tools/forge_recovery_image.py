"""Internal bounded image publication; caller supplies policy and durable intent.

Not a deployment CLI. The owner must verify target identity, persistent private
mount policy and original destination absence before calling this primitive.
Failures retain scratch evidence and imply uncertain effects, never rollback.
"""
import hashlib
import os
import re
import stat

from forge_target_files import parent_fd, publish_absent

MAX_IMAGE = 128 * 1024 * 1024


def parameters(digest, size, token):
    if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or
            type(size) is not int or not 1 <= size <= MAX_IMAGE or
            not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token)):
        raise ValueError('Invalid image digest, size or journal token')


def private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Image and parent must be private and owned by executor')
    if not directory and info.st_nlink != 1:
        raise ValueError('Image must be singly linked')


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def verified(fd, digest, size):
    before = os.fstat(fd)
    private(before)
    if before.st_size != size:
        raise ValueError('Image size differs from approved size')
    os.lseek(fd, 0, os.SEEK_SET)
    hashed = hashlib.sha256()
    remaining = size
    while remaining:
        data = os.read(fd, min(1024 * 1024, remaining))
        if not data:
            raise ValueError('Incomplete image read')
        hashed.update(data)
        remaining -= len(data)
    if os.read(fd, 1) or hashed.hexdigest() != digest or signature(before) != signature(os.fstat(fd)):
        raise ValueError('Image changed or differs from approved digest')
    return before


def publish(source, destination, digest, size, token):
    parameters(digest, size, token)
    with parent_fd(source) as (source_parent, source_name):
        source_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent)
    try:
        before = verified(source_fd, digest, size)
        with parent_fd(destination) as (parent, name):
            private(os.fstat(parent), directory=True)
            # Reject even matching existing files. Idempotent reconciliation
            # requires owner journal state, not merely matching bytes.
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(destination)
            temporary = '.forge-image-' + token
            out = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=parent)
            try:
                private(os.fstat(out))
                os.lseek(source_fd, 0, os.SEEK_SET)
                remaining = size
                while remaining:
                    data = os.read(source_fd, min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError('Source truncated during copy')
                    view = memoryview(data)
                    while view:
                        count = os.write(out, view)
                        if count <= 0:
                            raise OSError('Image write made no progress')
                        view = view[count:]
                    remaining -= len(data)
                if signature(before) != signature(os.fstat(source_fd)):
                    raise ValueError('Source changed during copy')
                os.fsync(out)
                staged = verified(out, digest, size)
                if signature(staged) != signature(os.stat(temporary, dir_fd=parent, follow_symlinks=False)):
                    raise ValueError('Staging path changed')
                publish_absent(parent, temporary, name)
                os.fsync(parent)
                final = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    verified(final, digest, size)
                finally:
                    os.close(final)
            finally:
                os.close(out)
        return {'status': 'published', 'sha256': digest, 'size': size}
    finally:
        os.close(source_fd)


def remove(destination, digest, size, token):
    parameters(digest, size, token)
    with parent_fd(destination) as (parent, name):
        private(os.fstat(parent), directory=True)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = verified(fd, digest, size)
            if signature(before) != signature(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                raise ValueError('Published image path changed')
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(fd)
    return {'status': 'removed', 'sha256': digest, 'size': size}


def inspect(destination, digest, size, token):
    """Observe destination/scratch without inferring ownership or safe retry."""
    parameters(digest, size, token)
    with parent_fd(destination) as (parent, name):
        try:
            private(os.fstat(parent), directory=True)
            parent_private = True
        except ValueError:
            parent_private = False
        result = {'parent_private': parent_private, 'retry_authorized': False}
        for field, candidate in (('destination', name), ('scratch', '.forge-image-' + token)):
            try:
                before = os.stat(candidate, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                result[field] = {'state': 'absent'}
                continue
            record = {'size': before.st_size, 'mode': stat.S_IMODE(before.st_mode),
                      'uid': before.st_uid, 'links': before.st_nlink}
            result[field] = record
            try:
                private(before)
            except ValueError:
                record['state'] = 'unsafe-metadata'
                continue
            if not parent_private:
                record['state'] = 'unsafe-parent'
                continue
            if before.st_size != size:
                record['state'] = 'different-size'
                continue
            fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                         getattr(os, 'O_NOATIME', 0), dir_fd=parent)
            try:
                if signature(before) != signature(os.fstat(fd)):
                    record['state'] = 'changed'
                    continue
                try:
                    verified(fd, digest, size)
                    record['state'] = 'matching-bytes'
                except ValueError:
                    record['state'] = 'mismatch-or-changed'
                if signature(before) != signature(os.stat(candidate, dir_fd=parent, follow_symlinks=False)):
                    record['state'] = 'changed'
            finally:
                os.close(fd)
        return result
