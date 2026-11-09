"""Managed QEMU identity and relocatable package payload."""
import hashlib
import json
from pathlib import Path
import shutil


def fingerprint(root):
    from build_emulator_qemu import PATCHES, MODEL_SOURCES
    names = ['tools/build_emulator_qemu.py'] + [
        'Code/patch/qemu/' + name for name in PATCHES + [name for name, _ in MODEL_SOURCES]]
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((Path(root) / name).read_bytes())
    return digest.hexdigest()


def record(build, root):
    from build_emulator_qemu import VERSION
    data = dict(schema=1, version=VERSION, fingerprint=fingerprint(root))
    (build / 'workbench-emulator.json').write_text(json.dumps(data, indent=2) + '\n')


def compatible(directory, root):
    try:
        data = json.loads((directory / 'workbench-emulator.json').read_text())
        return (data['schema'] == 1 and data['fingerprint'] == fingerprint(root)
                and all((directory / name).is_file() for name in ('qemu-system-aarch64', 'qemu-img')))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def selected(root, build_root):
    for directory in (build_root / 'emulator/qemu-build', root / 'emulator/bin'):
        if compatible(directory, root):
            return directory
    return None


def bundle(build_root, root, destination):
    from build_emulator_qemu import VERSION
    build = build_root / 'emulator/qemu-build'
    if not compatible(build, root):
        raise ValueError('Build the matching patched QEMU before packaging')
    binaries = destination / 'emulator/bin'
    binaries.mkdir(parents=True)
    for name in ('qemu-system-aarch64', 'qemu-img', 'workbench-emulator.json'):
        shutil.copy2(build / name, binaries / name)
    # QEMU resolves ../share/qemu relative to its installed executable.
    source = (build / 'qemu-source').resolve(strict=True)
    data = destination / 'emulator/share/qemu'
    data.mkdir(parents=True)
    for path in (source / 'pc-bios').iterdir():
        if path.is_file() and path.suffix in ('.bin', '.rom', '.dtb', '.efi', '.img'):
            shutil.copy2(path, data / path.name)
    shutil.copytree(source / 'pc-bios/keymaps', data / 'keymaps')
    sources = destination / 'emulator/source'
    sources.mkdir()
    shutil.copy2(build_root / 'emulator' / f'qemu-{VERSION}.tar.xz', sources)
    for name in ('COPYING', 'COPYING.LIB'):
        shutil.copy2(source / name, sources)


if __name__ == '__main__':
    import sys
    if sys.argv[1] == '--check':
        import subprocess
        directory, backend = Path(sys.argv[2]), sys.argv[3]
        for name in ('qemu-system-aarch64', 'qemu-img'):
            subprocess.run([str(directory / name), '--version'], check=True)
        displays = subprocess.check_output([str(directory / 'qemu-system-aarch64'), '-display', 'help'], text=True)
        print(displays)
        if backend != 'none' and backend not in displays.split():
            raise SystemExit(f'The selected emulator lacks the {backend} display backend. '
                             'Install its development dependencies and rebuild, or select an available backend.')
    else:
        bundle(*(Path(value).resolve() for value in sys.argv[1:]))
