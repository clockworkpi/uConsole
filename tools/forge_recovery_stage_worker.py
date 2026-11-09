"""Boot-bound normal-host staging: one boot file, never a reboot or root image."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from forge_boot_observation import normal_boot, read_boot
from forge_boot_privacy import MOUNT_CHECK, verify_mount
from forge_recovery_image import inspect as inspect_image
from forge_recovery_journal import validate as validate_image
from forge_recovery_ledger import fence, run
from forge_recovery_ssh import target_policy
from forge_target_files import apply_file, equivalent, parent_fd, snapshot
from forge_target_journal import validate_plan
from forge_target_ssh import target_lock


def digest(value):
    return hashlib.sha256((json.dumps(value, sort_keys=True, indent=2)+'\n').encode()).hexdigest()


def validate(value):
    if (not isinstance(value, dict) or set(value) != {
            'schema', 'preparation_sha256', 'nonce', 'phase', 'direction', 'attempt',
            'original_boot', 'boot', 'plan', 'plan_sha256', 'image_plan'} or
            type(value['schema']) is not int or value['schema'] != 1):
        raise ValueError('Invalid staging request')
    for field, size in (('preparation_sha256', 64), ('nonce', 32), ('attempt', 32), ('plan_sha256', 64)):
        if not isinstance(value[field], str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value[field]):
            raise ValueError('Invalid staging request pin or nonce')
    plan, image = validate_plan(value['plan']), validate_image(value['image_plan'])
    if (value['direction'] not in ('apply', 'restore') or digest(plan) != value['plan_sha256'] or
            image['schema'] != 2 or image['boot_source'] != '/dev/mmcblk0p1' or
            plan['host'] != image['host'] or plan['before']['machine_id'] != image['machine_id']):
        raise ValueError('Staging request target or plan differs')
    prefix = '/boot/firmware/'
    selected = [prefix+name for name in ('config.txt', 'cmdline.txt', 'tryboot.txt',
                'forge-trial-cmdline.txt', 'autoboot.txt', 'start4.elf', 'fixup4.dat',
                'forge-start-'+value['nonce']+'.elf', 'forge-fixup-'+value['nonce']+'.dat')]
    targets = dict(zip(('firmware-start', 'firmware-fixup', 'command', 'selector'),
                       (selected[-2], selected[-1], selected[3], selected[2])))
    target = targets.get(value['phase'])
    if target is None or [item['path'] for item in plan['before']['files']] != [
            path for path in selected if path != target] + [target]:
        raise ValueError('Staging request must guard exactly the nine fixed boot paths')
    if any(not equivalent(old, new) for old, new in zip(plan['before']['files'][:-1], plan['after']['files'][:-1])):
        raise ValueError('Staging phase may change only its final selected boot file')
    if image['destination'] in selected:
        raise ValueError('Recovery image collides with boot files')
    for state in ('before', 'after'):
        if any(item['kind'] == 'file' and (item['mode'] != 0o700 or item['uid'] != 0 or
                item['gid'] != 0 or item['xattrs']) for item in plan[state]['files']):
            raise ValueError('Staging requires private root-owned boot files')
    original = normal_boot(value['original_boot'], image['machine_id'])
    boot = normal_boot(value['boot'], image['machine_id'])
    if roots(original) != roots(boot) or (value['direction'] == 'apply' and boot != original):
        raise ValueError('Staging apply requires the prepared boot; restore requires the native root')
    return value


def roots(boot):
    return [token for token in boot['cmdline'].split() if token.startswith('root=')]


def provision(boot_id, preparation_pin):
    """Private per-boot ledger in /run; never write an on-card ledger."""
    if (os.geteuid() != 0 or not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id) or
            not re.fullmatch('[0-9a-f]{64}', preparation_pin)):
        raise ValueError('Staging ledger requires root and pinned boot/preparation identities')
    fd = os.open('/run', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in ('forge-boot-staging', boot_id, preparation_pin):
            try:
                os.mkdir(name, mode=0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            info = os.fstat(child)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                os.close(child)
                raise PermissionError('Unsafe staging ledger directory')
            os.close(fd)
            fd = child
        os.fsync(fd)
    finally:
        os.close(fd)
    return Path('/run/forge-boot-staging')/boot_id/preparation_pin


def inspect_files(plan):
    files, scratch = [], []
    for index, (old, new) in enumerate(zip(plan['before']['files'], plan['after']['files'])):
        with parent_fd(old['path']) as (fd, name):
            current = snapshot(fd, name, old['path'])
            before, after = equivalent(current, old), equivalent(current, new)
            state = 'unchanged' if before and after else 'before' if before else 'after' if after else 'conflict'
            files.append(dict(path=old['path'], state=state))
            for direction in ('apply', 'restore'):
                temporary = '.uconsole-forge-'+plan['stage_tokens'][direction][index]
                try: os.stat(temporary, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError: continue
                scratch.append(dict(path=old['path'], direction=direction, token=plan['stage_tokens'][direction][index]))
    return dict(files=files, scratch=scratch)


def mount_guard(image):
    # Restore must not depend on recovery-image availability, but must still
    # address the actual private boot filesystem, never an unmounted directory.
    result = subprocess.run(['python3', '-c', MOUNT_CHECK], text=True, capture_output=True,
                            timeout=20, check=True)
    verify_mount(json.loads(result.stdout), image['boot_source'], True)


def runtime_guard():
    """Attempt bookkeeping and the shared lock must reside on RAM filesystems."""
    for path in ('/run', '/run/lock'):
        result = subprocess.run(['findmnt', '--noheadings', '--output', 'FSTYPE', '--target', path],
                                text=True, capture_output=True, timeout=10, check=True)
        if result.stdout.strip() != 'tmpfs':
            raise ValueError('Staging bookkeeping requires tmpfs at '+path)


def perform(value, operation, observed_boot=None):
    validate(value)
    if operation not in ('execute', 'reconcile') or os.geteuid() != 0:
        raise ValueError('Choose a root staging execution or reconciliation')
    expected = value['boot'] if operation == 'execute' else observed_boot
    normal_boot(expected, value['image_plan']['machine_id'])
    if roots(expected) != roots(value['original_boot']):
        raise ValueError('Observed normal boot no longer uses the native root')
    if read_boot() != expected:
        raise ValueError('Staging worker belongs to another boot')
    runtime_guard()
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        if read_boot() != expected:
            raise ValueError('Staging worker belongs to another boot')
        mount_guard(value['image_plan'])
        request = dict(plan_sha256=digest(value), direction=value['direction'], nonce=value['attempt'])
        same_boot = expected['boot_id'] == value['boot']['boot_id']
        if operation == 'reconcile':
            outcome = fence(provision(expected['boot_id'], value['preparation_sha256']),
                            value['attempt'], request) if same_boot else {'status': 'previous-boot-fenced'}
            inspection = inspect_files(value['plan'])
            result = dict(status='inspected', outcome=outcome, **inspection,
                          requires_new_boot=outcome['status'] == 'incomplete')
        else:
            def publication_guard():
                if value['direction'] == 'apply':
                    image = value['image_plan']
                    target_policy(image)
                    observed = inspect_image(image['destination'], image['sha256'], image['size'], image['stage_token'])
                    if (observed['destination']['state'] != 'matching-bytes' or observed['scratch']['state'] != 'absent'):
                        raise ValueError('Published recovery image changed before staging')
            publication_guard()
            inspected = inspect_files(value['plan'])
            if any(item['state'] == 'conflict' for item in inspected['files']):
                raise ValueError('Staging preimage conflict; no boot file written')
            def effect():
                before, after = value['plan']['before']['files'][-1], value['plan']['after']['files'][-1]
                if value['direction'] == 'restore': before, after = after, before
                result = apply_file(before, after, stage_token=value['plan']['stage_tokens'][value['direction']][-1])
                publication_guard()
                mount_guard(value['image_plan'])
                inspected = inspect_files(value['plan'])
                desired = 'after' if value['direction'] == 'apply' else 'before'
                if any(item['state'] not in ('unchanged', desired) for item in inspected['files']) or read_boot() != expected:
                    raise ValueError('Staging post-write guard changed; reconciliation required')
                return dict(status='acknowledged', file=result, **inspected)
            result = run(provision(expected['boot_id'], value['preparation_sha256']), value['attempt'], request, effect)
        if read_boot() != expected:
            raise ValueError('Boot changed during staging observation')
        return dict(result, request_sha256=digest(value), attempt=value['attempt'],
                    boot_id=expected['boot_id'], machine_id=expected['machine_id'],
                    root_written=False, reboot_performed=False, recovery_qualified=False)
