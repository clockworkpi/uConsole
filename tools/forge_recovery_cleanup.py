"""Owner-confirmed reverse staging and exact private-image removal; keep FAT private."""
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from forge_backup_source import _create
from forge_boot_observation import capture, normal_boot, verify as verify_boot
from forge_recovery_bootplan import digest
from forge_recovery_journal import validate, history_checked, dispatch as image_dispatch, inspect_operation
from forge_recovery_stage_dispatch import PHASES, MODULES, bound_attempt, checked_response, key, pending, dispatch
from forge_recovery_stage_review import load
from forge_target_backup import capture as capture_files
from forge_target_files import equivalent
from forge_target_journal import private_directory, read_record, write_record, events


def phase_states(fd, reviewed, pin):
    pending(fd, reviewed, pin)
    names, completed = set(os.listdir(fd)), []
    # This complete-cycle cleanup requires all four original applications.
    # Partial staging remains available through the per-phase recovery panel.
    for phase in PHASES:
        attempt = bound_attempt(fd, reviewed, pin, phase, 'apply')
        checked_response(attempt, read_record(fd, key(phase, 'apply')+'-ack.json'), attempt['boot'])
    gap = False
    for phase in reversed(PHASES):
        stem = key(phase, 'restore')
        if stem+'.json' not in names:
            gap = True
            continue
        if gap: raise ValueError('Restore history is not in reverse staging order')
        attempt = bound_attempt(fd, reviewed, pin, phase, 'restore')
        if stem+'-ack.json' in names:
            checked_response(attempt, read_record(fd, stem+'-ack.json'), attempt['boot'])
        else:
            records = sorted(name for name in names if name.startswith(stem+'-reconciled-'))
            if not records: raise ValueError('Uncertain staging cleanup must be reconciled, never retried')
            saved = read_record(fd, records[-1])
            reply = checked_response(attempt, saved['response'], saved['boot'], reconcile=True)
            if reply['requires_new_boot'] or any(item['state'] not in ('before', 'unchanged') for item in reply['files']):
                raise ValueError('Staging restoration is not independently resolved')
        completed.append(phase)
    return completed


def inputs(staging, pin, boot_id):
    if not isinstance(boot_id, str) or not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id):
        raise ValueError('Exact owner-verified normal boot UUID required')
    staging = Path(staging).absolute()
    fd = private_directory(staging)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reviewed = load(staging, pin)
        if boot_id == reviewed['original_boot']['boot_id']:
            raise ValueError('Complete-cycle cleanup requires a verified newer normal boot')
        completed = phase_states(fd, reviewed, pin)
        before = read_record(fd, 'before.json')
    finally: os.close(fd)
    publication = Path(reviewed['request']['publication'])
    fd = private_directory(publication)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = validate(read_record(fd, 'plan.json'))
        history = events(fd)
        if plan != reviewed['request']['image_plan'] or digest(plan) != reviewed['request']['publication_sha256'] or not history:
            raise ValueError('Publication journal differs from sealed staging')
        state = history_checked(history, plan, digest(plan), fd)
    finally: os.close(fd)
    if state == 'restore' and len(completed) != len(PHASES):
        raise ValueError('Recovery image removed before verified staging restoration')
    return dict(staging=str(staging), staging_sha256=pin, boot_id=boot_id, host=plan['host'],
        machine_id=plan['machine_id'], original_boot=reviewed['original_boot'], before=before,
        publication=str(publication), publication_sha256=digest(plan), image_plan=plan,
        completed_phases=completed, image_removed=state == 'restore')


BOOTSTRAP = '''import json,sys,types
r=json.load(sys.stdin)
for name,source in r['modules']:
    m=types.ModuleType(name);sys.modules[name]=m
    exec(compile(source,'<forge-cleanup:'+name+'>','exec'),m.__dict__)
from forge_boot_observation import read_boot
from forge_target_files import parent_fd,snapshot,equivalent
from forge_recovery_ssh import perform
def guard():
    if read_boot()!=r['boot']: raise ValueError('Cleanup normal boot changed')
    for desired in r['before']['files']:
        with parent_fd(desired['path']) as (fd,name):
            if not equivalent(snapshot(fd,name,desired['path']),desired):
                raise ValueError('Original boot preimages differ; keep recovery image private')
    if read_boot()!=r['boot']: raise ValueError('Cleanup boot changed during file checks')
print(json.dumps(perform(r['plan'],r['direction'],r['nonce'],r['digest'],guard=guard)))
'''


def image_transport(frozen, boot, plan, direction, nonce, pin):
    if direction not in ('restore', 'inspect') or plan != frozen['image_plan'] or pin != frozen['publication_sha256']:
        raise ValueError('Cleanup may only inspect or remove the exact owned publication')
    base = Path(__file__).parent
    request = dict(plan=plan, direction=direction, nonce=nonce, digest=pin, boot=boot, before=frozen['before'],
                   modules=[(name, (base/(name+'.py')).read_text()) for name in MODULES])
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=10', frozen['host'], 'sudo -n python3 -c '+shlex.quote(BOOTSTRAP)],
        input=json.dumps(request), text=True, capture_output=True, timeout=180, check=True)
    return json.loads(result.stdout)


def cleanup(output, frozen):
    if inputs(frozen['staging'], frozen['staging_sha256'], frozen['boot_id']) != frozen:
        raise ValueError('Cleanup progress changed since owner review')
    output = Path(output).resolve()
    for path in (frozen['staging'], frozen['publication']):
        path = Path(path).resolve(strict=True)
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Cleanup output must not overlap sealed journals')
    # Separate from the per-phase journal locks: serialize the complete owner
    # sequence without bypassing those existing, independently guarded workers.
    owner = Path(frozen['staging'])/'cleanup-owner'
    owner.mkdir(mode=0o700, exist_ok=True)
    lock, out = private_directory(owner), None
    started = False
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if inputs(frozen['staging'], frozen['staging_sha256'], frozen['boot_id']) != frozen:
            raise ValueError('Cleanup progress changed before execution')
        out = _create(output)
        try:
            write_record(out, 'request.json', frozen)
            boot = normal_boot(capture(frozen['host']), frozen['machine_id'])
            verify_boot(boot, frozen['original_boot'], frozen['machine_id'])
            if boot['boot_id'] != frozen['boot_id']: raise ValueError('Owner-approved normal boot changed')
            write_record(out, 'normal-before.json', boot)
            for phase in reversed(PHASES):
                if phase in frozen['completed_phases']: continue
                def authorize(value):
                    if (value['stage'] != frozen['staging_sha256'] or value['phase'] != phase or
                            value['direction'] != 'restore' or value['boot_id'] != frozen['boot_id']):
                        raise ValueError('Cleanup phase or approved normal boot changed')
                    return value
                write_record(out, 'restore-'+phase+'-intent.json', dict(phase=phase, boot_id=frozen['boot_id']))
                started = True
                response = dispatch(frozen['staging'], frozen['staging_sha256'], phase, 'restore', authorize=authorize)
                write_record(out, 'restore-'+phase+'-ack.json', response)
            capture_files(frozen['host'], [item['path'] for item in frozen['before']['files']], output/'boot-preimages.json')
            files = read_record(out, 'boot-preimages.json')
            if (files['machine_id'] != frozen['machine_id'] or len(files['files']) != len(frozen['before']['files']) or
                    any(not equivalent(a, b) for a, b in zip(files['files'], frozen['before']['files']))):
                raise ValueError('Original boot preimages not restored; do not remove private image')
            def transport(plan, direction, nonce, pin): return image_transport(frozen, boot, plan, direction, nonce, pin)
            if not frozen['image_removed']:
                write_record(out, 'remove-image-intent.json', dict(plan_sha256=frozen['publication_sha256']))
                started = True
                removed = image_dispatch(frozen['publication'], 'restore', frozen['publication_sha256'], transport)
                write_record(out, 'remove-image-ack.json', removed)
            observed = inspect_operation(frozen['publication'], frozen['publication_sha256'], transport)
            write_record(out, 'image-after.json', observed)
            if (observed['boot_id'] != boot['boot_id'] or observed['policy'].get('valid') is not True or
                    observed['files'].get('parent_private') is not True or
                    observed['files'].get('destination', {}).get('state') != 'absent' or
                    observed['files'].get('scratch', {}).get('state') != 'absent'):
                raise ValueError('Private image absence and retained privacy are not verified')
            after = normal_boot(capture(frozen['host']), frozen['machine_id'])
            if after != boot: raise ValueError('Cleanup normal boot changed')
            write_record(out, 'normal-after.json', after)
            result = dict(status='cleaned-staging-and-owned-image', staging_sha256=frozen['staging_sha256'],
                publication_sha256=frozen['publication_sha256'], boot_id=boot['boot_id'],
                staged_preimages_verified=True, owned_image_absence_verified=True, private_mount_retained=True,
                public_permissions_authorized=False, reboot_performed=False, root_written=False,
                automatic_retry_performed=False)
            write_record(out, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(out, 'failure.json', dict(error_type=type(exc).__name__, target_changes_may_have_started=started,
                public_permissions_authorized=False, automatic_retry_performed=False, preserve_evidence=True))
            raise
    finally:
        if out is not None: os.close(out)
        os.close(lock)
