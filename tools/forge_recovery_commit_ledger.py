"""RAM-only per-boot commit fencing; provision only after verified RAM identity.

An old boot's worker cannot execute in a new boot because the worker checks its
bound boot UUID before opening this ledger. Existing target attempt primitives
provide exclusive execution, pre-start fencing and durable in-RAM receipts.
"""
import os
from pathlib import Path
import re
import stat

from forge_recovery_ledger import fence, run, validate_request


def request(plan, pin, attempt):
    value=dict(plan_sha256=pin,nonce=attempt,
               direction='apply' if plan['operation']=='install-hold' else 'restore')
    validate_request(attempt,value)
    return value


def provision(boot_id, pin):
    if (os.geteuid()!=0 or not isinstance(boot_id,str) or not re.fullmatch(
            '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot_id) or
            not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin)):
        raise ValueError('RAM commit ledger requires root and pinned boot/plan identities')
    fd=os.open('/run',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in ('forge-boot-commits',boot_id,pin):
            try:
                os.mkdir(name,mode=0o700,dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(child)
            if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
                os.close(child)
                raise ValueError('Unsafe RAM commit ledger directory')
            os.close(fd)
            fd=child
        os.fsync(fd)
    finally:
        os.close(fd)
    return Path('/run/forge-boot-commits')/boot_id/pin
