"""Fenced boot-commit inspection; never reapply or reverse a CONFIG write.

Cleanup may unmount the exact stale RW boot mount left by a dead worker, which
can flush that worker's pending FAT writes. No file is repaired or deleted.
The subsequent inspection mounts boot read-only and binds its observation to
a stable prefix hash for independent host whole-card verification.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess

from forge_ram_identity import READER, verify
from forge_recovery_layout import reader, root_extent
from forge_recovery_bootcommit import validate, ensure_lock_directory, check_mount, mounted_boot, commit_timer
from forge_recovery_claim import claim_root, range_digest
from forge_recovery_image import verified as verify_image
from forge_target_files import snapshot, equivalent
from forge_target_ssh import target_lock
import forge_recovery_commit_ledger as ledger
from forge_recovery_commit_liveness import budget


def observe(source):
    output=io.StringIO()
    with contextlib.redirect_stdout(output): exec(source,{})
    return json.loads(output.getvalue())


def observation_plan(plan, pin, boot_id=None):
    """An observation-only view; it cannot pass the original commit plan pin."""
    validate(plan,pin)
    if boot_id is None: boot_id=plan['binding']['boot_id']
    if not isinstance(boot_id,str) or not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot_id):
        raise ValueError('Explicit observed boot UUID required')
    view=copy.deepcopy(plan)
    view['binding']['boot_id']=boot_id
    return view


def identity(plan, *, stale_mount=False):
    binding=plan['binding']
    value=observe(READER)
    point='/run/forge-boot-commit-'+plan['stage_token']
    rows=value['mountinfo'].splitlines()
    matches=[row for row in rows if len(row.split(' - ')[0].split())>=5 and
             row.split(' - ')[0].split()[4]==point]
    if matches:
        if not stale_mount or len(matches)!=1:
            raise ValueError('Unqualified or ambiguous stale boot mount')
        check_mount(point,binding['device']+'p1')
        # Remove exactly the separately verified, plan-bound boot mount only.
        # The normal verifier still rejects every other persistent filesystem.
        value=dict(value,mountinfo='\n'.join(row for row in rows if row!=matches[0]))
    checked=verify(value,binding['nonce'],binding['kernel'],binding['serial'],mode=binding['mode'])
    if checked['boot_id']!=binding['boot_id']: raise ValueError('Commit inspection boot changed')
    return bool(matches)


def layout(plan, *, stale_mount=False):
    identity(plan,stale_mount=stale_mount)
    b=plan['binding']
    result=root_extent(observe(reader(b['device'])),b['cid'],b['disk_id'],device=b['device'])
    if result!=b['extent']: raise ValueError('Commit inspection storage changed')
    return result


def cleanup(plan, pin, attempt, *, observed_boot_id=None):
    original=plan
    plan=observation_plan(original,pin,observed_boot_id)
    prior_boot=original['binding']['boot_id']
    changed_boot=plan['binding']['boot_id']!=prior_boot
    layout(plan,stale_mount=not changed_boot)  # Verify before creating any RAM ledger/lock.
    if changed_boot:
        # Never manufacture a current-boot receipt for an expired RAM ledger.
        # The old worker rejects this new UUID before opening its old ledger.
        outcome=dict(status='previous-boot-ended',request=ledger.request(original,pin,attempt),previous_boot_id=prior_boot)
    else:
        directory=ledger.provision(plan['binding']['boot_id'],pin)
        # A live old worker holds this ledger lock; fencing fails without killing it.
        outcome=ledger.fence(directory,attempt,ledger.request(plan,pin,attempt))
    ensure_lock_directory()
    removed=False
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        with claim_root(plan['binding']['device'],plan['binding']['extent'],
                        lambda:layout(plan,stale_mount=not changed_boot)):
            if identity(plan,stale_mount=not changed_boot):
                point=Path('/run/forge-boot-commit-'+plan['stage_token'])
                check_mount(point,plan['binding']['device']+'p1')
                subprocess.run(['umount',str(point)],check=True,timeout=10)
                if os.path.ismount(point): raise RuntimeError('Stale boot mount persists')
                point.rmdir()  # Empty RAM mountpoint only, never boot contents.
                removed=True
            layout(plan)
    return dict(status='fenced',plan_sha256=pin,boot_id=plan['binding']['boot_id'],
                attempt=attempt,outcome=outcome,stale_boot_unmounted=removed,
                pending_boot_writes_may_have_flushed=removed,root_written=False)


def inspect_files(plan, point):
    fd=os.open(point,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    states=[]
    try:
        before={record['path']:record for record in plan['before']['files']}
        after={record['path']:record for record in plan['after']['files']}
        for path in plan['guarded_paths']:
            try:
                current=snapshot(fd,Path(path).name,path)
                old,new=equivalent(current,before[path]),equivalent(current,after[path])
                state='unchanged' if old and new else 'before' if old else 'after' if new else 'conflict'
            except (OSError,ValueError,RuntimeError):
                state='conflict'
            states.append(dict(path=path,state=state))
        image=plan['image_dependency']
        image_state='conflict'
        try:
            image_fd=os.open(Path(image['path']).name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_NONBLOCK,dir_fd=fd)
            try: verify_image(image_fd,image['sha256'],image['size'])
            finally: os.close(image_fd)
            image_state='matched'
        except (OSError,ValueError,RuntimeError):
            pass
        stage='conflict'
        try:
            current=snapshot(fd,'.uconsole-forge-'+plan['stage_token'],'/boot/firmware/config.txt')
            stage=('absent' if current['kind']=='absent' else
                   'matches-desired' if equivalent(current,after['/boot/firmware/config.txt']) else 'conflict')
        except (OSError,ValueError,RuntimeError):
            pass
        status='conflict'
        if image_state=='matched' and stage=='absent':
            for candidate in ('before','after'):
                if all(item['state'] in ('unchanged',candidate) for item in states): status=candidate
        return dict(status=status,files=states,image=image_state,stage=stage)
    finally:
        os.close(fd)


def inspect(plan, pin, owner, accepted, token, *, observed_boot_id=None):
    plan=observation_plan(plan,pin,observed_boot_id)
    layout(plan)
    if budget(plan['binding'],owner,accepted)<180:
        raise ValueError('Insufficient verified read-only inspection budget')
    ensure_lock_directory()
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        with claim_root(plan['binding']['device'],plan['binding']['extent'],lambda:layout(plan)) as claim:
            with commit_timer(120):
                prefix=plan['binding']['extent']['offset_bytes']
                before=range_digest(claim.card_fd,0,prefix)
                with mounted_boot(plan['binding']['device']+'p1',token,read_only=True) as point:
                    result=inspect_files(plan,point)
                layout(plan)
                after=range_digest(claim.card_fd,0,prefix)
                if before!=after: raise ValueError('Read-only inspection changed or raced boot storage')
    return dict(result,plan_sha256=pin,boot_id=plan['binding']['boot_id'],
                prefix=dict(offset=0,bytes=prefix,sha256=after),read_only=True,
                boot_unmounted=True,root_written=False,deployment_authorized=False)


def run(request):
    def disconnected(signum,frame):
        # Allow bounded unmount cleanup to finish after the first hangup.
        signal.signal(signal.SIGHUP,signal.SIG_IGN)
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise RuntimeError('Commit inspection disconnected; retain incomplete evidence')
    signal.signal(signal.SIGHUP,disconnected)
    signal.signal(signal.SIGTERM,disconnected)
    if request['action']=='fence':
        result=cleanup(request['plan'],request['pin'],request['attempt'],observed_boot_id=request.get('observed_boot_id'))
    elif request['action']=='inspect':
        result=inspect(request['plan'],request['pin'],request['owner'],request['lease'],request['query'],
                       observed_boot_id=request.get('observed_boot_id'))
    else:
        raise ValueError('Unknown commit inspection action')
    print(json.dumps(dict(type='complete',attempt=request['query'],result=result)),flush=True)
