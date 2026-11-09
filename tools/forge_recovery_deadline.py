"""Atomic RAM-only lease deadline exchange for independent timer owners.

No watchdog is touched here. Production callers must use a private directory
on /run, pin the initial lease from recovery startup, and treat read errors as
expiry. Publication does not itself authorize or prove a physical operation.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import stat

from forge_recovery_lease import Lease, clock
from forge_recovery_lease_socket import decode


def validate(previous, candidate, now):
    """Accept a fresh snapshot, never an unbounded or resurrected deadline."""
    for field in ('started', 'sampled', 'deadline'):
        clock(getattr(candidate, field))
    candidate.check(now)
    if (candidate.nonce, candidate.boot_id, candidate.owner, candidate.started) != (
            previous.nonce, previous.boot_id, previous.owner, previous.started):
        raise ValueError('Deadline belongs to a different recovery session')
    for field in ('sequence', 'last_seconds'):
        if type(getattr(candidate, field)) is not int:
            raise ValueError('Invalid deadline integer')
    if (not 0 <= candidate.sequence <= 2**53-1 or
            not 0 <= candidate.last_seconds <= 300 or
            candidate.sampled < candidate.started or candidate.deadline <= candidate.sampled or
            candidate.sampled < previous.sampled or candidate.sampled > now or
            candidate.deadline < previous.deadline or
            candidate.deadline > candidate.started + 86400 or
            candidate.deadline > candidate.sampled + 300):
        raise ValueError('Invalid deadline bounds')
    if candidate.sequence == previous.sequence:
        if (candidate.deadline, candidate.last_seconds) != (previous.deadline, previous.last_seconds):
            raise ValueError('Conflicting deadline at the same sequence')
    elif (candidate.sequence < previous.sequence or candidate.sampled >= previous.deadline or
          candidate.last_seconds == 0):
        raise ValueError('Stale or late deadline renewal')
    return candidate


class DeadlineFile:
    """Directory-fd anchored file; each reader keeps its last accepted state."""
    def __init__(self, directory, initial):
        path = Path(directory).absolute()
        if path.resolve() != path:
            raise ValueError('Deadline directory must not traverse symlinks')
        self.fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(self.fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(self.fd)
            self.fd = None
            raise ValueError('Deadline directory must be private and owned')
        self.state = initial
        self.published = None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _read(self, *, allow_unlinked=False):
        fd = os.open('deadline.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or
                    stat.S_IMODE(before.st_mode) != 0o600 or
                    before.st_nlink not in ((0,1) if allow_unlinked else (1,)) or
                    not 0 < before.st_size <= 4096):
                raise ValueError('Invalid deadline file metadata')
            data = os.read(fd, 4097)
            after = os.fstat(fd)
            # Atomic publication can unlink an already-open immutable snapshot.
            # That changes nlink/ctime, not its content or permissions. Readers
            # may finish validating that bounded old lease; publishers still
            # require their current named inode to remain linked and unchanged.
            unlinked=(allow_unlinked and before.st_nlink==1 and after.st_nlink==0 and
                      after.st_ctime_ns>=before.st_ctime_ns)
            if ((before.st_size,before.st_mtime_ns,before.st_mode,before.st_uid,before.st_gid) !=
                    (after.st_size,after.st_mtime_ns,after.st_mode,after.st_uid,after.st_gid) or
                    not unlinked and (before.st_nlink,before.st_ctime_ns)!=(after.st_nlink,after.st_ctime_ns) or
                    len(data) != before.st_size):
                raise ValueError('Deadline changed while being read')
            return data
        finally:
            os.close(fd)

    def read(self, now, *, finished=None):
        started=clock(now)
        value = decode(self._read(allow_unlinked=True))
        if finished is not None:
            now=clock(finished())
            if now<started: raise ValueError('Deadline reader clock reversed')
        if type(value) is not dict or set(value) != set(asdict(self.state)):
            raise ValueError('Invalid deadline record')
        try:
            candidate = Lease(**value)
            validate(self.state, candidate, now)
        except (TypeError, OverflowError) as exc:
            raise ValueError('Invalid deadline types') from exc
        self.state = candidate
        return candidate

    def publish(self, candidate, now):
        validate(self.state, candidate, now)
        data = json.dumps(asdict(candidate), sort_keys=True, allow_nan=False).encode()
        if self.published is not None and self._read() != self.published:
            raise ValueError('Published deadline was replaced')
        temporary = 'deadline-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if self.published is None:
                # Exclusive initial publication; do not adopt an earlier daemon's
                # file or silently reset the session after a process restart.
                os.link(temporary, 'deadline.json', src_dir_fd=self.fd, dst_dir_fd=self.fd,
                        follow_symlinks=False)
                os.unlink(temporary, dir_fd=self.fd)
            else:
                os.replace(temporary, 'deadline.json', src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
            self.state, self.published = candidate, data
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass
