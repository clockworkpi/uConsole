#!/usr/bin/env python3
"""Prove observational counters with real QEMU/qtest, not a Linux boot claim."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace

from forge_runtime import Runtime
from test_emulator_keyboard import USBTest
from test_emulator_watchdog import connect
from uconsole_emulator import executable
from workbench_schematic_live import Sampler
from workbench_schematic_trace import Recording, Playback


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = Runtime(SimpleNamespace(workspace=output))
    with tempfile.TemporaryDirectory(prefix='uc-observe-') as directory, (output / 'qemu.log').open('wb') as log:
        runtime.directory = SimpleNamespace(name=directory)
        process = runtime.process = subprocess.Popen([
            executable('qemu-system-aarch64'), '-M', 'raspi4b', '-accel', 'qtest',
            '-name', runtime.identity, '-display', 'none', '-serial', 'none', '-monitor', 'none',
            '-device', 'usb-uconsole-keyboard,id=deck,port=1',
            '-qtest', f'unix:{directory}/test,server=on,wait=off',
            '-qmp', f'unix:{directory}/qmp,server=on,wait=off'], stdout=log, stderr=log)
        try:
            with connect(Path(directory) / 'test', process) as sock:
                usb = USBTest(sock.makefile('rwb', buffering=0))
                usb.reset()
                usb.control(0x0009, 1)
                keyboard = '/machine/peripheral/deck'
                display = '/machine/soc/peripherals/fb'

                def count(path, prop):
                    result = runtime.observe('qom-get', {'path': path, 'property': prop})
                    assert type(result) is int and result >= 0
                    return result

                before = count(keyboard, 'delivered-reports')
                runtime.control('qom-set', {'path': keyboard, 'property': 'inject-report',
                                            'value': '020000040000000000'})
                assert count(keyboard, 'delivered-reports') == before, 'Injection is not delivery'
                sampler = Sampler()
                trace = Recording(runtime.identity, time.monotonic())
                for event in sampler.sample(runtime):
                    trace.append(event)
                status, data = usb.packet(endpoint=1, kind=3, incoming=True, size=64)
                assert status & 1 and data.hex() == '020000040000000000'
                assert count(keyboard, 'delivered-reports') == before + 1
                usb.packet(endpoint=1, kind=3, incoming=True, size=64)
                assert count(keyboard, 'delivered-reports') == before + 1, 'NAK is not delivery'
                for event in sampler.sample(runtime):
                    trace.append(event)
                runtime.control('screendump', {'filename': str(output / 'initial.ppm')})
                frame_before = count(display, 'display-updates')
                base = runtime.observe('qom-get', {'path': display, 'property': 'vcram-base'})
                usb.command(f'writel {base + 0x100000:#x} 0xff00ff00')
                runtime.control('screendump', {'filename': str(output / 'changed.ppm')})
                frame_after = count(display, 'display-updates')
                assert frame_after > frame_before, (frame_before, frame_after)
                assert (output / 'initial.ppm').read_bytes() != (output / 'changed.ppm').read_bytes()
                for event in sampler.sample(runtime):
                    trace.append(event)
                for path, prop in ((keyboard, 'delivered-reports'), (display, 'display-updates')):
                    try:
                        runtime.control('qom-set', {'path': path, 'property': prop, 'value': 999})
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Diagnostic counter was writable')
                assert any(e['component'] == 'keyboard' and e['state'] == 'active' for e in trace.data['events'])
                assert any(e['component'] == 'display' and e['state'] == 'active' for e in trace.data['events'])
                trace.save(output / 'observations.json')
                playback = Playback.load(output / 'observations.json')
                assert playback.seek(playback.duration).identity == runtime.identity
                receipt = dict(status='passed', coverage='Real QEMU qtest USB DMA and framebuffer; no Linux guest boot',
                               identity=runtime.identity, keyboard_delivered=before + 1,
                               framebuffer_before=frame_before, framebuffer_after=frame_after,
                               counters_read_only=True, trace_replayed=True)
                (output / 'acceptance.json').write_text(json.dumps(receipt, indent=2) + '\n')
                print(json.dumps(receipt))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', required=True, type=Path)
    run(cli.parse_args().output)
