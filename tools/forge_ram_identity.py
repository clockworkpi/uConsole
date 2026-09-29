"""Read-only RAM recovery identity checks; never authorize disk writes."""
import re
import shlex


READER = r'''import json,os
from pathlib import Path
def read(path, maximum):
    with open(path, 'rb') as stream:
        data=stream.read(maximum+1)
    if len(data)>maximum: raise ValueError('Oversized recovery observation')
    return data.decode().rstrip('\0\n')
before=read('/proc/sys/kernel/random/boot_id',128)
serial=Path('/proc/device-tree/serial-number')
result=dict(boot_id=before,uid=os.geteuid(),kernel=os.uname().release,
            serial=read(serial,128) if serial.is_file() else None,
            cmdline=read('/proc/cmdline',65536),mountinfo=read('/proc/self/mountinfo',1048576))
if read('/proc/sys/kernel/random/boot_id',128)!=before:
    raise ValueError('Recovery boot identity changed')
print(json.dumps(result))
'''


def command():
    return '/usr/bin/python3 -I -S -c ' + shlex.quote(READER)


def verify(observed, nonce, kernel, serial, *, mode='physical'):
    if mode not in ('physical', 'emulated') or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Invalid expected recovery identity')
    if not isinstance(kernel, str) or not kernel:
        raise ValueError('Expected kernel release is required')
    if mode == 'physical' and (not isinstance(serial, str) or not re.fullmatch('[0-9a-f]{16}', serial)):
        raise ValueError('Physical recovery requires the expected hardware serial')
    fields = {'boot_id', 'uid', 'kernel', 'serial', 'cmdline', 'mountinfo'}
    if not isinstance(observed, dict) or set(observed) != fields:
        raise ValueError('Unexpected recovery observation schema')
    if type(observed['uid']) is not int or observed['uid'] != 0 or observed['kernel'] != kernel:
        raise ValueError('Recovery privilege or kernel differs')
    if serial is not None and observed['serial'] != serial:
        raise ValueError('Recovery hardware serial differs')
    if not isinstance(observed['boot_id'], str) or not re.fullmatch(
            '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', observed['boot_id']):
        raise ValueError('Invalid recovery boot identity')
    if not isinstance(observed['cmdline'], str) or len(observed['cmdline']) > 65536:
        raise ValueError('Invalid recovery command line')
    tokens = observed['cmdline'].split()
    for prefix, expected in (('uconsole.recovery=', '1'), ('uconsole.forge_trial=', nonce),
                             ('root=', '/dev/ram0'), ('rdinit=', '/init')):
        if [t for t in tokens if t.startswith(prefix)] != [prefix + expected]:
            raise ValueError('Recovery command-line identity differs: ' + prefix)
    emulator = [t for t in tokens if t.startswith('uconsole.emulator=')]
    if emulator != (['uconsole.emulator=1'] if mode == 'emulated' else []):
        raise ValueError('Recovery physical/emulated mode differs')
    mounts = observed['mountinfo']
    if not isinstance(mounts, str) or not mounts or len(mounts) > 1048576:
        raise ValueError('Missing or oversized recovery mount inventory')
    roots = 0
    for line in mounts.splitlines():
        left, separator, right = line.partition(' - ')
        fields, fs = left.split(), right.split()
        if not separator or len(fields) < 6 or len(fs) != 3:
            raise ValueError('Invalid mount inventory record')
        if not re.fullmatch('0:[0-9]+', fields[2]) or fs[0] not in (
                'rootfs', 'tmpfs', 'proc', 'sysfs', 'devtmpfs', 'devpts'):
            raise ValueError('Recovery has a persistent or unqualified filesystem mounted')
        if fields[4] == '/':
            roots += 1
            if fs[0] not in ('rootfs', 'tmpfs'):
                raise ValueError('Recovery root is not RAM-backed')
    if roots != 1:
        raise ValueError('Expected exactly one RAM root')
    return dict(status='verified-ram-session', boot_id=observed['boot_id'], nonce=nonce,
                mode=mode, hardware_serial=observed['serial'], mutation_authorized=False,
                fallback_qualified=False)
