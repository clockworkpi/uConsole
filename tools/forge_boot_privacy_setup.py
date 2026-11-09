"""Owner-prepared fstab privacy change; no remount, reboot or image publication."""
import base64
import copy
import fcntl
import os
from pathlib import Path
import subprocess

from forge_boot_observation import capture as capture_boot, normal_boot
from forge_boot_privacy import observe_mount, verify_mount, private_fstab, prepare_policy, verify_candidate
from forge_recovery_bootplan import digest
from forge_target_backup import capture as capture_files, verify
from forge_target_files import equivalent
from forge_target_journal import private_directory, read_record, write_record, events, validate_plan
from forge_target_ssh import dispatch


def fstab_source(host):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=10', host,
        '/usr/bin/findmnt --fstab --evaluate --noheadings --output SOURCE --mountpoint /boot/firmware'],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=True).stdout.strip()


def prepare(output, host):
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    fd = private_directory(output)
    try:
        write_record(fd, 'request.json', dict(host=host, target_write_authorized=False))
        before = capture_boot(host)
        normal_boot(before, before['machine_id'])
        capture_files(host, ['/etc/fstab'], output/'before.json')
        backup = read_record(fd, 'before.json')
        verify(backup, ['/etc/fstab'])
        if backup['machine_id'] != before['machine_id'] or backup['files'][0]['kind'] != 'file':
            raise ValueError('Fstab target identity differs')
        text = base64.b64decode(backup['files'][0]['data'], validate=True).decode()
        entries = [line.split('#', 1)[0].split() for line in text.splitlines()]
        entries = [entry for entry in entries if len(entry) >= 2 and entry[1] == '/boot/firmware']
        if len(entries) != 1:
            raise ValueError('Expected one boot mount entry')
        desired = private_fstab(text, entries[0][0])
        if desired == text:
            raise ValueError('Persistent policy is already private; inspect it instead of replacing it')
        mounted = observe_mount(host)
        verify_mount(mounted, '/dev/mmcblk0p1', False)
        if fstab_source(host) != '/dev/mmcblk0p1':
            raise ValueError('Persistent boot source differs from the mounted CM4 boot partition')
        write_record(fd, 'mount-before.json', mounted)
        # findmnt validates a temporary /run candidate, not the live fstab.
        verify_candidate(host, desired.encode())
        plan = prepare_policy(output/'transaction', host, backup, entries[0][0])
        capture_files(host, ['/etc/fstab'], output/'rechecked.json')
        after = read_record(fd, 'rechecked.json')
        verify(after, ['/etc/fstab'])
        if after['machine_id'] != backup['machine_id'] or not equivalent(after['files'][0], backup['files'][0]):
            raise ValueError('Fstab changed during preparation')
        if capture_boot(host) != before or observe_mount(host) != mounted or fstab_source(host) != '/dev/mmcblk0p1':
            raise ValueError('Boot or mount changed during preparation')
        write_record(fd, 'owner-review.json', dict(**plan, boot=before, boot_source='/dev/mmcblk0p1'))
        result = dict(status='prepared-not-applied', plan_sha256=plan['plan_sha256'],
            machine_id=before['machine_id'], boot_id=before['boot_id'],
            target_written=False, reboot_performed=False, private_mount_qualified=False)
        write_record(fd, 'acceptance.json', result)
        return result
    except BaseException as exc:
        write_record(fd, 'failure.json', dict(error_type=type(exc).__name__, target_written=False,
            reboot_performed=False, preserve_journals=True))
        raise
    finally:
        os.close(fd)


def inputs(directory, pin, *, applied=False):
    directory = Path(directory).absolute()
    fd = private_directory(directory)
    try:
        if os.path.lexists(directory/'failure.json') or (not applied and os.path.lexists(directory/'apply-attempt')):
            raise ValueError('Failed or attempted privacy setup requires inspection; never replay')
        accepted, review = read_record(fd, 'acceptance.json'), read_record(fd, 'owner-review.json')
        request = read_record(fd, 'request.json')
        child = private_directory(directory/'transaction')
        try:
            plan = validate_plan(read_record(child, 'plan.json'))
            history = events(child)
            if history and not applied:
                raise ValueError('Existing privacy transaction history requires inspection')
        finally:
            os.close(child)
        if (digest(accepted) != pin or accepted.get('status') != 'prepared-not-applied'
                or any(accepted.get(key) is not False for key in ('target_written', 'reboot_performed', 'private_mount_qualified'))
                or review.get('journal') != str(directory/'transaction')
                or review.get('plan_sha256') != digest(plan) or accepted.get('plan_sha256') != digest(plan)
                or plan['host'] != request.get('host') or request.get('target_write_authorized') is not False
                or review.get('boot_source') != '/dev/mmcblk0p1'
                or accepted.get('machine_id') != plan['before']['machine_id']):
            raise ValueError('Privacy review differs from its pinned plan')
        boot = normal_boot(review['boot'], accepted['machine_id'])
        if accepted.get('boot_id') != boot['boot_id']:
            raise ValueError('Privacy boot binding differs')
        for state in ('before', 'after'):
            verify(plan[state], ['/etc/fstab'])
        old, new = plan['before']['files'][0], plan['after']['files'][0]
        text = base64.b64decode(old['data'], validate=True).decode()
        entries = [line.split('#', 1)[0].split() for line in text.splitlines()]
        selected = [entry for entry in entries if len(entry) >= 2 and entry[1] == '/boot/firmware']
        if len(selected) != 1 or base64.b64decode(new['data'], validate=True).decode() != private_fstab(text, selected[0][0]):
            raise ValueError('Only the compiled private boot mount policy may be applied')
        if any(old[key] != new[key] for key in ('path', 'kind', 'mode', 'uid', 'gid', 'xattrs', 'atime_ns')):
            raise ValueError('Privacy setup cannot change fstab metadata')
        if applied:
            attempt = private_directory(directory/'apply-attempt')
            try:
                if os.path.lexists(directory/'apply-attempt/failure.json'):
                    raise ValueError('Uncertain privacy application requires reconciliation')
                result = read_record(attempt, 'acceptance.json')
                ack = read_record(attempt, 'acknowledgement.json')
                actual = read_record(attempt, 'after.json')
            finally:
                os.close(attempt)
            if (result.get('status') != 'applied-awaiting-private-mount' or result.get('plan_sha256') != digest(plan)
                    or result.get('target_written') is not True or result.get('reboot_performed') is not False
                    or len(history) != 2 or history[0].get('state') != 'dispatch'
                    or history[1].get('state') != 'acknowledged' or history[1].get('result') != ack
                    or any(event.get('direction') != 'apply' or event.get('nonce') != ack.get('nonce') for event in history)
                    or ack.get('machine_id') != boot['machine_id'] or ack.get('direction') != 'apply'
                    or [item.get('path') for item in ack.get('files', [])] != ['/etc/fstab']
                    or any(item.get('status') not in ('applied', 'already-applied') for item in ack['files'])):
                raise ValueError('Require acknowledged exact privacy application history')
            verify(actual, ['/etc/fstab'])
            if actual['machine_id'] != boot['machine_id'] or not equivalent(actual['files'][0], new):
                raise ValueError('Recorded applied fstab differs')
        return dict(directory=str(directory), acceptance_sha256=pin, plan=plan, boot=boot)
    finally:
        os.close(fd)


def apply(frozen):
    frozen = copy.deepcopy(frozen)
    directory = Path(frozen['directory'])
    fd, out = private_directory(directory), None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if inputs(directory, frozen['acceptance_sha256']) != frozen:
            raise ValueError('Privacy setup changed after confirmation')
        os.mkdir('apply-attempt', 0o700, dir_fd=fd)
        os.fsync(fd)
        out = private_directory(directory/'apply-attempt')
        plan, started = frozen['plan'], False
        try:
            write_record(out, 'request.json', dict(plan_sha256=digest(plan), reboot_authorized=False))
            if capture_boot(plan['host']) != frozen['boot']:
                raise ValueError('Normal boot changed before privacy application')
            verify_mount(observe_mount(plan['host']), '/dev/mmcblk0p1', False)
            if fstab_source(plan['host']) != '/dev/mmcblk0p1':
                raise ValueError('Persistent boot source changed before privacy application')
            started = True
            acknowledgement = dispatch(directory/'transaction', 'apply', approved_plan_sha256=digest(plan))
            write_record(out, 'acknowledgement.json', acknowledgement)
            capture_files(plan['host'], ['/etc/fstab'], directory/'apply-attempt/after.json')
            after = read_record(out, 'after.json')
            verify(after, ['/etc/fstab'])
            if (after['machine_id'] != plan['after']['machine_id']
                    or not equivalent(after['files'][0], plan['after']['files'][0])
                    or capture_boot(plan['host']) != frozen['boot']):
                raise ValueError('Applied privacy file or boot differs; preserve acknowledgement')
            result = dict(status='applied-awaiting-private-mount', plan_sha256=digest(plan),
                target_written=True, reboot_performed=False, private_mount_qualified=False,
                image_publication_authorized=False, automatic_retry_performed=False)
            write_record(out, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(out, 'failure.json', dict(error_type=type(exc).__name__,
                target_write_may_have_started=started, reboot_performed=False, preserve_journals=True))
            raise
    finally:
        if out is not None:
            os.close(out)
        os.close(fd)
