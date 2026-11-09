"""Owner-only native image construction, with private retained input/output evidence."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import uuid

from build_recovery_initramfs import read_credentials
from forge_boot_observation import capture, normal_boot
from forge_recovery_build_worker import MODULES, MAX_IMAGE, validate
from forge_target_journal import private_directory, write_record

BOOTSTRAP = '''import hashlib,json,sys,types
raw=sys.stdin.buffer.read(4*1024*1024+1)
if len(raw)>4*1024*1024: raise ValueError('Build request exceeds bound')
r=json.loads(raw)
for name in ('forge_boot_observation','forge_recovery_build_worker'):
    m=types.ModuleType(name); sys.modules[name]=m
    exec(compile(r['modules'][name],'<owner-native-build:'+name+'>','exec'),m.__dict__)
result=sys.modules['forge_recovery_build_worker'].perform(r)
from pathlib import Path
image=Path(result['image'])
out=sys.stdout.buffer
out.write((json.dumps(result)+'\\n').encode()); out.flush()
with image.open('rb') as source:
    remaining=result['size']
    while remaining:
        data=source.read(min(65536,remaining))
        if not data: raise ValueError('Native image truncated during transfer')
        out.write(data); remaining-=len(data)
    if source.read(1): raise ValueError('Native image grew during transfer')
out.flush()
'''


def argv(host, command):
    if not isinstance(host, str) or not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Invalid normal SSH host')
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ClearAllForwardings=yes', '-o', 'RequestTTY=no', host, command]


def discover(host):
    """Read-only owner discovery; confirm this identity before submitting a build."""
    command = argv(host, '/usr/bin/uname -r')
    before = capture(host)
    normal_boot(before, before['machine_id'])
    kernel = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}', kernel) or capture(host) != before:
        raise ValueError('Kernel or normal boot changed during discovery')
    return dict(host=host, boot=before, kernel=kernel)


def build(output, discovered, credentials):
    """One native build attempt. Does not publish, stage boot files, or reboot.

    On SSH loss, preserve the host journal and target's token-bound scratch;
    never submit another build under this output or infer that nothing happened.
    """
    discovered = copy.deepcopy(discovered)
    if set(discovered) != {'host', 'boot', 'kernel'}: raise ValueError('Unexpected discovery fields')
    command = argv(discovered['host'], 'sudo -n /usr/bin/python3 -I -S -c '+shlex.quote(BOOTSTRAP))
    frozen = read_credentials(credentials)
    sources = {name: Path(__file__).with_name(name+'.py').read_text() for name in MODULES}
    request = dict(schema=1, token=uuid.uuid4().hex, boot=discovered['boot'], kernel=discovered['kernel'],
                   modules=sources, credentials={name: base64.b64encode(data).decode() for name, data in frozen.items()})
    validate(request)
    payload = json.dumps(request).encode()
    if len(payload) > 4*1024*1024: raise ValueError('Build request exceeds transport bound')
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    fd = private_directory(output)
    try:
        pins = {name: hashlib.sha256(source.encode()).hexdigest() for name, source in sources.items()}
        write_record(fd, 'request.json', dict(discovered, token=request['token'], module_sha256=pins,
            credential_sha256={name: hashlib.sha256(data).hexdigest() for name, data in frozen.items()},
            target_directory='/var/tmp/uconsole-forge-build-'+request['token'],
            boot_files_written=False, publication_authorized=False, reboot_authorized=False))
        # Fixed owner code limits image size before transfer; retain stderr only in the private journal.
        wire_fd = os.open('wire.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        error_fd = os.open('stderr.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(wire_fd, 'wb') as wire, os.fdopen(error_fd, 'wb') as errors:
            reply = subprocess.run(command, input=payload, stdout=wire, stderr=errors, timeout=900)
            wire.flush(); os.fsync(wire.fileno())
        if reply.returncode: raise RuntimeError('Native build/transfer failed; retain both journals, do not retry')
        with (output/'wire.bin').open('rb') as wire:
            header = wire.readline(65537)
            if len(header) > 65536 or not header.endswith(b'\n'): raise ValueError('Invalid build reply header')
            result = json.loads(header)
            if (result.get('status') != 'built-not-published' or result.get('token') != request['token']
                    or result.get('boot') != discovered['boot'] or result.get('kernel') != discovered['kernel']
                    or result.get('module_sha256') != pins or result.get('contains_private_credentials') is not True
                    or result.get('target_scratch_created') is not True
                    or any(result.get(key) is not False for key in ('boot_files_written', 'publication_performed', 'reboot_performed'))
                    or type(result.get('size')) is not int or not 0 < result['size'] <= MAX_IMAGE
                    or result.get('image') != '/var/tmp/uconsole-forge-build-'+request['token']+'/build/recovery.img'):
                raise ValueError('Native build acknowledgement differs from owner request')
            image_fd = os.open('recovery.img', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            checksum, remaining = hashlib.sha256(), result['size']
            with os.fdopen(image_fd, 'wb') as image:
                while remaining:
                    data = wire.read(min(65536, remaining))
                    if not data: raise ValueError('Incomplete native image transfer')
                    checksum.update(data); image.write(data); remaining -= len(data)
                image.flush(); os.fsync(image.fileno())
            if wire.read(1) or checksum.hexdigest() != result.get('sha256'):
                raise ValueError('Native image bytes differ from acknowledgement')
        write_record(fd, 'native-build.json', result)
        summary = {key: result[key] for key in ('status', 'kernel', 'sha256', 'size', 'contains_private_credentials',
            'target_scratch_created', 'boot_files_written', 'publication_performed', 'reboot_performed')}
        summary.update(boot_qualified=False, root_write_authorized=False, automatic_retry_performed=False)
        write_record(fd, 'acceptance.json', summary)
        return summary
    except BaseException as exc:
        write_record(fd, 'failure.json', dict(error_type=type(exc).__name__, preserve_journals=True,
            target_scratch_may_exist=True, automatic_retry_performed=False, publication_performed=False, reboot_performed=False))
        raise
    finally:
        os.close(fd)
