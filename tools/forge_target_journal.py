"""Private durable plans for target file transitions; no SSH or target writes."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from forge_target_backup import verify


def private_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise PermissionError('Target journal directory must be private and owned by this user')
    return fd


def write_record(fd, name, record):
    payload = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode()
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(out, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)
    return hashlib.sha256(payload).hexdigest()


def read_record(fd, name):
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(source, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 100 * 1024 * 1024):
            raise PermissionError('Journal record must be a private, owned, bounded regular file')
        return json.load(stream)


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {'schema', 'host', 'before', 'after', 'stage_tokens'}:
        raise ValueError('Unsupported target plan fields; file executor cannot silently ignore service/package actions')
    if type(plan.get('schema')) is not int or plan['schema'] != 1:
        raise ValueError('Unsupported target journal schema')
    if not isinstance(plan.get('host'), str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', plan['host']):
        raise ValueError('Invalid SSH target')
    before, after = plan['before'], plan['after']
    paths = [item['path'] for item in before['files']]
    verify(before, paths)
    verify(after, paths)
    if before['machine_id'] != after['machine_id']:
        raise ValueError('Target machine identity mismatch')
    tokens = plan['stage_tokens']
    if set(tokens) != {'apply', 'restore'}:
        raise ValueError('Missing apply/restore staging tokens')
    all_tokens = []
    for direction in ('apply', 'restore'):
        if not isinstance(tokens[direction], list) or len(tokens[direction]) != len(paths):
            raise ValueError('Staging token count mismatch')
        all_tokens.extend(tokens[direction])
    if (any(not isinstance(t, str) or not re.fullmatch('[0-9a-f]{32}', t) for t in all_tokens)
            or len(set(all_tokens)) != len(all_tokens)):
        raise ValueError('Invalid or reused staging token')
    return plan


def prepare(directory, host, before, after):
    count = len(before['files'])
    plan = validate_plan({'schema': 1, 'host': host, 'before': before, 'after': after,
                          'stage_tokens': {direction: [uuid.uuid4().hex for _ in range(count)]
                                           for direction in ('apply', 'restore')}})
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)  # Exclusive; never overwrite an earlier transaction.
    fd = private_directory(directory)
    try:
        digest = write_record(fd, 'plan.json', plan)
    finally:
        os.close(fd)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return {'journal': str(directory), 'plan_sha256': digest, 'status': 'prepared'}


@contextmanager
def locked(directory):
    """Hold across dispatch/acknowledgement; reconnect must inspect prior events."""
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = validate_plan(read_record(fd, 'plan.json'))
        yield fd, plan
    finally:
        os.close(fd)


def events(fd):
    names = sorted(name for name in os.listdir(fd) if name.startswith('event-'))
    if names != [f'event-{index:06d}.json' for index in range(len(names))]:
        raise ValueError('Journal event sequence is incomplete')
    # A partial write intentionally prevents further dispatch. Do not silently
    # skip a malformed event that might precede an unacknowledged target write.
    return [read_record(fd, name) for name in names]


def append_event(fd, event):
    previous = events(fd)
    if not isinstance(event, dict) or event.get('state') not in ('dispatch', 'acknowledged', 'uncertain'):
        raise ValueError('Invalid target journal event')
    if event.get('direction') not in ('apply', 'restore'):
        raise ValueError('Invalid transition direction')
    write_record(fd, f'event-{len(previous):06d}.json', event)
