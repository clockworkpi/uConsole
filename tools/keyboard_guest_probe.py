#!/usr/bin/env python3
"""Disposable Linux guest probe for the experimental composite USB keyboard.

Run inside the guest, never as a host hardware test. Reads input/CDC devices;
does not flash firmware. The caller supplies a private readiness marker.
"""
import argparse
import json
import os
from pathlib import Path
import re
import select
import struct
import termios
import time


EXPECTED = {(1, 30, 1), (1, 30, 0),       # KEY_A press/release
            (2, 0, 5), (2, 1, -3), (2, 8, 1),  # relative X/Y/wheel
            (1, 115, 1), (1, 115, 0),     # KEY_VOLUMEUP
            (1, 288, 1), (1, 288, 0),     # BTN_TRIGGER
            (3, 0, 123), (3, 1, 789)}     # ABS_X/Y

FIRMWARE_EXPECTED = {(1, 30, 1), (1, 30, 0), (1, 59, 1), (1, 59, 0)}
CAPS_EXPECTED = {(1, code, value) for code in (58, 42, 30) for value in (0, 1)}
POINTER_EXPECTED = {(1, code, value) for code in (272, 273, 274) for value in (0, 1)}
SCROLL_EXPECTED = POINTER_EXPECTED | {(1, 57, 0), (1, 57, 1), (2, 8, 1), (2, 8, -1)}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--ready', required=True)
    cli.add_argument('--profile', choices=('raw', 'firmware', 'caps', 'pointer', 'pointer-scroll'), default='raw')
    args = cli.parse_args()
    expected = {'raw': EXPECTED, 'firmware': FIRMWARE_EXPECTED, 'caps': CAPS_EXPECTED,
                'pointer': POINTER_EXPECTED, 'pointer-scroll': SCROLL_EXPECTED}[args.profile]
    if not re.fullmatch(r'UC_KEYBOARD_READY_[0-9a-f]{32}', args.ready):
        raise ValueError('Invalid private readiness marker')
    devices = [p for p in Path('/sys/bus/usb/devices').iterdir()
               if (p / 'idVendor').exists() and (p / 'idVendor').read_text().strip() == '1eaf'
               and (p / 'idProduct').read_text().strip() == '0024']
    if len(devices) != 1:
        raise ValueError(f'Expected one composite keyboard, found {len(devices)}')
    usb = devices[0].resolve()
    identity = {name: (usb / name).read_text().strip()
                for name in ('manufacturer', 'product', 'serial')}
    if identity != {'manufacturer': 'ClockworkPI', 'product': 'uConsole', 'serial': '20230713'}:
        raise ValueError(f'Incorrect USB identity: {identity}')
    drivers = [(usb / f'{usb.name}:1.{n}' / 'driver').resolve(strict=True).name for n in range(3)]
    if drivers != ['usbhid', 'cdc_acm', 'cdc_acm']:
        raise ValueError(f'Incorrect driver bindings: {drivers}')
    tty = [p for p in Path('/sys/class/tty').glob('ttyACM*')
           if (p / 'device').resolve().is_relative_to(usb)]
    if len(tty) != 1:
        raise ValueError('CDC tty is missing or ambiguous')
    fd = os.open('/dev/' + tty[0].name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[4] = attrs[5] = termios.B9600
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        if termios.tcgetattr(fd)[4:6] != [termios.B9600, termios.B9600]:
            raise ValueError('CDC termios line coding did not round-trip')
    finally:
        os.close(fd)
    inputs = [p for p in Path('/sys/class/input').glob('event*')
              if (p / 'device').resolve().is_relative_to(usb)]
    if not inputs:
        raise ValueError('No Linux input devices bound to composite HID')
    handles, records, seen = {}, [], set()
    event = struct.Struct('llHHi')
    try:
        for p in inputs:
            handles[os.open('/dev/input/' + p.name, os.O_RDONLY | os.O_NONBLOCK)] = p.name
        # Discard initialization ABS/SYN reports before announcing readiness.
        for descriptor in handles:
            try:
                while os.read(descriptor, event.size * 64):
                    pass
            except BlockingIOError:
                pass
        with open('/dev/ttyAMA1', 'w') as console:
            console.write('\n' + args.ready + '\n')
            console.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not expected <= seen:
            ready, _, _ = select.select(list(handles), [], [], max(0, deadline - time.monotonic()))
            for descriptor in ready:
                data = os.read(descriptor, event.size * 64)
                if not data or len(data) % event.size:
                    raise ValueError('Truncated or closed evdev stream')
                for _, _, kind, code, value in event.iter_unpack(data):
                    seen.add((kind, code, value))
                    records.append({'input': handles[descriptor], 'type': kind, 'code': code, 'value': value})
        result = {'identity': identity, 'drivers': drivers, 'cdc': tty[0].name,
                  'input_devices': [p.name for p in inputs], 'events': records,
                  'missing': sorted(expected - seen), 'profile': args.profile,
                  'usb_descriptors_hex': (usb / 'descriptors').read_bytes().hex()}
        print(json.dumps(result), flush=True)
        if result['missing']:
            raise ValueError('Not all injected reports reached Linux evdev')
    finally:
        for descriptor in handles:
            os.close(descriptor)


if __name__ == '__main__':
    main()
