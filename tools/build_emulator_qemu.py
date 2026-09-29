#!/usr/bin/env python3
"""Build an isolated, pinned QEMU on a POSIX host; never replace system QEMU."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid

VERSION = '10.2.4'
SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
SOURCE_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_ROOT = Path(os.environ.get('UCONSOLE_ROOT', SOURCE_ROOT)).resolve()
BUILD_ROOT = Path(os.environ.get('UCONSOLE_BUILD_DIR', RESOURCE_ROOT / 'build')).expanduser().resolve()
ROOT = BUILD_ROOT / 'emulator'
PATCHES = ['dummy-cpu-darwin-wakeup.patch', 'bcm2835-watchdog-timer.patch', 'raspi4-upper-memory.patch',
           'uconsole-axp221-pmic.patch', 'axp2xx-poweroff.patch', 'axp2xx-i2c-migration.patch',
           'arm-gic-monitor-access.patch', 'dwc2-remote-wakeup.patch', 'dwc2-frame-clock.patch',
           'dwc2-detached-device.patch',
           'bcm2838-gpio-interrupts.patch', 'bcm2835-i2c-status-irq.patch',
           'bcm2835-i2c-nack-completion.patch',
           'bcm2835-firmware-gpio.patch', 'uconsole-keyboard.patch', 'usb-audio-drain.patch',
           'wav-backend-lifetime.patch', 'forge-audio-capture.patch', 'uconsole-adc101c.patch',
           'adc101c-reference-profile.patch', 'forge-modem.patch', 'usb-net-composite.patch',
           'forge-observation-counters.patch']
MODEL_SOURCES = [('adc101c-core.h', 'hw/adc/adc101c-core.h'),
                 ('adc101c.c', 'hw/adc/adc101c.c'),
                 ('forge-modem.c', 'hw/usb/forge-modem.c'),
                 ('usb-net-internal.h', 'hw/usb/usb-net-internal.h')]
CONFIGURE = ['--target-list=aarch64-softmmu', '--disable-docs', '--disable-werror',
             '--disable-guest-agent', '--enable-slirp', '--enable-vnc', '--disable-debug-info']


def generation_key(patches):
    """Identify frozen inputs, including this builder's configuration recipe."""
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(SHA256.encode())
    digest.update(json.dumps(CONFIGURE).encode())
    for name, data in patches:
        digest.update(json.dumps([name, hashlib.sha256(data).hexdigest()]).encode())
    for name in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS', 'PKG_CONFIG_PATH'):
        digest.update(json.dumps([name, os.environ.get(name)]).encode())
    return digest.hexdigest()


def publish_build(root, build):
    """Switch the public path only after validation; preserve legacy builds."""
    public = root / 'qemu-build'
    temporary = root / ('.qemu-publish-' + uuid.uuid4().hex)
    legacy = None
    temporary.symlink_to(build.resolve(), target_is_directory=True)
    try:
        if public.exists() and not public.is_symlink():
            if not public.is_dir():
                raise ValueError('QEMU build path is not a directory')
            legacy = root / ('qemu-build.legacy-' + uuid.uuid4().hex)
            public.rename(legacy)
        try:
            os.replace(temporary, public)
        except BaseException:
            if legacy is not None:
                legacy.rename(public)
            raise
    finally:
        temporary.unlink(missing_ok=True)


def source_filter(member, destination):
    # This pinned upstream symlink is used only when building EDK2's Unix
    # firmware emulator, not our QEMU targets. Never permit arbitrary host links.
    omitted = f'qemu-{VERSION}/roms/edk2/EmulatorPkg/Unix/Host/X11IncludeHack'
    if member.name == omitted and member.issym() and member.linkname == '/opt/X11/include':
        return None
    if not (member.name == f'qemu-{VERSION}' or member.name.startswith(f'qemu-{VERSION}/')):
        raise tarfile.FilterError('Unexpected QEMU source archive root: ' + member.name)
    return tarfile.data_filter(member, destination)


def extract_source(archive, root):
    """Publish the source directory only after safe extraction succeeds."""
    root = Path(root)
    source = root / f'qemu-{VERSION}'
    if source.exists():
        return source
    with tempfile.TemporaryDirectory(prefix='.qemu-extract-', dir=root) as temporary:
        with tarfile.open(archive) as tar:
            tar.extractall(temporary, filter=source_filter)
        staged = Path(temporary) / source.name
        if not (staged / 'configure').is_file() or not (staged / 'meson.build').is_file():
            raise ValueError('Incomplete QEMU source archive')
        # rename refuses to replace a populated source tree from a peer build.
        staged.rename(source)
    return source


def patched_paths(patches, models):
    paths = {name for name, _ in models}
    for _, data in patches:
        for line in data.decode().splitlines():
            if line.startswith(('--- a/', '+++ b/')):
                paths.add(line[6:].split('\t', 1)[0])
    for name in paths:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or str(path) != name:
            raise ValueError('Unsafe patched source path: '+name)
    return sorted(paths)


def source_hashes(source, paths):
    result = {}
    for name in paths:
        path = source/name
        checked = source
        for part in ('', *Path(name).parts):
            checked = checked/part
            if checked.is_symlink(): raise ValueError('Patched source path is a symlink: '+name)
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def prepare_source(archive, generation, patches, models):
    """Apply the complete stack once, atomically publish it, then verify outputs.

    Earlier patches cannot be reverse-checked individually against the final
    tree: later patches may legitimately replace their lines and context.
    Models can also be patch inputs, so verify their final bytes, not raw inputs.
    """
    source = generation/f'qemu-{VERSION}'
    inputs = [[name, hashlib.sha256(data).hexdigest()] for name, data in patches+models]
    paths = patched_paths(patches, models)
    marker = '.uconsole-prepared-source.json'
    if source.exists():
        if source.is_symlink() or (source/marker).is_symlink():
            raise ValueError('Unsafe cached source preparation')
        record = json.loads((source/marker).read_text())
        if record != dict(inputs=inputs, files=source_hashes(source, paths)):
            raise ValueError('Cached patched source differs from its completed preparation')
        return source
    with tempfile.TemporaryDirectory(prefix='.qemu-prepare-', dir=generation) as temporary:
        prepared = extract_source(archive, Path(temporary))
        for name, data in models:
            target = prepared/name
            source_hashes(prepared, [name])  # Refuse links before writing.
            target.write_bytes(data)
        for index, (_, data) in enumerate(patches):
            patch = Path(temporary)/f'patch-{index:04d}'
            patch.write_bytes(data)
            subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(patch)], cwd=prepared, check=True)
        record = dict(inputs=inputs, files=source_hashes(prepared, paths))
        with (prepared/marker).open('x') as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        prepared.rename(source)
    return source


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jobs', type=int, default=4)
    args = p.parse_args()
    if args.jobs < 1:
        p.error('--jobs must be positive')
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / '.qemu-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        build_qemu(args.jobs)


def build_qemu(jobs):
    archive = ROOT / f'qemu-{VERSION}.tar.xz'
    if not archive.exists():
        temporary = archive.with_suffix('.partial')
        urllib.request.urlretrieve(f'https://download.qemu.org/{archive.name}', temporary)
        temporary.rename(archive)
    with archive.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != SHA256:
        raise SystemExit(f'QEMU archive checksum mismatch: {actual}')
    patches = [(name, (RESOURCE_ROOT / 'Code/patch/qemu' / name).read_bytes()) for name in PATCHES]
    models = [(destination, (RESOURCE_ROOT / 'Code/patch/qemu' / name).read_bytes())
              for name, destination in MODEL_SOURCES]
    generation = ROOT / 'qemu-cache' / generation_key(patches + models)
    generation.mkdir(parents=True, exist_ok=True)
    source = prepare_source(archive, generation, patches, models)
    build = generation / 'build'
    build.mkdir(exist_ok=True)
    source_link = build / 'qemu-source'
    if not source_link.is_symlink():
        source_link.symlink_to(source, target_is_directory=True)
    if not (build / 'build.ninja').exists():
        subprocess.run([str(source / 'configure'), *CONFIGURE],
                       cwd=build, check=True)
    subprocess.run(['ninja', f'-j{jobs}', 'qemu-system-aarch64', 'qemu-img'], cwd=build, check=True)
    subprocess.run([str(build / 'qemu-system-aarch64'), '--version'], check=True)
    subprocess.run([str(build / 'qemu-img'), '--version'], check=True)
    from workbench_emulator import record
    record(build, RESOURCE_ROOT)
    publish_build(ROOT, build)


if __name__ == '__main__':
    main()
