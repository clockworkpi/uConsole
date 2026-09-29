"""Owner-supplied native builder: private scratch only, never publication or boot."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from forge_boot_observation import normal_boot, read_boot

MODULES = ('build_recovery_initramfs', 'forge_recovery_runtime', 'forge_recovery_watchdog',
           'forge_ram_identity', 'forge_recovery_lease', 'forge_recovery_lease_socket',
           'forge_recovery_deadline', 'forge_recovery_lease_timer',
           'forge_recovery_lease_watchdog', 'forge_recovery_lease_launcher',
           'forge_boot_observation', 'forge_recovery_build_worker')
CREDENTIALS = ('wpa.conf', 'authorized_keys', 'ssh_host_ed25519_key')
SCRATCH_PARENT = Path('/var/tmp')
MAX_IMAGE = 128*1024*1024


def validate(request):
    if (not isinstance(request, dict) or set(request) != {'schema', 'token', 'boot', 'kernel', 'modules', 'credentials'}
            or type(request['schema']) is not int or request['schema'] != 1
            or not isinstance(request['token'], str) or not re.fullmatch('[0-9a-f]{32}', request['token'])
            or not isinstance(request['kernel'], str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}', request['kernel'])):
        raise ValueError('Invalid owner native-build request')
    normal_boot(request['boot'], request['boot']['machine_id'])
    if (not isinstance(request['modules'], dict) or set(request['modules']) != set(MODULES)
            or any(not isinstance(source, str) or not 0 < len(source.encode()) <= 512*1024
                   for source in request['modules'].values())
            or sum(len(source.encode()) for source in request['modules'].values()) > 2*1024*1024
            or not isinstance(request['credentials'], dict) or set(request['credentials']) != set(CREDENTIALS)):
        raise ValueError('Native-build resources differ from fixed allowlist')
    credentials = {}
    for name, encoded in request['credentials'].items():
        if not isinstance(encoded, str) or len(encoded) > 90000:
            raise ValueError('Credential exceeds build bounds')
        data = base64.b64decode(encoded, validate=True)
        if not 0 < len(data) <= 65536: raise ValueError('Invalid credential size')
        credentials[name] = data
    return credentials


def prerequisites(kernel):
    if os.geteuid() != 0 or os.uname().machine not in ('aarch64', 'arm64') or os.uname().release != kernel:
        raise ValueError('Build requires root on the selected native ARM64 kernel')
    if not Path('/lib/modules', kernel).is_dir(): raise ValueError('Matching native kernel modules are missing')
    for executable in ('/usr/sbin/mkinitramfs', '/usr/sbin/sshd', '/usr/sbin/wpa_supplicant',
                       '/usr/bin/python3', 'cc', 'openssl', 'modprobe', 'busybox', 'cpio', 'gzip'):
        if shutil.which(executable) is None: raise ValueError('Native build dependency missing: '+executable)
    if not stat.S_ISDIR(SCRATCH_PARENT.lstat().st_mode) or shutil.disk_usage(SCRATCH_PARENT).free < 1024**3:
        raise ValueError('Require a real scratch parent with at least 1 GiB free')


def record(path, value):
    with path.open('xb') as output:
        output.write((json.dumps(value, sort_keys=True, indent=2)+'\n').encode())
        output.flush()
        os.fsync(output.fileno())


def perform(request):
    credentials = validate(request)
    prerequisites(request['kernel'])
    if normal_boot(read_boot(), request['boot']['machine_id']) != request['boot']:
        raise ValueError('Native boot changed before build; nothing staged')
    directory = SCRATCH_PARENT/('uconsole-forge-build-'+request['token'])
    previous = os.umask(0o077)
    try:
        directory.mkdir(mode=0o700)  # Exclusive claim; uncertain builds cannot be replayed.
        try:
            pins = {name: hashlib.sha256(source.encode()).hexdigest() for name, source in request['modules'].items()}
            record(directory/'request.json', dict(schema=1, boot=request['boot'], kernel=request['kernel'],
                module_sha256=pins, credential_sha256={name: hashlib.sha256(data).hexdigest() for name, data in credentials.items()},
                publication_authorized=False, reboot_authorized=False))
            source_dir, secret_dir = directory/'tools', directory/'credentials'
            source_dir.mkdir(mode=0o700)
            secret_dir.mkdir(mode=0o700)
            for name, source in request['modules'].items():
                with (source_dir/(name+'.py')).open('xb') as output: output.write(source.encode())
            for name, data in credentials.items():
                with (secret_dir/name).open('xb') as output: output.write(data)
            code = ('import sys; sys.path.insert(0,'+repr(str(source_dir))+'); '
                    'from build_recovery_initramfs import build; build('+repr(str(directory/'build'))+','+
                    repr(str(secret_dir))+','+repr(request['kernel'])+')')
            with (directory/'builder.log').open('xb') as log:
                subprocess.run(['/usr/bin/python3', '-I', '-S', '-c', code], stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=780)
            manifest = json.loads((directory/'build/manifest.json').read_text())
            image = directory/'build/recovery.img'
            descriptor = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077 or not 0 < info.st_size <= MAX_IMAGE):
                    raise ValueError('Native image is not a private bounded file')
                checksum = hashlib.file_digest(source, 'sha256').hexdigest()
            if (manifest.get('kernel') != request['kernel'] or manifest.get('image') != str(image)
                    or manifest.get('sha256') != checksum or manifest.get('size') != info.st_size
                    or manifest.get('contains_private_credentials') is not True
                    or manifest.get('boot_qualified') is not False or manifest.get('deployment_performed') is not False):
                raise ValueError('Native build manifest differs from output')
            if normal_boot(read_boot(), request['boot']['machine_id']) != request['boot'] or os.uname().release != request['kernel']:
                raise ValueError('Native identity changed during build')
            result = dict(status='built-not-published', token=request['token'], boot=request['boot'],
                kernel=request['kernel'], sha256=checksum, size=info.st_size, image=str(image),
                module_sha256=pins, contains_private_credentials=True, target_scratch_created=True,
                boot_files_written=False, publication_performed=False, reboot_performed=False)
            record(directory/'acceptance.json', result)
            return result
        except BaseException as exc:
            record(directory/'failure.json', dict(error_type=type(exc).__name__, preserve_scratch=True,
                automatic_retry_performed=False, boot_files_written=False, reboot_performed=False))
            raise
    finally:
        os.umask(previous)
