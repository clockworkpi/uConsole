"""Owner SSH transport for journaled private recovery-image operations."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from forge_boot_privacy import MOUNT_CHECK, private_fstab, verify_mount
from forge_recovery_image import publish, remove, inspect
from forge_recovery_journal import validate, acknowledgement
from forge_target_ssh import target_lock
from forge_recovery_ledger import provision, run as ledger_run, fence


def target_policy(plan):
    if os.geteuid() != 0:
        raise PermissionError('Recovery image worker must run as root')
    if Path('/etc/machine-id').read_text().strip() != plan['machine_id']:
        raise ValueError('Recovery target identity differs')
    fd = os.open('/etc/fstab', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        from forge_recovery_image import signature
        import stat
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != 0
                or before.st_mode & 0o022 or before.st_size > 1024 * 1024):
            raise ValueError('Unsafe target fstab')
        data = os.read(fd, 1024 * 1024 + 1)
        if signature(before) != signature(os.fstat(fd)) or len(data) != before.st_size:
            raise ValueError('Target fstab changed during inspection')
    finally:
        os.close(fd)
    if hashlib.sha256(data).hexdigest() != plan['fstab_sha256']:
        raise ValueError('Target fstab differs from approved privacy policy')
    text = data.decode('utf-8')
    entries = [line.split('#', 1)[0].split() for line in text.splitlines()]
    entries = [fields for fields in entries if len(fields) >= 2 and fields[1] == '/boot/firmware']
    if len(entries) != 1 or private_fstab(text, entries[0][0]) != text:
        raise ValueError('Persistent boot mount policy is not the compiled private policy')
    source = subprocess.run(['findmnt', '--fstab', '--evaluate', '--noheadings',
                             '--output', 'SOURCE', '--mountpoint', '/boot/firmware'],
                            text=True, capture_output=True, timeout=10, check=True).stdout.strip()
    if source != plan['boot_source']:
        raise ValueError('Persistent boot source differs from approved device')
    observed = subprocess.run(['python3', '-c', MOUNT_CHECK], text=True, capture_output=True,
                              timeout=20, check=True)
    verify_mount(json.loads(observed.stdout), plan['boot_source'], True)


def perform(plan, direction, nonce, digest, *, guard=None):
    validate(plan)
    expected_digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
    if digest != expected_digest or not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Invalid recovery request binding')
    if direction not in ('apply', 'restore', 'inspect', 'fence-apply', 'fence-restore'):
        raise ValueError('Invalid recovery operation')
    if guard is not None and not callable(guard):
        raise ValueError('Installed owner guard must be callable')
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        if guard is not None: guard()
        if direction.startswith('fence-'):
            if (plan['schema'] != 2 or os.geteuid() != 0 or
                    Path('/etc/machine-id').read_text().strip() != plan['machine_id']):
                raise PermissionError('Fencing requires schema 2 and the matching root target')
            request = dict(plan_sha256=digest, direction=direction[len('fence-'):], nonce=nonce)
            outcome = fence(provision(digest), nonce, request)
            return dict(kind='fence', nonce=nonce, plan_sha256=digest, machine_id=plan['machine_id'],
                        outcome=outcome)
        if direction == 'inspect':
            if os.geteuid() != 0 or Path('/etc/machine-id').read_text().strip() != plan['machine_id']:
                raise PermissionError('Inspection target identity/privilege differs')
            policy = {'valid': True}
            try:
                target_policy(plan)
            except (ValueError, PermissionError) as exc:
                policy = {'valid': False, 'reason': str(exc)}
            return dict(kind='inspection', nonce=nonce, plan_sha256=digest, machine_id=plan['machine_id'],
                        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), policy=policy,
                        files=inspect(plan['destination'], plan['sha256'], plan['size'], plan['stage_token']),
                        mutation_performed=False, retry_authorized=False)
        if plan['schema'] != 2:
            raise ValueError('Legacy recovery plans are inspect-only')
        target_policy(plan)
        def effect():
            args = (plan['destination'], plan['sha256'], plan['size'], plan['stage_token'])
            result = publish(plan['source'], *args) if direction == 'apply' else remove(*args)
            target_policy(plan)  # Policy drift after an effect remains uncertain.
            if guard is not None: guard()
            response = acknowledgement(plan, direction, nonce, digest)
            if any(result[name] != response[name] for name in ('status', 'sha256', 'size')):
                raise RuntimeError('Unexpected image primitive outcome')
            return response
        return ledger_run(provision(digest), nonce,
                          dict(plan_sha256=digest, direction=direction, nonce=nonce), effect)


BOOTSTRAP = '''import json, sys, types
request = json.load(sys.stdin)
for name, source in request['modules']:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, '<forge-owner:' + name + '>', 'exec'), module.__dict__)
from forge_recovery_ssh import perform
print(json.dumps(perform(request['plan'], request['direction'], request['nonce'], request['digest'])))
'''


def transport(plan, direction, nonce, digest):
    validate(plan)
    root = Path(__file__).resolve().parent
    names = ('forge_target_backup', 'forge_target_files', 'forge_target_journal',
             'forge_target_ssh', 'forge_boot_privacy', 'forge_recovery_image',
             'forge_recovery_journal', 'forge_recovery_ledger', 'forge_recovery_ssh')
    payload = dict(plan=plan, direction=direction, nonce=nonce, digest=digest,
                   modules=[(name, (root / (name + '.py')).read_text()) for name in names])
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', plan['host'],
                             'sudo -n python3 -c ' + shlex.quote(BOOTSTRAP)],
                            input=json.dumps(payload), text=True, capture_output=True,
                            timeout=180, check=True)
    return json.loads(result.stdout)
