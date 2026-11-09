#!/usr/bin/env python3
"""Encode firmware-oracle calls using pinned USBComposite report semantics.

This is host-side report encoding, not USB timing, MCU execution or hardware
qualification. The firmware scanner/keymap remains in keyboard-oracle.
"""
import argparse
import json
from pathlib import Path
import sys


# Compiled _ZL12ascii_to_hid from the pinned STM32F1 2021.2.22 firmware ELF.
# SHA-256: 52369ccefa4602cc7e0143b423762911c89c830c9ca9ef6e099178b470b0c3d5
ASCII_TO_HID = bytes.fromhex(
    '00000000000000002a2b280000000000000000000000000000000000000000002c9e'
    'b4a0a1a2a434a6a7a5ae362d3738271e1f20212223242526b333b62eb7b89f848586'
    '8788898a8b8c8d8e8f909192939495969798999a9b9c9d2f3130a3ad35040506070809'
    '0a0b0c0d0e0f101112131415161718191a1b1c1dafb1b0b500')
MAX_TRACE = 4 * 1024 * 1024
MAX_EVENTS = 10000


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Expected integer in {low}..{high}')
    return value


class Reports:
    """Stateful equivalents of the four USBComposite report senders.

    Mirrors Keyboard.cpp, Mouse.cpp, Consumer.cpp and Joystick.cpp, including
    duplicate reports, first-free key slots, silent seventh-key refusal and
    non-reference-counted modifier release. Do not substitute idealized NKRO.
    """
    def __init__(self, leds=0):
        self.keys = [0] * 6
        self.modifiers = 0
        self.adjust_caps = True
        self.set_leds(leds)
        self.buttons = 0
        self.joy_buttons = 0
        self.hat = 15
        self.axes = [512, 512, 512, 512, 0, 0]

    def set_leds(self, value):
        self.leds = integer(value, 0, 255)

    def keycode(self, value):
        key = integer(value, 0, 65535) & 255  # Firmware API narrows to uint8_t.
        if self.adjust_caps and self.leds & 2:
            if ord('a') <= key <= ord('z'):
                key -= 32
            elif ord('A') <= key <= ord('Z'):
                key += 32
        if key < 128:
            code = ASCII_TO_HID[key]
            return code & 127, 2 if code & 128 else 0
        if key >= 136:
            return key - 136, 0
        return 0, 1 << (key - 128)

    def keyboard(self, key, pressed):
        code, modifiers = self.keycode(key)
        if not code and not modifiers:
            return []
        if pressed:
            if code and code not in self.keys:
                if 0 not in self.keys:
                    return []
                self.keys[self.keys.index(0)] = code
            self.modifiers |= modifiers
        else:
            if code in self.keys and code:
                self.keys[self.keys.index(code)] = 0
            self.modifiers &= ~modifiers
        return [bytes((2, self.modifiers, 0, *self.keys))]

    def mouse(self, x=0, y=0, wheel=0):
        return bytes((1, self.buttons, x & 255, y & 255, wheel & 255))

    def joystick(self):
        bits = self.joy_buttons | (self.hat << 32)
        for index, value in enumerate(self.axes):
            bits |= value << (36 + 10 * index)
        return b'\x14' + bits.to_bytes(12, 'little')

    def feed(self, event, arguments):
        if not isinstance(arguments, list) or len(arguments) != 3 or any(type(n) is not int for n in arguments):
            raise ValueError('Expected three integer oracle arguments')
        a, b, c = arguments
        if event not in ('mouse_move', 'state') and c != 0:
            raise ValueError('Unused oracle argument must be zero')
        if event not in ('mouse_move', 'state', 'pwm', 'joystick_button') and b != 0:
            raise ValueError('Unused oracle argument must be zero')
        if event in ('keyboard_press', 'keyboard_release'):
            return self.keyboard(a, event == 'keyboard_press')
        if event == 'caps_adjust':
            self.adjust_caps = bool(integer(a, 0, 1))
        elif event == 'consumer_press':
            return [b'\x03' + integer(a, 0, 65535).to_bytes(2, 'little')]
        elif event == 'consumer_release':
            integer(a, 0, 0)
            return [b'\x03\0\0']
        elif event == 'mouse_move':
            for value in arguments:
                integer(value, -128, 127)
            return [self.mouse(a, b, c)]
        elif event in ('mouse_press', 'mouse_release', 'mouse_click'):
            integer(a, 0, 255)
            if event == 'mouse_click':
                self.buttons = a
                down = self.mouse()
                self.buttons = 0
                return [down, self.mouse()]
            buttons = self.buttons | a if event == 'mouse_press' else self.buttons & ~a
            if buttons != self.buttons:
                self.buttons = buttons
                return [self.mouse()]
        elif event in ('joystick_x', 'joystick_y'):
            self.axes[event == 'joystick_y'] = min(integer(a, 0, 65535), 1023)
            return [self.joystick()]
        elif event == 'joystick_button':
            mask = 1 << (integer(a, 1, 32) - 1)
            integer(b, 0, 1)
            self.joy_buttons = self.joy_buttons | mask if b else self.joy_buttons & ~mask
            return [self.joystick()]
        elif event == 'state':
            integer(a, 0, 1)
            integer(b, 0, 1)
            integer(c, 0, 2)
        elif event == 'pwm':
            integer(a, 0, 255)
            integer(b, 0, 65535)
        else:
            raise ValueError(f'Unsupported oracle event: {event}')
        return []


def encode_trace(records, *, leds=0):
    """Validate/snapshot the whole trace before a caller can inject any result."""
    iterator = iter(records)
    header = next(iterator, None)
    if (header != {'evidence': 'host-firmware-semantic-oracle', 'schema': 1}
            or type(header.get('schema')) is not int):
        raise ValueError('Expected firmware oracle schema 1 header')
    encoder, reports, previous = Reports(leds), [], 0
    for count, record in enumerate(iterator, 1):
        if count > MAX_EVENTS:
            raise ValueError('Oracle trace exceeds event limit')
        if not isinstance(record, dict) or set(record) != {'us', 'event', 'args'}:
            raise ValueError('Invalid oracle event record')
        timestamp = integer(record['us'], previous, (1 << 64) - 1)
        previous = timestamp
        for data in encoder.feed(record['event'], record['args']):
            reports.append({'us': timestamp, 'report_hex': data.hex()})
    return reports


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('trace', type=Path, nargs='?', help='oracle JSONL; default stdin')
    cli.add_argument('--leds', type=int, default=0, help='initial host LED byte; not live feedback')
    args = cli.parse_args()
    if args.trace:
        with args.trace.open() as source:
            content = source.read(MAX_TRACE + 1)
    else:
        content = sys.stdin.read(MAX_TRACE + 1)
    if len(content.encode()) > MAX_TRACE:
        raise ValueError('Oracle trace exceeds size limit')
    reports = encode_trace((json.loads(line) for line in content.splitlines() if line.strip()), leds=args.leds)
    print(json.dumps({'evidence': 'firmware-derived-hid-reports', 'schema': 1,
                      'physical_fidelity': 'unverified', 'initial_leds': args.leds,
                      'timing': 'oracle virtual microseconds, not USB polling time', 'reports': reports}))


if __name__ == '__main__':
    main()
