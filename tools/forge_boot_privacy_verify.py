"""Owner-confirmed normal reboot and fresh private-mount verification."""
import copy
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import uuid

from forge_boot_observation import capture, normal_boot, verify as verify_boot
from forge_boot_privacy import observe_mount, verify_mount
from forge_boot_privacy_setup import inputs as setup_inputs, fstab_source
from forge_recovery_bootplan import digest
from forge_target_backup import capture as capture_files, verify as verify_files
from forge_target_files import equivalent
from forge_target_journal import private_directory, read_record, write_record


REBOOT = '''import hashlib,json,os,stat,subprocess,sys,types
r=json.load(sys.stdin)
for name,source in r['modules']:
    m=types.ModuleType(name);sys.modules[name]=m
    exec(compile(source,'<owner-privacy-reboot:'+name+'>','exec'),m.__dict__)
from forge_boot_observation import read_boot,normal_boot
from forge_target_ssh import target_lock
with target_lock('/run/lock/uconsole-forge-target.lock'):
    if os.geteuid()!=0 or normal_boot(read_boot(),r['boot']['machine_id'])!=r['boot']:
        raise ValueError('Normal boot changed before reboot')
    fd=os.open('/etc/fstab',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno()); data=stream.read(1048577)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                or info.st_mode & 0o022 or len(data)>1048576
                or hashlib.sha256(data).hexdigest()!=r['fstab_sha256']):
            raise ValueError('Applied fstab changed before reboot')
    subprocess.run(['/usr/bin/systemctl','reboot'],check=True,timeout=15)
'''


def inputs(directory, pin):
    return setup_inputs(directory, pin, applied=True)


def check_files(output, fd, frozen, label):
    plan = frozen['plan']
    capture_files(plan['host'], ['/etc/fstab'], output/(label+'.json'))
    observed = read_record(fd, label+'.json')
    verify_files(observed, ['/etc/fstab'])
    if (observed['machine_id'] != frozen['boot']['machine_id']
            or not equivalent(observed['files'][0], plan['after']['files'][0])
            or fstab_source(plan['host']) != '/dev/mmcblk0p1'):
        raise ValueError('Applied fstab or persistent boot source changed')


def reboot_transport(frozen):
    source = Path(__file__).resolve().parent
    names = ('forge_boot_observation', 'forge_target_backup', 'forge_target_files',
             'forge_target_journal', 'forge_target_ssh')
    payload = dict(boot=frozen['boot'], fstab_sha256=frozen['plan']['after']['files'][0]['sha256'],
        modules=[(name, (source/(name+'.py')).read_text()) for name in names])
    try:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', frozen['plan']['host'],
            'sudo -n /usr/bin/python3 -I -S -c '+shlex.quote(REBOOT)],
            input=json.dumps(payload), text=True, capture_output=True, timeout=30)
        return dict(returncode=result.returncode, reboot_observed=False)
    except subprocess.TimeoutExpired:
        return dict(transport_timeout=True, reboot_observed=False, do_not_repeat=True)


def verify(frozen, *, reboot=False, timeout=600):
    """At most one reboot request. Observation retries never dispatch again.

    Read-only verification can be used after a manual reboot or an uncertain
    prior request. It requires a different normal boot and effective privacy.
    """
    if type(reboot) is not bool or type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('Invalid reboot or observation bound')
    frozen = copy.deepcopy(frozen)
    directory = Path(frozen['directory'])
    fd, out = private_directory(directory), None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if inputs(directory, frozen['acceptance_sha256']) != frozen:
            raise ValueError('Applied privacy evidence changed after confirmation')
        name = 'reboot-attempt' if reboot else 'mount-verification-'+uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=fd)
        os.fsync(fd)
        output = directory/name
        out = private_directory(output)
        dispatched = False
        try:
            write_record(out, 'request.json', dict(plan_sha256=digest(frozen['plan']),
                previous_boot_id=frozen['boot']['boot_id'], reboot_authorized=reboot))
            check_files(output, out, frozen, 'fstab-before')
            if reboot:
                if capture(frozen['plan']['host']) != frozen['boot']:
                    raise ValueError('Boot changed; verify the existing new boot without another reboot')
                write_record(out, 'reboot-intent.json', dict(boot_id=frozen['boot']['boot_id'],
                    automatic_retry_authorized=False))
                dispatched = True
                write_record(out, 'reboot-transport.json', reboot_transport(frozen))
            deadline, attempt = time.monotonic()+timeout, 0
            while True:
                attempt += 1
                try:
                    observed = normal_boot(capture(frozen['plan']['host']), frozen['boot']['machine_id'])
                    verify_boot(observed, frozen['boot'], frozen['boot']['machine_id'])
                    mount = observe_mount(frozen['plan']['host'])
                    checked = verify_mount(mount, '/dev/mmcblk0p1', True)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                    write_record(out, f'observation-{attempt:04d}.json', dict(error_type=type(exc).__name__))
                    if not reboot or time.monotonic() >= deadline:
                        raise RuntimeError('Fresh private normal boot not verified; no reboot retry authorized') from exc
                    time.sleep(min(2, max(0, deadline-time.monotonic())))
                    continue
                break
            check_files(output, out, frozen, 'fstab-after')
            if capture(frozen['plan']['host']) != observed:
                raise ValueError('Boot changed during private mount verification')
            write_record(out, 'mount.json', dict(observation=mount, verification=checked, boot=observed))
            result = dict(status='verified-private-normal-boot', boot_id=observed['boot_id'],
                plan_sha256=digest(frozen['plan']), private_mount_qualified=True,
                reboot_request_dispatched=dispatched, image_publication_performed=False,
                recovery_fallback_qualified=False, root_write_authorized=False)
            write_record(out, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(out, 'failure.json', dict(error_type=type(exc).__name__, reboot_may_have_started=dispatched,
                automatic_retry_performed=False, preserve_journals=True))
            raise
    finally:
        if out is not None:
            os.close(out)
        os.close(fd)
