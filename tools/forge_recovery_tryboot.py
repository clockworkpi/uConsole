"""One owner-confirmed tryboot from acknowledged staging, then local enrollment."""
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from forge_boot_observation import capture, normal_boot
from forge_recovery_stage_dispatch import PHASES, MODULES, bound_attempt, checked_response, key, pending
from forge_recovery_stage_review import load
from forge_recovery_stage_prepare import published
from forge_session_enrollment import inputs as enrollment_inputs, prepare as enroll
from forge_target_journal import private_directory, read_record, write_record


REBOOT = '''import json,os,subprocess,sys,types
r=json.load(sys.stdin)
for name,source in r['modules']:
    m=types.ModuleType(name);sys.modules[name]=m
    exec(compile(source,'<owner-tryboot:'+name+'>','exec'),m.__dict__)
from forge_boot_observation import read_boot
from forge_target_ssh import target_lock
from forge_target_files import parent_fd,snapshot,equivalent
from forge_recovery_ssh import target_policy
from forge_recovery_image import inspect
with target_lock('/run/lock/uconsole-forge-target.lock'):
    if os.geteuid()!=0 or read_boot()!=r['boot']: raise ValueError('Normal boot changed')
    p=r['image'];target_policy(p)
    image=inspect(p['destination'],p['sha256'],p['size'],p['stage_token'])
    if (not image['parent_private'] or image['destination']['state']!='matching-bytes'
            or image['scratch']['state']!='absent'): raise ValueError('Private recovery image differs')
    for desired in r['files']:
        with parent_fd(desired['path']) as (fd,name):
            if not equivalent(snapshot(fd,name,desired['path']),desired):
                raise ValueError('Staged boot file changed')
    if read_boot()!=r['boot']: raise ValueError('Normal boot changed during checks')
    subprocess.run(['/sbin/reboot','0 tryboot'],check=True,timeout=15)
'''


def reviewed(value):
    if value.boot_id is not None or value.tryboot != 1:
        raise ValueError('Tryboot requires fresh UUID discovery and one-shot selection')
    rebound = enrollment_inputs(value.staging, value.staging_pin, value.probe.host,
        value.probe.key, value.probe.known_hosts, value.probe.kernel, value.probe.serial, None, 1)
    if rebound != value:
        raise ValueError('Staging enrollment inputs changed')
    draft = load(value.staging, value.staging_pin)
    fd = private_directory(value.staging)
    try:
        if os.path.lexists(value.staging/'tryboot-attempt.json'):
            raise ValueError('Tryboot already attempted; inspect/enroll, never reboot again')
        pending(fd, draft, value.staging_pin)
        names = os.listdir(fd)
        for phase in PHASES:
            if any(name.startswith(key(phase, 'restore')) for name in names):
                raise ValueError('Staging restoration has started; new preparation required')
            attempt = bound_attempt(fd, draft, value.staging_pin, phase, 'apply')
            if attempt['boot'] != draft['original_boot']:
                raise ValueError('Staging belongs to another normal boot')
            checked_response(attempt, read_record(fd, key(phase, 'apply')+'-ack.json'), draft['original_boot'])
        staged = read_record(fd, 'planned-staged.json')
    finally:
        os.close(fd)
    return draft, staged


def reboot_transport(draft, staged):
    source = Path(__file__).resolve().parent
    payload = dict(boot=draft['original_boot'], image=draft['request']['image_plan'], files=staged['files'],
                   modules=[(name, (source/(name+'.py')).read_text()) for name in MODULES])
    try:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', draft['request']['image_plan']['host'],
            'sudo -n /usr/bin/python3 -I -S -c '+shlex.quote(REBOOT)],
            input=json.dumps(payload), text=True, capture_output=True, timeout=35)
        return dict(returncode=result.returncode, recovery_boot_verified=False)
    except subprocess.TimeoutExpired:
        return dict(transport_timeout=True, recovery_boot_verified=False, do_not_repeat=True)


def boot_and_enroll(output, value, *, timeout=600):
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('Invalid recovery observation bound')
    draft, staged = reviewed(value)
    output = Path(output).absolute()
    stage_fd, out = private_directory(value.staging), None
    try:
        fcntl.flock(stage_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if reviewed(value) != (draft, staged):
            raise ValueError('Staging changed before tryboot submission')
        output.mkdir(mode=0o700)
        out = private_directory(output)
        write_record(stage_fd, 'tryboot-attempt.json', dict(output=str(output), staging_sha256=value.staging_pin,
            normal_boot_id=draft['original_boot']['boot_id'], automatic_retry_authorized=False))
        dispatched = False
        try:
            write_record(out, 'request.json', dict(staging_sha256=value.staging_pin,
                normal_boot_id=draft['original_boot']['boot_id'], root_write_authorized=False))
            image = draft['request']['image_plan']
            if normal_boot(capture(image['host']), value.machine_id) != draft['original_boot']:
                raise ValueError('Normal boot changed since staging')
            if published(draft['request'])['boot_id'] != draft['original_boot']['boot_id']:
                raise ValueError('Publication inspection saw another boot')
            write_record(out, 'reboot-intent.json', dict(boot_id=draft['original_boot']['boot_id'], one_shot=True))
            dispatched = True
            write_record(out, 'reboot-transport.json', reboot_transport(draft, staged))
            deadline, attempt = time.monotonic()+timeout, 0
            while True:
                attempt += 1
                try:
                    observed = value.probe.inspect()
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                    write_record(out, f'waiting-{attempt:04d}.json', dict(error_type=type(exc).__name__))
                    if time.monotonic() >= deadline:
                        raise RuntimeError('New recovery boot not verified; do not repeat tryboot') from exc
                    time.sleep(min(2, max(0, deadline-time.monotonic())))
                    continue
                break
            boot_id = observed['verification']['boot_id']
            if boot_id == draft['original_boot']['boot_id']:
                raise ValueError('Recovery must be a new boot')
            write_record(out, 'observed-ram.json', observed)
            enrolled = enroll(output/'enrollment', replace(value, boot_id=boot_id))
            result = dict(status='recovery-boot-enrolled-not-leased', boot_id=boot_id,
                staging_sha256=value.staging_pin, enrollment=enrolled,
                reboot_request_dispatched=True, lease_acquired=False, root_write_authorized=False,
                recovery_fallback_qualified=False, automatic_retry_performed=False)
            write_record(out, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(out, 'failure.json', dict(error_type=type(exc).__name__, reboot_may_have_started=dispatched,
                root_write_authorized=False, automatic_retry_performed=False, preserve_journals=True))
            raise
    finally:
        if out is not None:
            os.close(out)
        os.close(stage_fd)
