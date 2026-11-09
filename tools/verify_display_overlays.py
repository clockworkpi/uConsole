#!/usr/bin/env python3
"""Check compiled uConsole display overlays with Raspberry Pi dtmerge."""
import argparse
import json
from pathlib import Path
import subprocess

PROFILES = {
    'cm4': ('bcm2711-rpi-cm4.dtb', 'clockworkpi-uconsole.dtbo', 'vc4-kms-v3d-pi4.dtbo'),
    'cm5': ('bcm2712-rpi-cm5-cm4io.dtb', 'clockworkpi-uconsole-cm5.dtbo', 'vc4-kms-v3d-pi5.dtbo'),
}


def verify(dtmerge, dts_dir, output, model):
    base, panel, display = PROFILES[model]
    output.mkdir(parents=True, exist_ok=True)
    intermediate = output / f'{model}-uconsole.dtb'
    merged = output / f'{model}-display.dtb'
    subprocess.run([str(dtmerge), str(dts_dir / 'broadcom' / base),
                    str(intermediate), str(dts_dir / 'overlays' / panel)], check=True)
    subprocess.run([str(dtmerge), str(intermediate), str(merged),
                    str(dts_dir / 'overlays' / display), 'cma-384'], check=True)

    def get(node, prop, kind='s'):
        return subprocess.check_output(['fdtget', '-t', kind, str(merged), node, prop],
                                       text=True).strip()

    def symbol(name):
        return get('/__symbols__', name)

    expected = [
        (symbol('dsi1') + '/panel@0', 'compatible', 's', 'cw,cwu50'),
        (symbol('dsi1') + '/panel@0', 'rotation', 'i', '90'),
        (symbol('dsi1'), 'status', 's', 'okay'),
        (symbol('vc4'), 'status', 's', 'okay'),
        ('/battery@0', 'constant-charge-current-max-microamp', 'i', '2100000'),
    ]
    if model == 'cm4':
        expected.extend((symbol(bus), 'status', 's', 'okay') for bus in ('i2c1', 'spi4'))
    checked = {}
    for node, prop, kind, value in expected:
        actual = get(node, prop, kind)
        if actual != value:
            raise ValueError(f'{node}:{prop}: expected {value!r}, got {actual!r}')
        checked[f'{node}:{prop}'] = actual
    cma = get(symbol('cma'), 'size', 'x')
    size = int(''.join(f'{int(cell, 16):08x}' for cell in cma.split()), 16)
    if size != 384 * 1024 * 1024:
        raise ValueError(f'CMA size: expected 384 MiB, got {size} bytes')
    checked['cma:size_bytes'] = size
    evidence = {'model': model, 'base': base, 'overlays': [panel, display],
                'parameters': ['cma-384'], 'checked_properties': checked,
                'hardware_boot_verified': False}
    (output / f'{model}-overlay-check.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(f'{model}: combined overlays and {len(checked)} properties verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtmerge', type=Path, required=True)
    parser.add_argument('--dts-dir', type=Path, required=True,
                        help='kernel output arch/arm64/boot/dts directory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', choices=PROFILES, required=True)
    args = parser.parse_args()
    verify(args.dtmerge.resolve(), args.dts_dir.resolve(), args.output.resolve(), args.model)
