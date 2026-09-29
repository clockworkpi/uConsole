"""Boot-wide RAM fencing for root restoration, never write authorization.

All restore plans in one RAM boot share a ledger. An incomplete attempt blocks
new plans as well as retries. This first recovery policy requires an explicitly
verified new recovery boot before planning another attempt; it never fabricates
a successful receipt or discards incomplete intent to permit a retry.
The enclosing worker must first verify RAM identity and persistent hold.
"""
import copy
import os
from pathlib import Path
import re
import stat

from forge_recovery_bootplan import digest
from forge_recovery_commit_protocol import exact
from forge_recovery_ledger import run as ledger_run, fence as ledger_fence, validate_request
from forge_recovery_operation_contract import source_kind

ROOT=Path('/run/forge-root-restores')


def check_boot(boot_id):
    if (not isinstance(boot_id,str) or not re.fullmatch(
            '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot_id) or
            Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=boot_id):
        raise ValueError('Restore worker belongs to a different boot')


def request(plan,pin,attempt):
    if (not isinstance(plan,dict) or not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin) or
            digest(plan)!=pin or type(plan.get('schema')) is not int or plan['schema']!=1 or
            any(plan.get(key) is not False for key in ('root_write_authorized','boot_write_authorized',
                'whole_card_write_authorized','normal_boot_release_authorized'))):
        raise ValueError('Expected pinned non-authorizing root restore plan')
    source_kind(plan)  # Both operations share the same boot-wide writer fence.
    value=dict(plan_sha256=pin,direction='restore',nonce=attempt)
    validate_request(attempt,value)
    return value


def checked_result(result,plan,pin):
    written=result.get('bytes_written') if isinstance(result,dict) else None
    root=plan['root_after']
    if type(written) is not int or not 0<=written<=root['bytes'] or written%512:
        raise ValueError('Invalid restored byte count')
    expected=dict(status='root-restore-verified',plan_sha256=pin,
        source_manifest_sha256=plan['source_manifest_sha256'],boot_id=plan['binding']['boot_id'],
        root=root,prefix=plan['prefix_guard'],suffix=plan['suffix_guard'],bytes_written=written,
        protected_ranges_verified=True,boot_unmounted=True,
        normal_boot_release_authorized=False,physical_restore_qualified=False)
    if not exact(result,expected): raise ValueError('Root restore completion receipt differs from the pinned plan')
    return copy.deepcopy(result)


def run(directory,plan,pin,attempt,effect):
    plan=copy.deepcopy(plan)
    value=request(plan,pin,attempt)
    boot_id=plan['binding']['boot_id']
    check_boot(boot_id)
    if Path(directory)!=ROOT/boot_id or not callable(effect):
        raise ValueError('Expected this boot-wide restore ledger and effect')
    def checked_effect():
        check_boot(boot_id)
        result=checked_result(effect(),plan,pin)
        check_boot(boot_id)
        return result
    # The shared boot directory means an incomplete DIFFERENT plan also blocks
    # execution. The lock is held across durable intent, effect and receipt.
    result=ledger_run(directory,attempt,value,checked_effect)
    check_boot(boot_id)
    return checked_result(result,plan,pin)


def fence(directory,plan,pin,attempt):
    plan=copy.deepcopy(plan)
    value=request(plan,pin,attempt)
    boot_id=plan['binding']['boot_id']
    check_boot(boot_id)
    if Path(directory)!=ROOT/boot_id: raise ValueError('Expected this boot-wide restore ledger')
    result=ledger_fence(directory,attempt,value)
    check_boot(boot_id)
    if result['status']=='completed': checked_result(result['result'],plan,pin)
    return dict(result,boot_id=boot_id,requires_new_recovery_boot=result['status']=='incomplete',
                root_write_authorized=False,normal_boot_release_authorized=False)


def provision(boot_id):
    check_boot(boot_id)
    if os.geteuid()!=0: raise ValueError('Restore ledger requires verified root recovery')
    fd=os.open('/run',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in ('forge-root-restores',boot_id):
            try:
                os.mkdir(name,mode=0o700,dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(child)
            if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
                os.close(child)
                raise ValueError('Unsafe RAM root-restore ledger directory')
            os.close(fd)
            fd=child
        os.fsync(fd)
    finally:
        os.close(fd)
    return ROOT/boot_id
