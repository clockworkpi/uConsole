"""Bounded no-follow inventory before relaxing private boot permissions.

Reject Forge/recovery names and byte-identical aliases of known credential
images. This is not a general secret scanner for unrelated user boot files.
"""
import hashlib
import os
import stat


def inventory(root, forbidden_hashes):
    root = os.fspath(root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    total, result = 0, []
    device = os.fstat(fd).st_dev
    def walk(directory, prefix, depth):
        nonlocal total
        if depth > 16: raise ValueError('Boot inventory nesting exceeds bound')
        for name in sorted(os.listdir(directory)):
            relative = prefix+name
            if len(result) >= 10000: raise ValueError('Boot inventory entry bound exceeded')
            if any(token in name.casefold() for token in ('forge', 'recovery')):
                raise ValueError('Retained Forge/recovery artifact requires separate review: '+relative)
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if info.st_dev != device or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Boot inventory contains a link, special file or nested mount')
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    if os.fstat(child).st_ino != info.st_ino: raise ValueError('Boot directory changed')
                    result.append(dict(path=relative, kind='directory'))
                    walk(child, relative+'/', depth+1)
                finally: os.close(child)
            else:
                total += info.st_size
                if info.st_nlink != 1 or total > 1024**3: raise ValueError('Boot inventory size/link bound exceeded')
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(child, 'rb') as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_ino != info.st_ino or before.st_dev != device:
                        raise ValueError('Boot file changed during open')
                    hashed, size = hashlib.sha256(), 0
                    while True:
                        data = stream.read(1024*1024)
                        if not data: break
                        size += len(data)
                        if size > info.st_size: raise ValueError('Boot file grew during inventory')
                        hashed.update(data)
                    after = os.fstat(stream.fileno())
                    if (size != info.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                            (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                        raise ValueError('Boot file changed during inventory')
                if hashed.hexdigest() in forbidden_hashes:
                    raise ValueError('Known credential image remains under another name')
                result.append(dict(path=relative, kind='file', size=size, sha256=hashed.hexdigest()))
    try: walk(fd, '', 0)
    finally: os.close(fd)
    return result
