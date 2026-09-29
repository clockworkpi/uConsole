"""Record and verify keyboard build provenance before reusing firmware artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from .forge_workspace import WorkspaceLock
else:
    from forge_workspace import WorkspaceLock

ROOT = Path(__file__).resolve().parents[1]
FQBN = 'stm32duino:STM32F1:genericSTM32F103R:device_variant=STM32F103RB,upload_method=DFUUploadMethod,cpu_speed=speed_48mhz,opt=osstd'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs(root):
    source = root / 'Code/uconsole_keyboard'
    paths = [path for path in source.rglob('*') if path.is_file()]
    paths += [root / 'Makefile', root / 'tools/firmware_manifest.py']
    return {str(path.relative_to(root)): digest(path) for path in sorted(paths)}


def record(build, fqbn, builder, root=ROOT, expected_inputs=None):
    firmware = build / 'firmware/uconsole_keyboard.ino.bin'
    if not firmware.is_file() or not firmware.stat().st_size:
        raise ValueError('Cannot record provenance for missing/empty firmware')
    if not builder:
        raise ValueError('Builder identity is required')
    current = inputs(root)
    if expected_inputs is not None and current != expected_inputs:
        raise ValueError('Firmware inputs changed during compilation; rebuild required')
    manifest = {'schema': 1, 'fqbn': fqbn, 'builder': builder,
                'inputs': current, 'firmware_sha256': digest(firmware)}
    target = build / 'firmware/provenance.json'
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(target)
    return manifest


def builder_identity(arduino_cli):
    data = {name: json.loads(subprocess.check_output(
                [arduino_cli, *arguments], text=True, timeout=30))
            for name, arguments in (('cli', ['version', '--format', 'json']),
                                    ('cores', ['core', 'list', '--format', 'json']))}
    # The CLI includes the full available-release catalog, whose ordering can
    # vary. Only installed core identities describe this compiler invocation.
    cores = []
    platforms = data['cores'].get('platforms') if isinstance(data['cores'], dict) else None
    if not isinstance(data['cli'], dict) or not isinstance(platforms, list):
        raise ValueError('Arduino CLI did not report structured installed-core identities')
    for platform in platforms:
        if not isinstance(platform, dict) or any(
                not isinstance(platform.get(key), str) or not platform[key]
                for key in ('id', 'installed_version')):
            raise ValueError('Arduino CLI reported an incomplete installed-core identity')
        cores.append({'id': platform['id'], 'installed_version': platform['installed_version'],
                      'manually_installed': platform.get('manually_installed', False)})
    return {'cli': data['cli'], 'cores': sorted(cores, key=lambda item: item['id'])}


def build_firmware(build, fqbn, arduino_cli, root=ROOT):
    build = build.resolve()
    build.mkdir(parents=True, exist_ok=True)
    with WorkspaceLock(build):
        before = inputs(root)
        builder = builder_identity(arduino_cli)
        # Never reuse a previous success record after a failed/interrupted build.
        (build / 'firmware/provenance.json').unlink(missing_ok=True)
        subprocess.run([arduino_cli, 'compile', '--fqbn', fqbn, '--output-dir',
                        str(build / 'firmware'), str(root / 'Code/uconsole_keyboard')],
                       cwd=root, check=True)
        if builder_identity(arduino_cli) != builder:
            raise ValueError('Firmware toolchain identity changed during compilation; rebuild required')
        return record(build, fqbn, builder, root, expected_inputs=before)


def verify(build, fqbn=FQBN, root=ROOT):
    target = build / 'firmware/provenance.json'
    manifest = json.loads(target.read_text())
    if manifest.get('schema') != 1 or not manifest.get('builder'):
        raise ValueError('Missing or invalid firmware build provenance')
    if manifest.get('fqbn') != fqbn:
        raise ValueError('Firmware FQBN does not match requested build')
    if manifest.get('inputs') != inputs(root):
        raise ValueError('Firmware source/build recipe changed; rebuild required')
    if manifest.get('firmware_sha256') != digest(build / 'firmware/uconsole_keyboard.ino.bin'):
        raise ValueError('Firmware bytes do not match their build provenance')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'record', 'check'])
    parser.add_argument('build', type=Path)
    parser.add_argument('--fqbn', default=os.environ.get('FQBN', FQBN))
    parser.add_argument('--arduino-cli', default='arduino-cli')
    args = parser.parse_args()
    try:
        if args.action == 'build':
            build_firmware(args.build, args.fqbn, args.arduino_cli)
        elif args.action == 'record':
            record(args.build, args.fqbn, builder_identity(args.arduino_cli))
        else:
            verify(args.build, args.fqbn)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Firmware provenance: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
