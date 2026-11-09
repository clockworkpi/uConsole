#!/usr/bin/env python3
"""Compare host report encoding to actual selected USBComposite method bodies.

Requires the pinned STM32F1 core source installed by the firmware build. Does
not execute MCU code or prove USB timing/physical fidelity. No files are altered.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import tempfile

from keyboard_reports import Reports, ASCII_TO_HID
from keyboard_usb_contract import extract_object


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--core', type=Path, required=True, help='USBComposite source directory')
    cli.add_argument('--elf', type=Path, required=True, help='built unstripped keyboard firmware')
    cli.add_argument('--output', type=Path, help='create an exclusive JSON evidence file')
    args = cli.parse_args()
    core = args.core.resolve()
    sources = {name: hashlib.sha256((core / name).read_bytes()).hexdigest()
               for name in ('Keyboard.cpp', 'Mouse.cpp', 'Consumer.cpp', 'Joystick.cpp', 'USBHID.h')}
    elf = args.elf.read_bytes()
    if extract_object(elf, b'_ZL12ascii_to_hid') != ASCII_TO_HID:
        raise ValueError('Compiled ASCII table does not match the encoder')
    events = []
    for leds in (0, 2):
        events.append(('leds', [leds, 0, 0]))
        for caps in (0, 1):
            events.append(('caps_adjust', [caps, 0, 0]))
            for key in range(256):
                events.extend((('keyboard_press', [key, 0, 0]), ('keyboard_release', [key, 0, 0])))
    # Stateful overlap/rollover/button/axis traces use a fixed, reproducible seed.
    rng = random.Random(20230713)
    for _ in range(4000):
        name = rng.choice(('keyboard_press', 'keyboard_release', 'mouse_press', 'mouse_release',
                           'mouse_click', 'mouse_move', 'consumer_press', 'consumer_release',
                           'joystick_button', 'joystick_x', 'joystick_y'))
        if name.startswith('keyboard_'):
            values = [rng.randrange(256), 0, 0]
        elif name == 'mouse_move':
            values = [rng.randrange(-128, 128) for _ in range(3)]
        elif name.startswith('mouse_'):
            values = [rng.randrange(256), 0, 0]
        elif name == 'consumer_release':
            values = [0, 0, 0]
        elif name == 'joystick_button':
            values = [rng.randrange(1, 33), rng.randrange(2), 0]
        else:
            values = [rng.randrange(65536), 0, 0]
        events.append((name, values))
    encoder, expected = Reports(), []
    for name, values in events:
        if name == 'leds':
            encoder.set_leds(values[0])
        else:
            expected.extend(data.hex() for data in encoder.feed(name, values))
    with tempfile.TemporaryDirectory(prefix='uc-report-reference-') as directory:
        binary = Path(directory) / 'reference'
        subprocess.run([*shlex.split(os.environ.get('CXX', 'c++')), '-std=c++17',
                        '-I', str(core), str(Path(__file__).with_name('keyboard-oracle') / 'report_reference.cpp'),
                        '-o', str(binary)], check=True, timeout=60)
        result = subprocess.run([str(binary)], input=''.join(
            f'{name} {a} {b} {c}\n' for name, (a, b, c) in events),
            text=True, capture_output=True, check=True, timeout=20)
    observed = result.stdout.splitlines()
    if observed != expected:
        mismatch = next((i for i, pair in enumerate(zip(observed, expected)) if pair[0] != pair[1]),
                        min(len(observed), len(expected)))
        raise ValueError(f'Report stream mismatch at {mismatch}; actual={observed[mismatch:mismatch+1]} '
                         f'expected={expected[mismatch:mismatch+1]}')
    record = {'validation': 'passed', 'events': len(events), 'reports': len(expected),
                      'sources_sha256': sources, 'elf_sha256': hashlib.sha256(elf).hexdigest(),
                      'report_stream_sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
                      'reference_shim_sha256': hashlib.sha256(
                          (Path(__file__).with_name('keyboard-oracle') / 'report_reference.cpp').read_bytes()).hexdigest(),
                      'encoder_sha256': hashlib.sha256(Path(__file__).with_name('keyboard_reports.py').read_bytes()).hexdigest(),
                      'physical_fidelity': 'unverified'}
    document = json.dumps(record, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as stream:
            stream.write(document)
    print(document, end='')


if __name__ == '__main__':
    main()
