"""Prepare alternate matched CM4 firmware files; never deploy or flash EEPROM."""
import base64
import hashlib
import json
import re
from urllib.request import urlopen

from forge_target_backup import verify
from forge_tryboot_recipe import PATHS, CONFIG, TRYBOOT, TRIAL_CMDLINE, compile_watchdog_trial, _prepare_staging

NATIVE = ['/boot/firmware/start4.elf', '/boot/firmware/fixup4.dat']


def paths(nonce):
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Invalid firmware trial nonce')
    return PATHS + NATIVE + ['/boot/firmware/forge-start-' + nonce + '.elf',
                             '/boot/firmware/forge-fixup-' + nonce + '.dat']


def fetch_pair(revision):
    """Fetch both members from one immutable upstream revision for owner review."""
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('An exact upstream commit is required')
    files = []
    def get_json(url, limit):
        with urlopen(url, timeout=30) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Oversized firmware API response')
        return json.loads(raw)
    for name, limit in (('start4.elf', 8 * 1024 * 1024), ('fixup4.dat', 64 * 1024)):
        url = 'https://raw.githubusercontent.com/raspberrypi/firmware/' + revision + '/boot/' + name
        api = 'https://api.github.com/repos/raspberrypi/firmware/'
        entry = get_json(api + 'contents/boot/' + name + '?ref=' + revision, 128 * 1024)
        blob_sha = entry.get('sha')
        if (entry.get('type') != 'file' or entry.get('path') != 'boot/' + name
                or type(entry.get('size')) is not int or not 0 < entry['size'] <= limit
                or not isinstance(blob_sha, str) or not re.fullmatch('[0-9a-f]{40}', blob_sha)):
            raise ValueError('Invalid pinned firmware entry')
        blob = get_json(api + 'git/blobs/' + blob_sha, 2 * limit + 4096)
        if blob.get('encoding') != 'base64' or blob.get('sha') != blob_sha:
            raise ValueError('Unexpected firmware blob response')
        data = base64.b64decode(''.join(blob['content'].split()), validate=True)
        actual_blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if len(data) != entry['size'] or actual_blob != blob_sha:
            raise ValueError('Firmware content differs from the pinned Git blob')
        files.append(dict(name=name, url=url, data=base64.b64encode(data).decode(),
                          size=len(data), sha256=hashlib.sha256(data).hexdigest()))
    return dict(schema=1, revision=revision, files=files)


def compile_recipe(backup, nonce, bundle, timeout=120):
    selected = paths(nonce)
    verify(backup, selected)
    records = {f['path']: f for f in backup['files']}
    if any(records[p]['kind'] != 'file' for p in NATIVE):
        raise ValueError('Both normal firmware preimages must be backed up')
    if any(records[p]['kind'] != 'absent' for p in selected[-2:]):
        raise ValueError('Alternate firmware destinations must be originally absent')
    if (not isinstance(bundle, dict) or set(bundle) != {'schema','revision','files'}
            or type(bundle['schema']) is not int or bundle['schema'] != 1
            or not isinstance(bundle['revision'], str) or not re.fullmatch('[0-9a-f]{40}', bundle['revision'])
            or not isinstance(bundle['files'], list) or len(bundle['files']) != 2):
        raise ValueError('Invalid matched firmware bundle')
    recipe = compile_watchdog_trial(dict(backup, files=backup['files'][:len(PATHS)]), nonce, timeout)
    config = base64.b64decode(recipe['files'][0]['data']).decode()
    for line in config.splitlines():
        key = re.split(r'[=\s]', line.split('#', 1)[0].strip(), maxsplit=1)[0]
        if key in ('start_file','fixup_file','start_x','start_debug','gpu_mem','gpu_mem_256','gpu_mem_512','gpu_mem_1024'):
            raise ValueError('Custom firmware or memory-split selection requires separate review')
    firmware = []
    for member, name, path, limit in zip(bundle['files'], ('start4.elf','fixup4.dat'), selected[-2:],
                                        (8*1024*1024, 64*1024)):
        expected_url = 'https://raw.githubusercontent.com/raspberrypi/firmware/' + bundle['revision'] + '/boot/' + name
        if (not isinstance(member, dict) or set(member) != {'name','url','data','size','sha256'}
                or member['name'] != name or member['url'] != expected_url):
            raise ValueError('Firmware pair provenance differs')
        data = base64.b64decode(member['data'], validate=True)
        if (type(member['size']) is not int or not 0 < len(data) <= limit
                or len(data) != member['size'] or hashlib.sha256(data).hexdigest() != member['sha256']):
            raise ValueError('Firmware member failed content verification')
        firmware.append(dict(path=path, data=member['data'], size=len(data), sha256=member['sha256']))
    config += 'start_file=' + selected[-2].split('/')[-1] + '\nfixup_file=' + selected[-1].split('/')[-1] + '\n'
    encoded = config.encode()
    recipe['files'][0].update(data=base64.b64encode(encoded).decode(), size=len(encoded),
                              sha256=hashlib.sha256(encoded).hexdigest())
    recipe['files'].extend(firmware)
    recipe.update(kind='alternate-firmware-watchdog-trial', firmware_revision=bundle['revision'],
                  firmware_sources=[dict(name=f['name'], url=f['url'], sha256=f['sha256']) for f in bundle['files']],
                  eeprom_modified=False, deployment_authorized=False,
                  required_gates=recipe['required_gates'] + ['physical-power-cycle-recovery'])
    return recipe


def compile_recovery_recipe(backup, nonce, bundle, image_plan, timeout=120, *, lease_owner=None):
    """Compile only: retain private-image and independent-fallback gates.

    A healthy firmware handoff does not authorize this RAM boot. The caller
    must independently publish the exact image and satisfy every required gate.
    """
    from forge_tryboot_recipe import compile_recovery_recipe as recovery_recipe
    if lease_owner is not None and (not isinstance(lease_owner, str) or
                                    not re.fullmatch('[0-9a-f]{64}', lease_owner)):
        raise ValueError('Recovery lease owner must be a fresh 64-hex session token')
    firmware = compile_recipe(backup, nonce, bundle, timeout)
    recovery = recovery_recipe(dict(backup, files=backup['files'][:len(PATHS)]), nonce, image_plan)
    selected = paths(nonce)
    if image_plan['destination'] in selected:
        raise ValueError('Recovery image collides with guarded boot paths')
    config = base64.b64decode(recovery['files'][0]['data']).decode()
    config += ('dtparam=watchdog=on\nkernel_watchdog_timeout=' + str(timeout) + '\n'
               'start_file=' + selected[-2].split('/')[-1] + '\n'
               'fixup_file=' + selected[-1].split('/')[-1] + '\n')
    encoded = config.encode()
    recovery['files'][0].update(data=base64.b64encode(encoded).decode(), size=len(encoded),
                                 sha256=hashlib.sha256(encoded).hexdigest())
    command = base64.b64decode(recovery['files'][1]['data']).decode().strip()
    options = ' uconsole.recovery_watchdog=1'
    if lease_owner is not None:
        options += ' uconsole.recovery_lease=1 uconsole.recovery_owner=' + lease_owner
    encoded = (command + options + '\n').encode()
    recovery['files'][1].update(data=base64.b64encode(encoded).decode(), size=len(encoded),
                                 sha256=hashlib.sha256(encoded).hexdigest())
    firmware.update(kind='alternate-firmware-ram-recovery-trial',
                    files=recovery['files'] + firmware['files'][2:],
                    image_dependency=recovery['image_dependency'],
                    required_gates=list(dict.fromkeys(firmware['required_gates'] + recovery['required_gates']
                                                     + ['recovery-watchdog-ownership'])))
    if lease_owner is not None:
        firmware['lease'] = dict(owner=lease_owner, purpose='offline-backup',
                                root_write_authorized=False, persistent_hold_authorized=False)
        firmware['required_gates'] += ['qualified-renewable-recovery-image',
                                       'bound-host-backup-lease-client',
                                       'qualified-lease-loss-and-watchdog-expiry']
    return firmware


def prepare(directory, host, backup, nonce, bundle, timeout=120):
    selected = paths(nonce)
    recipe = compile_recipe(backup, nonce, bundle, timeout)
    return _prepare_staging(directory, host, backup, recipe, paths=selected,
                            steps=(('firmware-start',selected[-2]), ('firmware-fixup',selected[-1]),
                                   ('command',TRIAL_CMDLINE), ('selector',TRYBOOT)))


def prepare_recovery(directory, host, backup, nonce, bundle, image_plan, timeout=120, *, lease_owner=None):
    """Freeze private RAM-trial transactions; no publication or boot authority.

    The caller must separately verify the publication receipt, current policy,
    fallback and physical recovery gates before dispatching these plans.
    """
    if image_plan.get('host') != host:
        raise ValueError('Recovery publication host differs from selector target')
    recipe = compile_recovery_recipe(backup, nonce, bundle, image_plan, timeout, lease_owner=lease_owner)
    records = {record['path']: record for record in backup['files']}
    if any(record['kind'] == 'file' and
           (record['mode'] != 0o700 or record['uid'] != 0 or record['gid'] != 0 or record['xattrs'])
           for record in records.values()):
        raise ValueError('Recovery staging requires private root/0700 boot preimages')
    selected = paths(nonce)
    return _prepare_staging(directory, host, backup, recipe, paths=selected,
                            steps=(('firmware-start', selected[-2]), ('firmware-fixup', selected[-1]),
                                   ('command', TRIAL_CMDLINE), ('selector', TRYBOOT)))


def compile_failed_root_trial(backup, nonce, bundle, timeout=120):
    """Non-deploying watchdog test: fail on RAM root, never mount the real root.

    No software panic reboot or recovery init is selected. A later normal boot
    alone cannot prove this trial executed: console evidence is also required.
    """
    recipe = compile_recipe(backup, nonce, bundle, timeout)
    config, command = [base64.b64decode(item['data']).decode() for item in recipe['files'][:2]]
    lines = []
    for line in config.splitlines():
        key = re.split(r'[=\s]', line.split('#', 1)[0].strip(), maxsplit=1)[0]
        if key in ('ramfsfile', 'ramfsaddr'):
            raise ValueError('Legacy RAM filesystem configuration requires separate review')
        if key not in ('initramfs', 'auto_initramfs'):
            lines.append(line)
    config = '\n'.join(lines) + '\nauto_initramfs=0\n'
    removed = ('root=', 'rootfstype=', 'rootflags=', 'rootwait=', 'resume=', 'resume_offset=',
               'panic=', 'fsck.', 'initrd=')
    tokens = [token for token in command.split() if not token.startswith(removed)
              and token not in ('rootwait', 'ro', 'rw', 'quiet', 'splash', 'noinitrd')]
    command = ' '.join(tokens + ['noinitrd', 'root=/dev/ram0', 'ro',
                                'rdinit=/forge-intentional-failure',
                                'init=/forge-intentional-failure', 'panic=0']) + '\n'
    for item, text in zip(recipe['files'], (config, command)):
        data = text.encode()
        item.update(data=base64.b64encode(data).decode(), size=len(data),
                    sha256=hashlib.sha256(data).hexdigest())
    recipe.update(kind='alternate-firmware-failed-root-trial',
                  expected_failure='Cannot boot RAM root or execute intentional missing init',
                  required_gates=recipe['required_gates'] + ['observed-trial-console-failure'],
                  normal_return_alone_is_proof=False)
    return recipe
