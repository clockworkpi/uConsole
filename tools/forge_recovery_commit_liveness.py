"""Read-only local commit budget checks; never renew or touch a watchdog."""
from dataclasses import fields
import json
import os
from pathlib import Path
import stat
import time

from forge_recovery_lease import Lease, clock


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 4096):
            raise ValueError('Unsafe recovery liveness record')
        data = os.read(fd,4097)
        after = os.fstat(fd)
        if ((before.st_size,before.st_mtime_ns,before.st_ctime_ns) !=
                (after.st_size,after.st_mtime_ns,after.st_ctime_ns) or len(data) != before.st_size):
            raise ValueError('Recovery liveness record changed during read')
        def unique(items):
            value = {}
            for key,item in items:
                if key in value: raise ValueError('Duplicate liveness field')
                value[key] = item
            return value
        return json.loads(data,object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite liveness field')))
    finally:
        os.close(fd)


def verify_lease(value, binding, owner, accepted, now):
    if not isinstance(value,dict) or set(value) != {field.name for field in fields(Lease)}:
        raise ValueError('Unexpected local lease state')
    lease = Lease(**value)
    for number in (lease.started,lease.sampled,lease.deadline,now): clock(number)
    if ((lease.nonce,lease.boot_id,lease.owner) != (binding['nonce'],binding['boot_id'],owner) or
            not lease.started <= lease.sampled <= now or
            not lease.sampled < lease.deadline <= min(lease.started+86400,lease.sampled+300) or
            type(lease.sequence) is not int or not 1 <= lease.sequence <= 2**53-1 or
            type(lease.last_seconds) is not int or not 1 <= lease.last_seconds <= 300 or
            json.dumps(lease.receipt(),sort_keys=True,allow_nan=False) != json.dumps(accepted,sort_keys=True,allow_nan=False)):
        raise ValueError('Local lease differs from the acknowledged host renewal')
    lease.check(now)
    return lease


def budget(binding, owner, accepted):
    sampled = clock(time.monotonic())
    lease = verify_lease(private_json('/run/forge-lease/deadline.json'),binding,owner,accepted,sampled)
    tokens = Path('/proc/cmdline').read_text().split()
    for prefix,expected in (('uconsole.recovery_owner=',owner),('uconsole.recovery_lease=','1')):
        if [value for value in tokens if value.startswith(prefix)] != [prefix+expected]:
            raise ValueError('Running recovery lease selection differs')
    ready = private_json('/run/forge-watchdog.ready')
    if (not isinstance(ready,dict) or set(ready) != {'pid','maximum_lifetime','lease'} or
            ready['lease'] is not True or type(ready['pid']) is not int or ready['pid'] <= 1 or
            type(ready['maximum_lifetime']) is not int or ready['maximum_lifetime'] != 86400):
        raise ValueError('Recovery watchdog readiness differs')
    process = Path('/proc',str(ready['pid']))
    before = (process/'stat').read_text()
    os.kill(ready['pid'],0)
    expected = Path('/sys/class/watchdog/watchdog0/dev').read_text().strip()
    held = False
    for path in (process/'fd').iterdir():
        try: info = path.stat()
        except FileNotFoundError: continue
        if stat.S_ISCHR(info.st_mode) and f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' == expected:
            held = True
    # PID/starttime identity, not mutable CPU accounting fields in /proc/stat.
    def identity(record):
        head,separator,tail = record.rpartition(') ')
        parts = tail.split()
        if not separator or len(parts) < 20 or parts[0] in ('Z','X','x'):
            raise ValueError('Invalid or dead watchdog owner')
        return head,parts[19]
    if (not held or identity(before) != identity((process/'stat').read_text()) or
            Path('/sys/class/watchdog/watchdog0/state').read_text().strip() != 'active' or
            Path('/sys/class/watchdog/watchdog0/timeout').read_text().strip() != '15'):
        raise ValueError('Watchdog ownership, activity or timeout differs')
    finished = clock(time.monotonic())
    if finished < sampled: raise ValueError('Recovery clock reversed during budget check')
    lease.check(finished)
    return lease.deadline-finished
