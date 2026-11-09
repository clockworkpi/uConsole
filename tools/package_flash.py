#!/usr/bin/env python3
"""Package freshly built firmware and the native reset helper for distribution."""
import argparse
import os
from pathlib import Path
import struct
import tarfile

try:
    from .firmware_manifest import FQBN, verify
except ImportError:
    from firmware_manifest import FQBN, verify

ROOT = Path(__file__).resolve().parents[1]


def helper_target(header):
    """Return the release OS/architecture encoded in a native helper."""
    if len(header) >= 20 and header[:4] == b'\x7fELF' and header[5] == 1:
        machine = struct.unpack_from('<H', header, 18)[0]
        architectures = {62: 'x86_64', 183: 'aarch64', 40: 'armhf', 243: 'riscv64'}
        if machine not in architectures:
            raise ValueError(f'unsupported ELF machine: {machine}')
        return 'linux', architectures[machine]

    # 64-bit, little-endian Mach-O. GitHub's standard macOS runner is arm64,
    # but recognizing x86_64 keeps local Intel packaging deterministic too.
    if len(header) >= 8 and header[:4] == b'\xcf\xfa\xed\xfe':
        cpu_type = struct.unpack_from('<I', header, 4)[0]
        architectures = {0x01000007: 'x86_64', 0x0100000C: 'arm64'}
        if cpu_type not in architectures:
            raise ValueError(f'unsupported Mach-O CPU type: {cpu_type}')
        return 'macos', architectures[cpu_type]

    raise ValueError('reset helper must be a 64-bit little-endian ELF or Mach-O binary')


def package(build_dir):
    verify(build_dir, os.environ.get('FQBN', FQBN))
    helper = build_dir / 'upload-reset.elf'
    firmware = build_dir / 'firmware/uconsole_keyboard.ino.bin'
    header = helper.read_bytes()[:20]
    operating_system, architecture = helper_target(header)
    if firmware.stat().st_size == 0:
        raise ValueError('firmware is empty')
    bundled = ROOT / 'Bin/uconsole_keyboard_flash'
    files = [(bundled / name, name) for name in ('flash.sh', 'maple_upload', 'README.md')]
    files += [(firmware, 'uconsole_keyboard.ino.bin')]
    files += [(build_dir / 'firmware/provenance.json', 'provenance.json')]
    files += [(helper, 'upload-reset')]
    output = build_dir / f'uconsole_keyboard_flash-{operating_system}-{architecture}.tar.gz'
    # Write atomically so an interrupted package step cannot replace a good bundle.
    temporary = output.with_suffix('.tmp')
    try:
        with tarfile.open(temporary, 'w:gz') as archive:
            for source, name in files:
                archive.add(source, arcname=f'uconsole_keyboard_flash/{name}')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_dir', type=Path)
    args = parser.parse_args()
    print(package(args.build_dir))
