"""Internal guarded CONFIG commit engine; no CLI or physical dispatch route.

The owner must durably journal the plan and satisfy its external qualification
gates. approve() is a host handshake, not a backup-lease renewal. live_budget()
must independently verify the bound live lease and watchdog and return remaining
seconds. Neither callback is implemented by accepting client-supplied flags.
Any exception after approval is uncertain completion: preserve the journal and
reconcile; never infer rollback or automatically reverse boot selection.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess

from forge_recovery_claim import claim_root
from forge_recovery_image import verified as verify_image
from forge_target_backup import verify
from forge_target_files import apply_file, equivalent, snapshot, TargetConflict
from forge_target_ssh import target_lock
from forge_trial_firmware import paths
from forge_tryboot_recipe import CONFIG


def plan_digest(plan):
    return hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2)+'\n').encode()).hexdigest()


def validate(plan, pin):
    if (not isinstance(pin, str) or not re.fullmatch('[0-9a-f]{64}', pin) or
            not isinstance(plan, dict) or plan_digest(plan) != pin):
        raise ValueError('Boot commit plan digest differs from owner approval')
    fields = {'schema','kind','operation','hold_review_sha256','checksum_plan_sha256',
              'checksum_observation_sha256','binding','before','after','guarded_paths',
              'changed_paths','root_guard','prefix_guard','suffix_guard','image_dependency',
              'deployment_authorized','root_write_authorized','normal_boot_release_authorized',
              'required_gates','stage_token'}
    if (set(plan) != fields or type(plan['schema']) is not int or plan['schema'] != 1 or
            plan['kind'] != 'guarded-recovery-boot-file-plan' or
            plan['operation'] not in ('install-hold','release-hold') or
            any(plan[field] is not False for field in ('deployment_authorized',
                'root_write_authorized','normal_boot_release_authorized')) or
            not isinstance(plan['stage_token'], str) or
            not re.fullmatch('[0-9a-f]{32}', plan['stage_token'])):
        raise ValueError('Unsupported boot commit plan')
    selected = paths(plan['binding']['nonce'])
    for field in ('hold_review_sha256','checksum_plan_sha256','checksum_observation_sha256'):
        if not isinstance(plan[field], str) or not re.fullmatch('[0-9a-f]{64}', plan[field]):
            raise ValueError('Missing pinned source evidence')
    gates = plan['required_gates']
    required = {'explicit-owner-plan-approval','exclusive-read-only-root-claim',
                'fresh-root-and-protected-range-hashes','verified-boot-file-preimages',
                'bounded-commit-with-live-watchdog','verified-unmount-before-acknowledgement'}
    if (not isinstance(gates, list) or any(not isinstance(gate, str) for gate in gates) or
            len(set(gates)) != len(gates) or not required.issubset(gates)):
        raise ValueError('Required boot commit gates are missing')
    if plan['guarded_paths'] != selected or plan['changed_paths'] != [CONFIG]:
        raise ValueError('Only the complete single-selector transition is supported')
    for state in ('before','after'):
        verify(plan[state], selected)
        for record in plan[state]['files']:
            if record['kind'] == 'file' and (record['uid'] != 0 or record['gid'] != 0 or
                                           record['mode'] != 0o700 or record['xattrs']):
                raise ValueError('Boot file preimages require private FAT metadata')
    if plan['before']['machine_id'] != plan['after']['machine_id']:
        raise ValueError('Boot file target identities differ')
    before, after = ({record['path']: record for record in plan[state]['files']}
                     for state in ('before','after'))
    if [path for path in selected if not equivalent(before[path], after[path])] != [CONFIG]:
        raise ValueError('Boot commit may change only config.txt')
    if (before[CONFIG]['kind'] != 'file' or after[CONFIG]['kind'] != 'file' or
            {k:v for k,v in before[CONFIG].items() if k not in ('data','size','sha256')} !=
            {k:v for k,v in after[CONFIG].items() if k not in ('data','size','sha256')}):
        raise ValueError('Boot commit may change selector content only')
    dependency = plan['image_dependency']
    if (not isinstance(dependency, dict) or set(dependency) != {'path','size','sha256','plan_sha256'} or
            not isinstance(dependency['path'], str) or not re.fullmatch(
                '/boot/firmware/forge-recovery-[0-9a-f]{32}\\.img', dependency['path']) or
            type(dependency['size']) is not int or not 0 < dependency['size'] <= 128*1024*1024 or
            any(not isinstance(dependency[key], str) or not re.fullmatch('[0-9a-f]{64}', dependency[key])
                for key in ('sha256','plan_sha256'))):
        raise ValueError('Invalid pinned recovery image dependency')
    return plan


def check_mount(point, device, *, read_only=False):
    if type(read_only) is not bool: raise ValueError('Boot mount access must be explicit')
    point = Path(point)
    rows = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, separator, right = line.partition(' - ')
        fields, fs = left.split(), right.split()
        if separator and len(fields) >= 6 and fields[4] == str(point):
            rows.append((fields, fs))
    if len(rows) != 1:
        raise ValueError('Expected exactly one boot filesystem mount')
    fields, fs = rows[0]
    expected = Path('/sys/class/block', device.rsplit('/', 1)[1], 'dev').read_text().strip()
    if (fields[2] != expected or len(fs) != 3 or fs[0] != 'vfat' or
            not {'ro' if read_only else 'rw','nosuid','nodev','noexec'}.issubset(fields[5].split(','))):
        raise ValueError('Boot mount device, filesystem or safety options differ')
    info = point.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Boot mount must be private and root-owned')


@contextmanager
def mounted_boot(device, token, *, read_only=False):
    if (type(read_only) is not bool or not re.fullmatch('/dev/mmcblk[0-9]{1,2}p1', device) or
            not re.fullmatch('[0-9a-f]{32}', token)):
        raise ValueError('Invalid boot mount identity')
    point = Path('/run/forge-boot-commit-'+token)
    point.mkdir(mode=0o700)  # Exclusive: preserve any prior uncertain mount.
    try:
        options = ('ro' if read_only else 'rw')+',nodev,nosuid,noexec,uid=0,gid=0,fmask=0077,dmask=0077,iocharset=ascii,codepage=437'
        subprocess.run(['mount','-t','vfat','-o',options,device,str(point)], check=True, timeout=10)
        check_mount(point, device, read_only=read_only)
        yield point
    finally:
        if os.path.ismount(point):
            subprocess.run(['umount',str(point)], check=True, timeout=10)
        if os.path.ismount(point):
            raise RuntimeError('Boot filesystem remains mounted; no acknowledgement allowed')
        point.rmdir()


@contextmanager
def commit_timer(seconds=60):
    if type(seconds) is not int or not 1 <= seconds <= 120:
        raise ValueError('Invalid bounded boot filesystem phase')
    # Dedicated worker process only; never replace an existing owner's timer.
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError('Commit worker already has an alarm')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise TimeoutError(f'Boot filesystem phase exceeded its {seconds}-second window')
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def files_commit(plan, point):
    """Internal mounted phase. Caller holds the root claim and transaction lock."""
    fd = os.open(point, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        def check_files(state):
            for record in plan[state]['files']:
                current = snapshot(fd, record['path'].rsplit('/',1)[1], record['path'])
                if not equivalent(current, record):
                    raise TargetConflict('Boot dependency differs: '+record['path'])
        check_files('before')
        image = plan['image_dependency']
        image_fd = os.open(image['path'].rsplit('/',1)[1],
                           os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=fd)
        try:
            verify_image(image_fd, image['sha256'], image['size'])
        finally:
            os.close(image_fd)
        old, new = (dict(next(record for record in plan[state]['files'] if record['path'] == CONFIG),
                         path=str(Path(point)/'config.txt')) for state in ('before','after'))
        result = apply_file(old, new, stage_token=plan['stage_token'])
        check_files('after')
        os.fsync(fd)
        return dict(status=result['status'], path=CONFIG)
    finally:
        os.close(fd)


def ensure_lock_directory():
    # RAM identity must be checked before this function creates anything.
    path = Path('/run/lock')
    path.mkdir(mode=0o755, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe recovery transaction lock directory')
    finally:
        os.close(fd)


def execute(plan, pin, *, check, approve, live_budget, progress=None, unmounted=None):
    """Execute in a dedicated RAM worker with trusted identity/handshake hooks.

    check() returns a freshly verified RAM-bound layout, never a cached extent.
    approve(pin, binding) must journal explicit owner commit approval and renew
    liveness while boot is still unmounted. A successful return here is a file
    commit receipt, not proof of reboot, restored system health, or root writes.
    """
    plan = validate(copy.deepcopy(plan), pin)
    binding = plan['binding']
    if check() != binding['extent']:
        raise ValueError('Recovery identity/layout changed before locking')
    ensure_lock_directory()
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        with claim_root(binding['device'], binding['extent'], check) as claim:
            claim.verify_guards({name:plan[name+'_guard'] for name in ('root','prefix','suffix')},
                                progress=progress)
            if approve(pin, copy.deepcopy(binding)) != {'commit': pin, 'boot_id': binding['boot_id']}:
                raise ValueError('Explicit owner commit acknowledgement differs')
            remaining = live_budget()
            if type(remaining) not in (int,float) or not math.isfinite(remaining) or not 90 <= remaining <= 300:
                raise ValueError('Insufficient verified lease/watchdog commit budget')
            if check() != binding['extent']:
                raise ValueError('Recovery identity/layout changed before commit')
            with commit_timer():
                with mounted_boot(binding['device']+'p1', plan['stage_token']) as point:
                    result = files_commit(plan, point)
            # Renewal is permissible again only after verified unmount.
            if check() != binding['extent']:
                raise ValueError('Recovery identity/layout changed after commit')
            if unmounted is not None:
                unmounted()
            claim.verify_guards({name:plan[name+'_guard'] for name in ('root','suffix')}, progress=progress)
    return dict(status='boot-file-commit-verified', plan_sha256=pin,
                boot_id=binding['boot_id'], operation=plan['operation'], file=result,
                root_written=False, boot_unmounted=True, reboot_performed=False,
                physical_boot_qualified=False)
