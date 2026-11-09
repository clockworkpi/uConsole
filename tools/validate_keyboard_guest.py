#!/usr/bin/env python3
"""Boot a disposable overlay to check composite keyboard Linux driver/input paths."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import threading
import time
import uuid

from forge_filesystem import check_overlay_root
from forge_keyboard import KeyboardBridge
from forge_runtime import Runtime
from forge_workspace import sha256
from keyboard_guest_probe import EXPECTED, FIRMWARE_EXPECTED, CAPS_EXPECTED, POINTER_EXPECTED, SCROLL_EXPECTED
from uconsole_emulator import parser, wait_for_log
from validate_desktop_preparation_gui import fixture


def verify_firmware_events(observed):
    # A set alone would also accept swapped releases or unrelated stale events.
    keys = [(item['code'], item['value']) for item in observed['events']
            if item['type'] == 1 and item['value'] in (0, 1)]
    if observed.get('profile') != 'firmware' or keys != [(30, 1), (30, 0), (59, 1), (59, 0)]:
        raise ValueError('Firmware key press/release order or profile did not match')


def verify_caps_report(reports):
    if not any(r['report_hex'] == '020200390400000000' and r['leds'] & 2
               for r in reports):
        raise ValueError('Guest Caps LED did not adjust the firmware A report')


def verify_pointer_events(observed):
    buttons = [(e['code'], e['value']) for e in observed['events'] if e['type'] == 1]
    expected = [(272, 1), (272, 0), (274, 1), (274, 0), (273, 1), (273, 0)]
    if observed.get('profile') == 'pointer-scroll':
        expected = [(57, 1), (57, 0)] * 2 + expected
        wheel = [e['value'] for e in observed['events'] if e['type'] == 2 and e['code'] == 8]
        if wheel != [1, -1]:
            raise ValueError('Select-scroll wheel directions did not match')
    if observed.get('profile') not in ('pointer', 'pointer-scroll') or buttons != expected:
        raise ValueError('Pointer button order or focus-loss release did not match')
    for axis in (0, 1):
        if sum(e['value'] for e in observed['events'] if e['type'] == 2 and e['code'] == axis) <= 0:
            raise ValueError('Missing positive relative pointer movement')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--oracle', type=Path, help='exercise persistent firmware actions instead of raw reports')
    cli.add_argument('--gui', action='store_true', help='drive real Tk deck buttons; requires --oracle and a display')
    cli.add_argument('--host-typing', action='store_true', help='drive focused Tk key events; requires --gui')
    cli.add_argument('--pointer', action='store_true', help='drive focused Tk pointer events; requires --gui')
    cli.add_argument('--select-scroll', action='store_true', help='also verify explicit Select-scroll and Space side effects; requires --pointer')
    cli.add_argument('--caps-feedback', action='store_true', help='test real guest Caps LED feedback; requires --oracle')
    options = cli.parse_args()
    if options.gui and not options.oracle:
        cli.error('--gui requires --oracle')
    if options.host_typing and not options.gui:
        cli.error('--host-typing requires --gui')
    if options.pointer and (not options.gui or options.host_typing):
        cli.error('--pointer requires --gui and cannot be combined with --host-typing')
    if options.select_scroll and not options.pointer:
        cli.error('--select-scroll requires --pointer')
    if options.caps_feedback and (not options.oracle or options.gui):
        cli.error('--caps-feedback requires --oracle and cannot be combined with --gui')
    image, output = options.image.resolve(), options.output.resolve()
    if not re.fullmatch('[0-9a-f]{64}', options.sha256) or sha256(image) != options.sha256:
        raise ValueError('Base image hash mismatch')
    fixture(image, output, options.sha256)  # Exclusive directory; immutable backing file.
    record = {'validation': 'failed', 'image_sha256': options.sha256,
              'physical_fidelity': 'unverified', 'forced_cleanup': False}
    runtime = Runtime(parser().parse_args(['--workspace', str(output), 'run', '--mode', 'maintenance',
                                          '--keyboard', 'composite']))
    token = uuid.uuid4().hex
    profile = 'pointer' if options.pointer else 'caps' if options.caps_feedback else 'firmware' if options.oracle else 'raw'
    if options.select_scroll:
        profile = 'pointer-scroll'
    expected = {'raw': EXPECTED, 'firmware': FIRMWARE_EXPECTED, 'caps': CAPS_EXPECTED,
                'pointer': POINTER_EXPECTED, 'pointer-scroll': SCROLL_EXPECTED}[profile]
    record['profile'] = profile
    bridge = None
    guest_path = f'/tmp/keyboard-probe-{token}.py'
    cancel = threading.Event()
    try:
        runtime.start()
        record['runtime_identity'] = runtime.identity
        wait_for_log(output / 'serial.log', b'root@(none):/#', runtime.process, 120)
        setup = runtime.execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                                'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                                'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; '
                                'modprobe usbhid; modprobe cdc_acm', cancel=cancel)
        record['setup'] = setup
        if setup['exit_code']:
            raise ValueError('Guest HID/CDC module setup failed')
        probe = Path(__file__).with_name('keyboard_guest_probe.py')
        record['probe_sha256'] = sha256(probe)
        runtime.upload(probe, guest_path)
        ready = 'UC_KEYBOARD_READY_' + token
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(runtime.execute, f'python3 {guest_path} --ready {ready} --profile {profile}', 45, cancel=cancel)
            try:
                wait_for_log(output / 'serial.log', ready.encode(), runtime.process, 25)
                bits = (8 << 32) | (123 << 36) | (789 << 46) | (511 << 56) | (511 << 66) | (511 << 76) | (511 << 86)
                reports = ['020000040000000000', '020000000000000000', '010005fd01',
                           '03e900', '030000', '14' + (bits | 1).to_bytes(12, 'little').hex(),
                           '14' + bits.to_bytes(12, 'little').hex()]
                if options.gui:
                    from validate_keyboard_deck import exercise
                    record['oracle_sha256'] = sha256(options.oracle.resolve())
                    record['gui'] = {}
                    exercise(runtime, options.oracle, record['gui'], host_typing=options.host_typing,
                             pointer=options.pointer, select_scroll=options.select_scroll)
                elif options.oracle:
                    record['oracle_sha256'] = sha256(options.oracle.resolve())
                    bridge = KeyboardBridge(runtime, options.oracle)
                    record['actions'] = []
                    commands_sequence = (
                            [('matrix', 4, 2, 1), ('run', 10)],
                            [('matrix', 4, 2, 0), ('run', 10)],
                            [('matrix', 7, 2, 1), ('run', 10)],
                            [('matrix', 1, 0, 1), ('run', 10)],
                            [('matrix', 7, 2, 0), ('run', 10)],
                            [('matrix', 1, 0, 0), ('run', 10)])
                    if options.caps_feedback:
                        commands_sequence = (
                            [('matrix', 7, 2, 1), ('run', 10)],
                            [('matrix', 2, 3, 1), ('run', 10)],
                            [('matrix', 4, 2, 1), ('run', 10)],
                            [('matrix', 4, 2, 0), ('run', 10)],
                            [('matrix', 2, 3, 0), ('run', 10)],
                            [('matrix', 7, 2, 0), ('run', 10)])
                    for index, commands in enumerate(commands_sequence):
                        if options.caps_feedback and index == 2:
                            deadline = time.monotonic() + 5
                            while not bridge.transport()['leds'] & 2:
                                if time.monotonic() >= deadline:
                                    raise TimeoutError('Guest did not send Caps Lock LED feedback')
                                threading.Event().wait(0.01)
                            record['caps_feedback'] = bridge.transport()
                        record['actions'].append(bridge.apply(commands))
                    if options.caps_feedback:
                        verify_caps_report(record['actions'][2]['reports'])
                    record['oracle_pid'] = bridge.process.pid
                    bridge.close()
                else:
                    record['reports'] = reports
                    for report in reports:
                        runtime.control('qom-set', {'path': '/machine/peripheral/deck',
                                                   'property': 'inject-report', 'value': report})
                result = job.result(timeout=55)
            except BaseException:
                cancel.set()
                try:
                    record['probe'] = job.result(timeout=60)
                except Exception as exc:
                    record['probe_error'] = str(exc)
                raise
        record['probe'] = result
        if result['exit_code']:
            raise ValueError('Linux composite keyboard probe failed')
        record['observed'] = json.loads(result['stdout'])
        observed = record['observed']
        actual_events = {(item['type'], item['code'], item['value']) for item in observed['events']}
        if not expected <= actual_events or observed['missing']:
            raise ValueError('Recorded evdev events do not establish all injected inputs')
        if options.pointer:
            verify_pointer_events(observed)
        elif options.oracle and not options.caps_feedback:
            verify_firmware_events(observed)
        if observed['drivers'] != ['usbhid', 'cdc_acm', 'cdc_acm']:
            raise ValueError('Recorded driver bindings do not match the composite contract')
        record['transport_state'] = runtime.control('qom-get', {
            'path': '/machine/peripheral/deck', 'property': 'transport-state'})
        cleanup = runtime.execute(f'rm -- {guest_path}', cancel=threading.Event())
        if cleanup['exit_code']:
            raise ValueError('Could not remove exact disposable guest probe')
        runtime.stop()
        if runtime.process.returncode != 0:
            raise ValueError('Keyboard test guest did not exit cleanly')
        record['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        record['base_unchanged'] = sha256(image) == options.sha256
        if not record['base_unchanged']:
            raise ValueError('Backing image changed')
        record['validation'] = 'passed'
    except BaseException as exc:
        record['error'] = str(exc)
        raise
    finally:
        try:
            if bridge is not None:
                record['last_bridge_attempt'] = bridge.last_attempt
                try:
                    bridge.close()
                except Exception as exc:
                    record['bridge_cleanup_error'] = str(exc)
                    record['validation'] = 'failed'
            if runtime.process is not None and runtime.process.poll() is None:
                try:
                    runtime.stop()
                except Exception as exc:
                    record['cleanup_error'] = str(exc)
                    record['forced_cleanup'] = True
                    runtime.stop(force=True)
            if 'bridge_cleanup_error' in record:
                raise RuntimeError('Firmware bridge cleanup failed; see acceptance record')
        finally:
            (output / 'acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
            print(f'Keyboard Linux validation: {record["validation"]}; {output / "acceptance.json"}', flush=True)


if __name__ == '__main__':
    main()
