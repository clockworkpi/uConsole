"""Check DWC2 frame counters against the negotiated port's virtual period."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from test_emulator_watchdog import connect
from uconsole_emulator import executable


def probe(usb_version=2):
    with tempfile.TemporaryDirectory(prefix='uc-usb-frame-') as directory:
        root = Path(directory)
        with (root / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b', '-accel', 'qtest',
                '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-device', f'usb-kbd,port=1,usb_version={usb_version}',
                '-qtest', f'unix:{root}/test,server=on,wait=off'], stdout=log, stderr=log)
            try:
                with connect(root / 'test', process) as sock:
                    stream = sock.makefile('rwb', buffering=0)
                    def test(command):
                        stream.write((command + '\n').encode())
                        while True:
                            line = stream.readline().decode().strip()
                            if line.startswith('IRQ '):
                                continue
                            if not line.startswith('OK'):
                                raise ValueError('qtest failed: ' + line)
                            return line.split()[1:]
                    test('writel 0xfe980440 0x1100')
                    test('writel 0xfe980440 0x1000')
                    speed = (int(test('readl 0xfe980440')[0], 16) >> 17) & 3
                    if speed not in (0, 1, 2):
                        raise ValueError('Invalid negotiated root-port speed')
                    period = 125000 if speed == 0 else 1000000
                    # HFNUM has a reset sentinel; sample after the first SOF.
                    test(f'clock_step {period}')
                    read = lambda: int(test('readl 0xfe980408')[0], 16) & 0x3fff
                    samples = [read()]
                    for _ in range(10):
                        test(f'clock_step {period}')
                        samples.append(read())
                    deltas = [(b - a) & 0x3fff for a, b in zip(samples, samples[1:])]
                    before = read()
                    test(f'clock_step {period * (16384 + 7)}')
                    wrapped = read()
                    wrap_delta = (wrapped - before) & 0x3fff
                    test(f'clock_step {period - 1}')
                    partial = read()
                    test('clock_step 1')
                    boundary_delta = (read() - partial) & 0x3fff
                    passed = (deltas == [1] * 10 and wrap_delta == 7 and
                              partial == wrapped and boundary_delta == 1)
                    return {'status': 'passed' if passed else 'failed',
                            'samples': samples, 'deltas': deltas, 'step_ns': period,
                            'wrap_delta': wrap_delta, 'partial_frame_unchanged': partial == wrapped,
                            'boundary_delta': boundary_delta,
                            'root_port_speed': ('high', 'full', 'low')[speed],
                            'scope': 'USB root-port frame counter, not isochronous packet fidelity'}
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path)
    args = cli.parse_args()
    cases = [probe(version) for version in (1, 2)]
    result = {'status': 'passed' if all(case['status'] == 'passed' for case in cases) else 'failed',
              'cases': cases}
    if args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
    print(result)
    raise SystemExit(result['status'] != 'passed')
