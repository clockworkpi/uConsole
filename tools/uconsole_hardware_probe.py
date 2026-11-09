#!/usr/bin/env python3
"""Capture a comparable uConsole inventory without active I2C bus scans."""
import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import shlex
import subprocess
import sys
from iio_inventory import compare_iio

IIO_SYSFS = Path(__file__).with_name('iio_inventory.py').read_text()
RECOVERY_SYSFS = Path(__file__).with_name('target_recovery_inventory.py').read_text()


KEYBOARD_SYSFS = '''from pathlib import Path
import json
devices = []
for path in Path('/sys/bus/usb/devices').iterdir():
    if not (path / 'idVendor').exists():
        continue
    if ((path / 'idVendor').read_text().strip(),
            (path / 'idProduct').read_text().strip()) != ('1eaf', '0024'):
        continue
    usb = path.resolve()
    reports = []
    for hid in Path('/sys/bus/hid/devices').iterdir():
        if hid.resolve().is_relative_to(usb):
            reports.append({'path': hid.name,
                            'hex': (hid / 'report_descriptor').read_bytes().hex()})
    devices.append({'path': path.name,
                    'identity': {name: (usb / name).read_text().strip()
                                 for name in ('manufacturer', 'product', 'serial')},
                    'usb_descriptors_hex': (usb / 'descriptors').read_bytes().hex(),
                    'report_descriptors': reports})
if len(devices) != 1 or len(devices[0]['report_descriptors']) != 1:
    raise ValueError('Expected exactly one keyboard USB device and HID descriptor')
print(json.dumps({'schema': 1, 'source': 'kernel-sysfs', 'devices': devices}))
'''


SUBSYSTEM_SYSFS = '''from pathlib import Path
import json
import sys
layout = {
 'framebuffer': ('/sys/class/graphics', ('fb[0-9]*',), ('name', 'virtual_size', 'bits_per_pixel', 'stride')),
 'drm': ('/sys/class/drm', ('card*-*',), ('status', 'enabled', 'modes', 'dpms')),
 'backlight': ('/sys/class/backlight', ('*',), ('type', 'brightness', 'actual_brightness', 'max_brightness', 'bl_power')),
 'rfkill': ('/sys/class/rfkill', ('rfkill*',), ('name', 'type', 'state', 'hard', 'soft')),
 'bluetooth': ('/sys/class/bluetooth', ('hci*',), ('name',)),
 'serial': ('/sys/class/tty', ('ttyAMA*', 'ttyUSB*', 'ttyACM*', 'ttyS*'), ('type',)),
 'thermal': ('/sys/class/thermal', ('thermal_zone*',), ('type', 'temp', 'trip_point_0_type', 'trip_point_0_temp')),
 'usb': ('/sys/bus/usb/devices', ('*',), ('busnum', 'devnum', 'devpath', 'speed', 'maxchild', 'idVendor', 'idProduct', 'bDeviceClass', 'bInterfaceClass', 'bInterfaceNumber', 'manufacturer', 'product')),
}
result = {'schema': 1, 'source': 'passive-kernel-sysfs', 'classes': {}, 'errors': []}
for name, (directory, patterns, fields) in layout.items():
    root = Path(directory)
    devices = []
    for path in sorted({p for pattern in patterns for p in root.glob(pattern)}):
        if not path.is_dir():
            continue
        device = {'name': path.name, 'fields': {}}
        for field in fields:
            target = path / field
            if not target.exists():
                continue
            try:
                with target.open() as stream:
                    value = stream.read(65537)
                if len(value) > 65536:
                    raise ValueError('attribute exceeds capture limit')
                device['fields'][field] = value.strip()
            except (OSError, ValueError) as exc:
                result['errors'].append({'class': name, 'device': path.name, 'field': field, 'error': str(exc)})
        for relative in ('driver', 'device/driver'):
            link = path / relative
            if link.is_symlink():
                device['driver'] = link.resolve().name
                break
        devices.append(device)
    result['classes'][name] = {'available': root.is_dir(), 'devices': devices}
print(json.dumps(result))
sys.exit(2 if result['errors'] else 0)
'''


PROBES = {
    'recovery': ['python3', '-c', RECOVERY_SYSFS],
    'kernel': ['uname', '-a'],
    'model': ['sh', '-c', "tr -d '\\000' </proc/device-tree/model"],
    'cpu': ['lscpu'],
    'usb': ['lsusb', '-v'],
    'keyboard_descriptors': ['python3', '-c', KEYBOARD_SYSFS],
    'subsystems': ['python3', '-c', SUBSYSTEM_SYSFS],
    'iio': ['python3', '-c', IIO_SYSFS],
    'usb_topology': ['lsusb', '-t'],
    'sound': ['sh', '-c', 'cat /proc/asound/cards /proc/asound/pcm'],
    'storage': ['lsblk', '--json', '--bytes', '--output', 'NAME,TYPE,SIZE,RO,FSTYPE,MOUNTPOINTS'],
    'input': ['sh', '-c', 'cat /proc/bus/input/devices'],
    'network': ['ip', '-details', 'link'],
    'power_supply': ['sh', '-c',
        "for d in /sys/class/power_supply/*; do test -d \"$d\" || continue; echo ===$d; "
        "for f in type status capacity voltage_now current_now online; do test -r \"$d/$f\" && echo \"$f=$(cat \"$d/$f\")\"; done; done"],
    'gpio': ['gpioinfo'],
    'i2c': ['sh', '-c',
        "for d in /sys/bus/i2c/devices/*; do test -e \"$d\" || continue; "
        "printf '%s\\n' \"${d##*/}\"; "
        "for f in name modalias; do if test -r \"$d/$f\"; then "
        "printf '%s=' \"$f\"; cat \"$d/$f\" || exit; fi; done; "
        "if test -L \"$d/driver\"; then readlink \"$d/driver\" || exit; fi; done"],
    'modules': ['lsmod'],
    'cmdline': ['sh', '-c', 'cat /proc/cmdline'],
}


def run_command(command, ssh):
    argv = ['ssh', '-o', 'BatchMode=yes', '--', ssh, shlex.join(command)] if ssh else command
    try:
        process = subprocess.run(argv, text=True, capture_output=True, timeout=45)
        return {'argv': command, 'exit_code': process.returncode,
                'stdout': process.stdout, 'stderr': process.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'argv': command, 'exit_code': None, 'stdout': '', 'stderr': str(exc)}


def capture(ssh=None, probes=None):
    selected = list(PROBES) if probes is None else list(dict.fromkeys(probes))
    if not selected or any(name not in PROBES for name in selected):
        raise ValueError('Unknown or empty probe selection')
    return {'schema': 1, 'target': ssh or 'local',
            'captured_at_utc': datetime.now(timezone.utc).isoformat(),
            'probes': {name: run_command(PROBES[name], ssh) for name in selected}}


def subsystem_contract(snapshot):
    """Normalize enumeration only; preserve state, multiplicity and USB port paths."""
    required = {'framebuffer', 'drm', 'backlight', 'rfkill', 'bluetooth', 'serial', 'thermal', 'usb'}
    if (not isinstance(snapshot, dict) or type(snapshot.get('schema')) is not int or snapshot['schema'] != 1
            or snapshot.get('source') != 'passive-kernel-sysfs'
            or not isinstance(snapshot.get('errors'), list)
            or not isinstance(snapshot.get('classes'), dict)
            or set(snapshot['classes']) != required):
        raise ValueError('Invalid or incomplete subsystem capture schema')
    result = {}
    for name, group in snapshot['classes'].items():
        if (not isinstance(group, dict) or type(group.get('available')) is not bool
                or not isinstance(group.get('devices'), list)
                or (not group['available'] and group['devices'])):
            raise ValueError('Invalid subsystem availability or device list')
        devices = []
        for device in group['devices']:
            if (not isinstance(device, dict) or not isinstance(device.get('name'), str)
                    or not isinstance(device.get('fields'), dict)):
                raise ValueError('Invalid subsystem device record')
            fields = dict(device['fields'])
            if any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items()):
                raise ValueError('Invalid subsystem attribute type')
            identity = device['name']
            if name == 'usb':
                fields.pop('busnum', None)
                fields.pop('devnum', None)
                identity = re.sub(r'^usb\d+$', 'root', identity)
                identity = re.sub(r'^\d+-', 'port-', identity)
            elif name == 'drm':
                identity = re.sub(r'^card\d+-', '', identity)
            elif name == 'framebuffer':
                identity = re.sub(r'^fb\d+$', 'framebuffer', identity)
            elif name == 'rfkill':
                identity = re.sub(r'^rfkill\d+$', 'rfkill', identity)
            normalized = {'name': identity, 'fields': fields}
            if 'driver' in device:
                if not isinstance(device['driver'], str):
                    raise ValueError('Invalid subsystem driver')
                normalized['driver'] = device['driver']
            devices.append(normalized)
        result[name] = {'available': group['available'],
                        'devices': sorted(devices, key=lambda d: json.dumps(d, sort_keys=True))}
    return result


def compare_subsystems(reference, candidate):
    left, right = subsystem_contract(reference), subsystem_contract(candidate)
    differences = [{'class': name, 'reference': left[name], 'candidate': right[name]}
                   for name in sorted(left) if left[name] != right[name]]
    complete = not reference['errors'] and not candidate['errors']
    return {'scope': 'allowlisted kernel attributes; not hardware equivalence',
            'complete': complete, 'matching': complete and not differences,
            'reference_errors': reference['errors'], 'candidate_errors': candidate['errors'],
            'differences': differences}


def compare(reference, candidate):
    differences = []
    incomplete = []
    semantic = None
    semantic_iio = None
    for name in sorted(set(reference['probes']) | set(candidate['probes'])):
        left = reference['probes'].get(name, {})
        right = candidate['probes'].get(name, {})
        if left.get('exit_code') != 0 or right.get('exit_code') != 0:
            incomplete.append(name)
        changed = left.get('stdout') != right.get('stdout') or left.get('exit_code') != right.get('exit_code')
        if name == 'iio':
            try:
                semantic_iio = compare_iio(json.loads(left['stdout']), json.loads(right['stdout']))
                changed = not semantic_iio['matching']
                if not semantic_iio['complete']:
                    incomplete.append(name)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                incomplete.append(name)
                semantic_iio = {'complete': False, 'matching': False, 'error': str(exc)}
                changed = True
        if name == 'subsystems' and left.get('exit_code') == right.get('exit_code') == 0:
            try:
                semantic = compare_subsystems(json.loads(left['stdout']), json.loads(right['stdout']))
                changed = not semantic['matching']
                if not semantic['complete']:
                    incomplete.append(name)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                incomplete.append(name)
                semantic = {'complete': False, 'matching': False, 'error': str(exc)}
                changed = True
        if changed:
            differences.append({'probe': name,
                'reference_exit': left.get('exit_code'), 'candidate_exit': right.get('exit_code'),
                'reference_lines': len(left.get('stdout', '').splitlines()),
                'candidate_lines': len(right.get('stdout', '').splitlines())})
    return {'schema': 1, 'reference': reference.get('target'), 'candidate': candidate.get('target'),
            'matching': bool(reference['probes']) and not differences and not incomplete,
            'complete': bool(reference['probes']) and not incomplete,
            'incomplete_probes': sorted(set(incomplete)), 'differences': differences,
            'semantic_subsystems': semantic, 'semantic_iio': semantic_iio}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    collect = sub.add_parser('capture')
    collect.add_argument('output', type=Path)
    collect.add_argument('--ssh', help='SSH target; authentication must already be configured')
    collect.add_argument('--probe', action='append', choices=sorted(PROBES),
                         help='Capture only this probe (repeatable); default is all probes')
    diff = sub.add_parser('compare')
    diff.add_argument('reference', type=Path)
    diff.add_argument('candidate', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'capture':
            with args.output.open('x') as destination:
                result = capture(args.ssh, args.probe)
                destination.write(json.dumps(result, indent=2) + '\n')
        else:
            result = compare(json.loads(args.reference.read_text()), json.loads(args.candidate.read_text()))
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
