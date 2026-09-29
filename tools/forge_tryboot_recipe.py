"""Compile a non-deploying native identity trial from verified boot preimages.

This is not a rescue image or permission to change boot files. The first trial
keeps the native kernel/root and adds only a boot-observation nonce. Actual
deployment needs a separate reviewed boot transaction and fallback gate.
"""
import base64
import copy
import hashlib
import os
from pathlib import Path
import re

from forge_target_backup import verify
from forge_target_journal import prepare, private_directory, write_record


CONFIG = '/boot/firmware/config.txt'
CMDLINE = '/boot/firmware/cmdline.txt'
TRYBOOT = '/boot/firmware/tryboot.txt'
TRIAL_CMDLINE = '/boot/firmware/forge-trial-cmdline.txt'
AUTOBOOT = '/boot/firmware/autoboot.txt'
PATHS = [CONFIG, CMDLINE, TRYBOOT, TRIAL_CMDLINE, AUTOBOOT]


def compile_recipe(backup, nonce):
    verify(backup, PATHS)
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Trial nonce must be 32 lowercase hexadecimal characters')
    files = {item['path']: item for item in backup['files']}
    if any(files[path]['kind'] != 'absent' for path in (TRYBOOT, TRIAL_CMDLINE, AUTOBOOT)):
        raise ValueError('Existing alternate boot configuration requires separate review')
    if any(files[path]['kind'] != 'file' for path in (CONFIG, CMDLINE)):
        raise ValueError('Native boot configuration and command line are required')
    config = base64.b64decode(files[CONFIG]['data']).decode('utf-8')
    cmdline = base64.b64decode(files[CMDLINE]['data']).decode('utf-8').strip()
    if '\0' in config or '\0' in cmdline or len(cmdline.splitlines()) != 1:
        raise ValueError('Invalid native boot text')
    # Includes and path indirection may hide dependencies or change the meaning
    # of the command line override. Do not guess how to flatten them.
    forbidden = {'include', 'os_prefix', 'cmdline', 'boot_ramdisk', 'tryboot_a_b'}
    for line in config.splitlines():
        setting = line.split('#', 1)[0].strip()
        if setting and re.split(r'[=\s]', setting, maxsplit=1)[0] in forbidden:
            raise ValueError('Indirect boot configuration requires separate review')
    tokens = cmdline.split()
    if (sum(t.startswith('root=') for t in tokens) != 1 or
            any(t.startswith(('uconsole.emulator=', 'uconsole.forge_trial=', 'uconsole.recovery=',
                              'uconsole.recovery_watchdog=', 'uconsole.recovery_lease=',
                              'uconsole.recovery_owner=', 'init=', 'rdinit='))
                for t in tokens)):
        raise ValueError('Expected native root command line without emulator or init overrides')
    trial = config.rstrip() + '\n\n[all]\ncmdline=forge-trial-cmdline.txt\n'
    command = cmdline + ' uconsole.forge_trial=' + nonce + '\n'
    outputs = []
    for path, text in ((TRYBOOT, trial), (TRIAL_CMDLINE, command)):
        data = text.encode('utf-8')
        outputs.append({'path': path, 'data': base64.b64encode(data).decode(),
                        'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
    return {'schema': 1, 'kind': 'native-identity-trial', 'nonce': nonce,
            'machine_id': backup['machine_id'], 'files': outputs,
            'native_preimages': {p: files[p]['sha256'] for p in (CONFIG, CMDLINE)},
            'restore': [{'path': p, 'kind': 'absent'} for p in (TRYBOOT, TRIAL_CMDLINE)],
            'deployment_performed': False, 'recovery_qualified': False}


def compile_recovery_recipe(backup, nonce, image_plan):
    """Compile only: a publication receipt and boot fallback are separate gates.

    Keep native kernel/overlays, but explicitly select the private RAM recovery
    image in tryboot.txt. Never edit the normal config or command line.
    """
    from forge_recovery_journal import validate
    validate(image_plan)
    if image_plan['schema'] != 2 or image_plan['machine_id'] != backup.get('machine_id'):
        raise ValueError('Recovery image plan must use schema 2 and match the backed-up target')
    recipe = compile_recipe(backup, nonce)
    config, command = [base64.b64decode(item['data']).decode() for item in recipe['files']]
    if any(t == 'noinitrd' or t.startswith('initrd=') for t in command.split()):
        raise ValueError('Native command line overrides initramfs loading')
    lines = []
    for line in config.splitlines():
        setting = line.split('#', 1)[0].strip()
        key = re.split(r'[=\s]', setting, maxsplit=1)[0]
        if key in ('ramfsfile', 'ramfsaddr'):
            raise ValueError('Legacy RAM filesystem configuration requires separate review')
        if key in ('initramfs', 'auto_initramfs'):
            continue
        lines.append(line)
    # The identity compiler ends in [all], so these overrides are unconditional.
    config = '\n'.join(lines) + '\nauto_initramfs=0\ninitramfs ' + Path(image_plan['destination']).name + ' followkernel\n'
    removed = ('root=', 'rootfstype=', 'rootflags=', 'resume=', 'resume_offset=',
               'net.ifnames=', 'panic=', 'fsck.')
    tokens = [t for t in command.split() if not t.startswith(removed)
              and t not in ('rootwait', 'ro', 'rw', 'quiet', 'splash')
              and not t.startswith('rootwait=')]
    command = ' '.join(tokens + ['root=/dev/ram0', 'rdinit=/init', 'uconsole.recovery=1',
                                 'net.ifnames=0', 'panic=10']) + '\n'
    for item, text in zip(recipe['files'], (config, command)):
        data = text.encode()
        item.update(data=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    import json
    recipe.update(kind='native-ram-recovery-trial', image_dependency=dict(
        path=image_plan['destination'], sha256=image_plan['sha256'], size=image_plan['size'],
        plan_sha256=hashlib.sha256((json.dumps(image_plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()),
        required_gates=['private-persistent-boot-policy', 'acknowledged-image-publication',
                        'reviewed-selector-staging', 'independent-boot-fallback'],
        deployment_authorized=False)
    return recipe


def prepare_staging(directory, host, backup, nonce):
    """Freeze two guarded transactions; caller must review before dispatch.

    Apply command then selector; restore selector then command. No reboot is
    performed or authorized by these plans. All five preimages are guarded.
    """
    recipe = compile_recipe(backup, nonce)
    return _prepare_staging(directory, host, backup, recipe)


def compile_watchdog_trial(backup, nonce, timeout=120):
    """Healthy native trial only; not a watchdog expiry or fallback proof."""
    if type(timeout) is not int or not 60 <= timeout <= 300:
        raise ValueError('Watchdog handoff trial timeout must be 60..300 seconds')
    recipe = compile_recipe(backup, nonce)
    config = base64.b64decode(recipe['files'][0]['data']).decode()
    command = base64.b64decode(recipe['files'][1]['data']).decode()
    for line in config.splitlines():
        setting = line.split('#', 1)[0].strip()
        if re.split(r'[=\s]', setting, maxsplit=1)[0] in ('kernel_watchdog_timeout', 'kernel_watchdog_partition'):
            raise ValueError('Existing firmware watchdog selection requires separate review')
    if any(t.startswith(('watchdog.', 'bcm2835_wdt.')) for t in command.split()):
        raise ValueError('Existing kernel watchdog arguments require separate review')
    # Current CM4 DTBs map this parameter to the early-watchdog property,
    # required by firmware since the July 2025 opt-in fix. Runtime watchdog
    # availability alone does not set that property.
    data = (config + 'dtparam=watchdog=on\nkernel_watchdog_timeout=' + str(timeout) + '\n').encode()
    recipe['files'][0].update(data=base64.b64encode(data).decode(), size=len(data),
                              sha256=hashlib.sha256(data).hexdigest())
    recipe.update(kind='native-watchdog-handoff-trial', watchdog_timeout=timeout,
                  required_gates=['native-runtime-watchdog-service', 'reviewed-selector-staging'],
                  failed_boot_fallback_qualified=False)
    return recipe


def prepare_watchdog_staging(directory, host, backup, nonce, timeout=120):
    return _prepare_staging(directory, host, backup, compile_watchdog_trial(backup, nonce, timeout))


def _prepare_staging(directory, host, backup, recipe, *, paths=PATHS,
                     steps=(('command', TRIAL_CMDLINE), ('selector', TRYBOOT))):
    verify(backup, paths)
    records = {item['path']: item for item in backup['files']}
    native = records[CONFIG]
    if native['mode'] not in (0o700, 0o755) or native['uid'] != 0 or native['gid'] != 0 or native['xattrs']:
        raise ValueError('Trial staging requires qualified FAT root/0700 or root/0755 metadata')
    outputs = {item['path']: item for item in recipe['files']}
    desired = {path: dict(native, **outputs[path]) for path in outputs}
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    phases = []
    for name, target in steps:
        order = [p for p in paths if p != target] + [target]
        before = {'schema': 1, 'machine_id': backup['machine_id'],
                  'files': [copy.deepcopy(records[p]) for p in order]}
        records[target] = desired[target]
        after = dict(before, files=[copy.deepcopy(records[p]) for p in order])
        phases.append(prepare(directory / name, host, before, after))
    review = dict(recipe, phases=phases, guarded_paths=list(paths), apply_order=[name for name, _ in steps],
                  restore_order=[name for name, _ in reversed(steps)])
    fd = private_directory(directory)
    try:
        write_record(fd, 'review.json', review)
    finally:
        os.close(fd)
    return review
