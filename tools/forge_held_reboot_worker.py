"""One bounded reboot from verified persistent recovery selectors; no SD writes."""
import os
import subprocess

from forge_recovery_bootcommit import validate, ensure_lock_directory, mounted_boot, commit_timer
from forge_recovery_claim import claim_root
from forge_recovery_commit_inspect import layout, inspect_files
from forge_recovery_commit_liveness import budget
from forge_target_ssh import target_lock


def run(request):
    reboot(request, 'install-hold')


def reboot(request, operation):
    if operation not in ('install-hold', 'release-hold'):
        raise ValueError('Unsupported owner reboot transition')
    plan = validate(request['plan'], request['pin'])
    owner, accepted = request['owner'], request['lease']
    if (os.geteuid() != 0 or plan['operation'] != operation or
            plan['binding']['mode'] != 'physical' or plan['binding'].get('lease_owner') != owner):
        raise ValueError('Reboot requires the exact physical selector-transition owner')
    layout(plan)
    ensure_lock_directory()
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        with claim_root(plan['binding']['device'], plan['binding']['extent'], lambda: layout(plan)):
            with commit_timer(120):
                if budget(plan['binding'], owner, accepted) < 180:
                    raise ValueError('Insufficient held reboot lease budget')
                with mounted_boot(plan['binding']['device']+'p1', request['query'], read_only=True) as point:
                    if inspect_files(plan, point)['status'] != 'after':
                        raise ValueError('Approved boot files or recovery image differ')
                # The read-only boot mount must be gone before the fixed reboot.
                # Keep both writer-exclusion locks until the reboot is dispatched.
                layout(plan)
                if budget(plan['binding'], owner, accepted) < 30:
                    raise ValueError('Held reboot lease expired during inspection')
                subprocess.run(['/sbin/reboot', '-f'], check=True, timeout=15,
                               stdin=subprocess.DEVNULL)
