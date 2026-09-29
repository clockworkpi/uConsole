"""Fenced, root-read-only observation of an uncertain physical restore.

The only cleanup is unmounting this attempt's exact private read-only boot
mount. No root bytes, boot files or original attempt receipts are changed.
An expired boot's ledger is never recreated as evidence of its outcome.
"""
import copy
import math
import os
from pathlib import Path
import stat
import subprocess

from forge_recovery_bootcommit import (check_mount, commit_timer, ensure_lock_directory,
                                      mounted_boot, validate as validate_hold)
from forge_recovery_claim import claim_root, range_digest
from forge_recovery_commit_inspect import inspect_files
from forge_recovery_commit_liveness import budget
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_identity import checker, observe_local
from forge_recovery_restore_plan import binding_checked
import forge_recovery_restore_ledger as ledger
from forge_target_ssh import target_lock


class Observation:
    def __init__(self, plan, pin, attempt, boot_id):
        self.plan = copy.deepcopy(plan)
        ledger.request(self.plan,pin,attempt)
        self.binding = copy.deepcopy(self.plan['binding'])
        self.binding['boot_id'] = boot_id
        binding_checked(self.binding)
        self.point = Path('/run/forge-boot-commit-'+attempt)
        self.stale = False

    def check(self, *, allow_stale=False):
        self.stale = False
        def observe(source):
            value = observe_local(source)
            if isinstance(value,dict) and set(value)=={'identity','bootloader'}:
                rows = value['identity']['mountinfo'].splitlines()
                matches = [row for row in rows if len(row.split(' - ')[0].split())>=5 and
                           row.split(' - ')[0].split()[4]==str(self.point)]
                if matches:
                    if not allow_stale or len(matches)!=1:
                        raise ValueError('Unqualified or ambiguous restore inspection mount')
                    check_mount(self.point,self.binding['device']+'p1',read_only=True)
                    if 'rw' in matches[0].split(' - ')[0].split()[5].split(','):
                        raise ValueError('Restore inspection cannot clean up a writable mount')
                    value = copy.deepcopy(value)
                    value['identity']['mountinfo'] = '\n'.join(row for row in rows if row!=matches[0])
                    self.stale = True
            return value
        return checker(self.binding,observe=observe)()


def fence(plan, pin, attempt, *, observed_boot_id):
    view = Observation(plan,pin,attempt,observed_boot_id)
    view.check(allow_stale=True)  # Before RAM directory creation or unmount.
    if observed_boot_id == view.plan['binding']['boot_id']:
        directory = ledger.provision(observed_boot_id)
        outcome = ledger.fence(directory,view.plan,pin,attempt)
    else:
        outcome = dict(status='previous-boot-ended',previous_boot_id=view.plan['binding']['boot_id'],
                       request=ledger.request(view.plan,pin,attempt))
    ensure_lock_directory()
    removed = False
    empty_removed = False
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        with claim_root(view.binding['device'],view.binding['extent'],lambda:view.check(allow_stale=True)):
            view.check(allow_stale=True)
            if view.stale:
                check_mount(view.point,view.binding['device']+'p1',read_only=True)
                subprocess.run(['umount',str(view.point)],check=True,timeout=10)
                if os.path.ismount(view.point): raise RuntimeError('Read-only restore mount persists')
                view.point.rmdir()  # Empty RAM mountpoint, not card contents.
                removed = True
            elif view.point.exists() or view.point.is_symlink():
                info = view.point.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_gid!=0 or
                        stat.S_IMODE(info.st_mode)!=0o700):
                    raise ValueError('Unsafe abandoned restore RAM mountpoint')
                view.point.rmdir()  # Fails closed if nonempty; no recursive deletion.
                empty_removed = True
            view.check()
    view.check()
    return dict(status='restore-fenced',plan_sha256=pin,attempt=attempt,boot_id=observed_boot_id,
                outcome=outcome,stale_read_only_boot_unmounted=removed,
                stale_empty_mountpoint_removed=empty_removed,root_written=False,
                boot_files_written=False,normal_boot_release_authorized=False)


def inspect(plan, pin, attempt, hold_plan, hold_pin, *, observed_boot_id, owner, accepted):
    """Inspect held boot dependencies and bind them to unchanged prefix bytes.

    Call fence first. A whole-card hash and host classification must follow;
    this inspection alone says nothing about restoration of the root bytes.
    """
    view = Observation(plan,pin,attempt,observed_boot_id)
    hold_plan = copy.deepcopy(validate_hold(hold_plan,hold_pin))
    if (hold_pin!=view.plan['hold_plan_sha256'] or
            not exact(hold_plan['after'],view.plan['held_files']) or
            not exact(hold_plan['image_dependency'],view.plan['image_dependency']) or
            owner!=view.binding['lease_owner']):
        raise ValueError('Restore inspection hold or lease owner differs')
    view.check()
    remaining = budget(view.binding,owner,accepted)
    if type(remaining) not in (int,float) or not math.isfinite(remaining) or not 180<=remaining<=300:
        raise ValueError('Insufficient restore read-only inspection budget')
    ensure_lock_directory()
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        with claim_root(view.binding['device'],view.binding['extent'],view.check) as claim:
            with commit_timer(120):
                length = view.binding['extent']['offset_bytes']
                before = range_digest(claim.card_fd,0,length)
                with mounted_boot(view.binding['device']+'p1',attempt,read_only=True) as point:
                    result = inspect_files(hold_plan,point)
                after = range_digest(claim.card_fd,0,length)
                if before!=after: raise ValueError('Boot prefix changed during read-only restore inspection')
            view.check()
    return dict(result,plan_sha256=pin,hold_plan_sha256=hold_pin,attempt=attempt,boot_id=observed_boot_id,
                prefix=dict(offset=0,bytes=length,sha256=after),read_only=True,boot_unmounted=True,
                root_written=False,normal_boot_release_authorized=False)
