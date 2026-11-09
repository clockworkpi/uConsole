"""Pinned default guest download and explicit, least-privilege package plans."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

IMAGE_NAME = 'uConsole_CM4_v3.1_64bit.img.bz2'
IMAGE_BYTES = 2212739229
IMAGE_SHA256 = 'ef95242cdb0125e8ed08157400a26d4665acddd083481ec2314acd6e073b74ab'
IMAGE_URL = ('https://drive.usercontent.google.com/download?'
             'id=17OFCPCBwddaqQ957R4vbY-3KzugGtaU-&export=download&confirm=t')
LINUX_RUNTIME = ('python3-tk', 'qemu-system-arm', 'qemu-utils', 'dosfstools', 'e2fsprogs')
LINUX_BUILD = ('build-essential', 'python3-venv', 'ninja-build', 'pkg-config', 'patch',
               'libglib2.0-dev', 'libpixman-1-dev', 'libslirp-dev', 'libgtk-3-dev', 'libcap-ng-dev')
MAC_RUNTIME = ('qemu', 'dosfstools', 'e2fsprogs')
MAC_BUILD = ('ninja', 'pkgconf', 'glib', 'pixman', 'libslirp')


def package_plan(build=False):
    """Read installed package state; never install or request privilege here."""
    if sys.platform.startswith('linux') and Path('/usr/bin/dpkg-query').is_file():
        wanted = LINUX_RUNTIME + (LINUX_BUILD if build else ())
        result = subprocess.run(['/usr/bin/dpkg-query', '-W',
                                 '-f=${binary:Package}\t${db:Status-Status}\n', *wanted],
                                capture_output=True, text=True, timeout=20)
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr or 'Could not inspect installed Debian packages')
        installed = {row.split('\t')[0].split(':')[0] for row in result.stdout.splitlines()
                     if row.endswith('\tinstalled')}
        return dict(manager='apt', missing=[name for name in wanted if name not in installed],
                    privileged=True)
    if sys.platform == 'darwin':
        brew = shutil.which('brew')
        if not brew:
            raise RuntimeError('Homebrew is not installed. Install Homebrew from https://brew.sh, then retry setup.')
        wanted = MAC_RUNTIME + (MAC_BUILD if build else ())
        result = subprocess.run([brew, 'list', '--formula', '--versions'],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr or 'Could not inspect Homebrew packages')
        installed = {row.split()[0] for row in result.stdout.splitlines() if row.split()}
        return dict(manager='brew', missing=[name for name in wanted if name not in installed],
                    privileged=False)
    raise RuntimeError('Automatic package installation supports Debian/Ubuntu and macOS with Homebrew.')


def install_command(plan):
    """Only fixed package names and package-manager operations can be elevated.

    Do not execute a writable Workbench script as root, accept shell fragments,
    collect passwords, remove packages, or upgrade already installed packages.
    """
    names = plan['missing']
    if not isinstance(names, list) or not names:
        raise ValueError('No packages selected')
    if plan['manager'] == 'apt':
        if any(name not in LINUX_RUNTIME + LINUX_BUILD for name in names):
            raise ValueError('Unexpected prerequisite package')
        if not Path('/usr/bin/pkexec').is_file():
            raise RuntimeError('The system authentication helper pkexec is missing; install polkit to enable UI authorization.')
        return ['/usr/bin/pkexec', '/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive',
                '/usr/bin/apt-get', '--assume-yes', '--no-remove', '--no-upgrade',
                '-o', 'Dpkg::Options::=--force-confold', 'install', *names]
    if plan['manager'] == 'brew':
        if any(name not in MAC_RUNTIME + MAC_BUILD for name in names):
            raise ValueError('Unexpected prerequisite package')
        brew = shutil.which('brew')
        if not brew:
            raise RuntimeError('Homebrew is missing')
        return [brew, 'install', *names]
    raise ValueError('Unsupported package manager')


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not newurl.startswith('https://'):
            raise ValueError('Refusing a non-HTTPS image redirect')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(cache, *, minimum_free=20 * 1024**3, allow_download=True):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive, partial = cache / IMAGE_NAME, cache / (IMAGE_NAME + '.partial')
    lock = cache / '.default-image.lock'
    for path in (archive, partial, lock):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('Unsafe download cache entry: ' + str(path))
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another default-image download is running; wait for it to finish') from None
        if archive.exists():
            print('Verifying the cached default image…', flush=True)
            if archive.stat().st_size == IMAGE_BYTES and digest(archive) == IMAGE_SHA256:
                print('Verified cached image: ' + str(archive), flush=True)
                return archive
            rejected = archive.with_name(archive.name + '.rejected-' + uuid.uuid4().hex)
            if not allow_download:
                raise ValueError('Cached image failed verification; approve a replacement download in Setup')
            archive.rename(rejected)
            print('Invalid cache preserved at ' + str(rejected), flush=True)
        if not allow_download:
            raise ValueError('No verified cached image; approve the download in Setup')
        if shutil.disk_usage(cache).free < minimum_free:
            raise ValueError('At least 20 GiB free is required for default image download and preparation')
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > IMAGE_BYTES:
            raise ValueError('Partial image is larger than the pinned download; preserve it and choose a fresh cache')
        if offset < IMAGE_BYTES:
            request = urllib.request.Request(IMAGE_URL, headers={'User-Agent': 'uConsole-Workbench',
                **({'Range': f'bytes={offset}-'} if offset else {})})
            opener = urllib.request.build_opener(HTTPSRedirects())
            with opener.open(request, timeout=30) as response:
                if not response.geturl().startswith('https://'):
                    raise ValueError('Image response is not HTTPS')
                if response.status == 206:
                    expected = f'bytes {offset}-{IMAGE_BYTES - 1}/{IMAGE_BYTES}'
                    if response.headers.get('Content-Range') != expected:
                        raise ValueError('Unexpected download range; partial file preserved')
                elif response.status == 200:
                    if offset:
                        print('Server declined resume; restarting the partial download.', flush=True)
                    offset = 0
                else:
                    raise ValueError('Unexpected download status: ' + str(response.status))
                length = response.headers.get('Content-Length')
                if length is not None and int(length) != IMAGE_BYTES - offset:
                    raise ValueError('Image response size differs from the pinned release')
                if 'text/html' in response.headers.get('Content-Type', ''):
                    raise ValueError('The mirror returned a web page instead of the image; retry later')
                with partial.open('ab' if offset else 'wb') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    last = 0
                    while chunk := response.read(1024 * 1024):
                        if offset + len(chunk) > IMAGE_BYTES:
                            raise ValueError('Download exceeds the pinned image size')
                        stream.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last >= 1:
                            print(f'Download: {offset / IMAGE_BYTES:.1%} ({offset:,} / {IMAGE_BYTES:,} bytes)', flush=True)
                            last = time.monotonic()
                    stream.flush()
                    os.fsync(stream.fileno())
        print('Verifying downloaded image SHA-256…', flush=True)
        if partial.stat().st_size != IMAGE_BYTES:
            raise ValueError('Incomplete download retained; Start again to resume')
        if digest(partial) != IMAGE_SHA256:
            rejected = partial.with_name(partial.name + '.rejected-' + uuid.uuid4().hex)
            partial.rename(rejected)
            raise ValueError('Image checksum mismatch; rejected bytes preserved at ' + str(rejected))
        partial.rename(archive)
        print('Verified default image: ' + str(archive), flush=True)
        return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    plan = commands.add_parser('packages')
    plan.add_argument('--build', action='store_true')
    fetch = commands.add_parser('download')
    fetch.add_argument('--cache', type=Path, required=True)
    fetch.add_argument('--cache-only', action='store_true')
    args = parser.parse_args()
    if args.action == 'packages':
        print(json.dumps(package_plan(args.build)), flush=True)
    else:
        download(args.cache, allow_download=not args.cache_only)
