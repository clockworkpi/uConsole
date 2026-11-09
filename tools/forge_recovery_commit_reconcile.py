"""Host-side fenced observation of an uncertain CONFIG commit, without retry."""
import fcntl
import os
from pathlib import Path
import re
import shlex
import uuid

from forge_recovery_bootcommit import validate
from forge_recovery_commit_protocol import exact
from forge_recovery_commit_worker import BOOTSTRAP, payload
from forge_recovery_commit_transport import exchange
from forge_recovery_commit_ledger import request as ledger_request
from forge_recovery_hash import capture
from forge_target_journal import private_directory, read_record, write_record
from forge_recovery_commit_inspect import observation_plan


def expected_receipt(plan, pin):
    return dict(status='boot-file-commit-verified',plan_sha256=pin,boot_id=plan['binding']['boot_id'],
        operation=plan['operation'],file=dict(status='applied',path='/boot/firmware/config.txt'),
        root_written=False,boot_unmounted=True,reboot_performed=False,physical_boot_qualified=False)


def check_fence(value, plan, pin, attempt, *, observed_boot_id=None):
    boot_id=plan['binding']['boot_id'] if observed_boot_id is None else observed_boot_id
    changed_boot=boot_id!=plan['binding']['boot_id']
    fields={'status','plan_sha256','boot_id','attempt','outcome','stale_boot_unmounted',
            'pending_boot_writes_may_have_flushed','root_written'}
    if (not isinstance(value,dict) or set(value)!=fields or value['status']!='fenced' or
            value['plan_sha256']!=pin or value['boot_id']!=boot_id or
            value['attempt']!=attempt or value['root_written'] is not False or
            type(value['stale_boot_unmounted']) is not bool or
            value['pending_boot_writes_may_have_flushed'] is not value['stale_boot_unmounted']):
        raise ValueError('Fenced cleanup receipt differs')
    outcome=value['outcome']
    allowed=('previous-boot-ended',) if changed_boot else ('fenced-not-started','incomplete','completed')
    if not isinstance(outcome,dict) or outcome.get('status') not in allowed:
        raise ValueError('Unknown target attempt outcome')
    expected=dict(status=outcome['status'],request=ledger_request(plan,pin,attempt))
    if outcome['status']=='completed': expected['result']=expected_receipt(plan,pin)
    if changed_boot:
        expected['previous_boot_id']=plan['binding']['boot_id']
        if value['stale_boot_unmounted']: raise ValueError('Previous boot cannot leave a current RAM mount')
    if not exact(outcome,expected): raise ValueError('Target fence identity or receipt differs')
    return value


def check_inspection(value, plan, pin, *, observed_boot_id=None):
    boot_id=plan['binding']['boot_id'] if observed_boot_id is None else observed_boot_id
    fields={'status','files','image','stage','plan_sha256','boot_id','prefix','read_only',
            'boot_unmounted','root_written','deployment_authorized'}
    if (not isinstance(value,dict) or set(value)!=fields or value['status'] not in ('before','after','conflict') or
            value['plan_sha256']!=pin or value['boot_id']!=boot_id or
            any(value[key] is not True for key in ('read_only','boot_unmounted')) or
            any(value[key] is not False for key in ('root_written','deployment_authorized')) or
            value['image'] not in ('matched','conflict') or value['stage'] not in ('absent','matches-desired','conflict')):
        raise ValueError('Read-only commit inspection receipt differs')
    states=value['files']
    if (not isinstance(states,list) or len(states)!=len(plan['guarded_paths']) or
            any(not isinstance(item,dict) or set(item)!={'path','state'} or item['path']!=path or
                item['state'] not in ('before','after','unchanged','conflict') for item,path in zip(states,plan['guarded_paths']))):
        raise ValueError('Commit file observation set differs')
    selector=next(item for item in states if item['path']=='/boot/firmware/config.txt')
    if selector['state']=='unchanged': raise ValueError('Distinct selectors cannot both match')
    expected='conflict'
    if value['image']=='matched' and value['stage']=='absent':
        for candidate in ('before','after'):
            if all(item['state'] in ('unchanged',candidate) for item in states): expected=candidate
    if expected!=value['status']: raise ValueError('Commit inspection classification differs')
    prefix=value['prefix']
    if (not isinstance(prefix,dict) or set(prefix)!={'offset','bytes','sha256'} or
            type(prefix['offset']) is not int or prefix['offset']!=0 or type(prefix['bytes']) is not int or
            prefix['bytes']!=plan['binding']['extent']['offset_bytes'] or
            not isinstance(prefix['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',prefix['sha256'])):
        raise ValueError('Commit inspection prefix binding differs')
    return value


class Reply:
    may_renew=False  # Cleanup may encounter an old RW mount; inspection mounts RO.
    def __init__(self, query, check):
        self.query,self.check=query,check
        self.phase,self.result='observing',None
    def consume(self,message):
        if (self.phase!='observing' or not isinstance(message,dict) or
                set(message)!={'type','attempt','result'} or message['type']!='complete' or message['attempt']!=self.query):
            raise ValueError('Unexpected commit reconciliation reply')
        self.result=self.check(message['result'])
        self.phase='complete'
        return 'complete'


def reconcile(probe, directory, pin, lease, *, observed_boot_id=None):
    """Reboots require an explicitly pinned new boot UUID and its new lease.

    Existing uncertain acceptance is preserved. New evidence cannot authorize
    deployment, root restoration, normal boot release or an automatic retry.
    """
    directory=Path(directory).absolute()
    journal=private_directory(directory)
    out_fd=None
    try:
        fcntl.flock(journal,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=validate(read_record(journal,'plan.json'),pin)
        original_binding=plan['binding']
        b=observation_plan(plan,pin,observed_boot_id)['binding']
        if (any(getattr(probe,key)!=b[key] for key in ('nonce','kernel','serial','mode')) or
                lease.probe is not probe or lease.boot_id!=b['boot_id'] or
                (b.get('lease_owner') is not None and b['lease_owner']!=lease.owner) or
                (probe.mode=='physical' and b.get('lease_owner')!=lease.owner)):
            raise ValueError('Reconciliation probe/lease differs')
        attempt_fd=os.open('commit-attempt',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=journal)
        try: original=read_record(attempt_fd,'dispatch.json')
        finally: os.close(attempt_fd)
        attempt=original.get('attempt')
        if (not isinstance(attempt,str) or not re.fullmatch('[0-9a-f]{32}',attempt) or
                not exact(original,dict(plan_sha256=pin,attempt=attempt,binding=original_binding,
                                        lease_owner=lease.owner,worker_protocol=2))):
            raise ValueError('Original commit attempt binding differs')
        query=uuid.uuid4().hex
        name='reconcile-'+query
        os.mkdir(name,mode=0o700,dir_fd=journal)
        os.fsync(journal)
        out_fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=journal)
        output=directory/name
        result=dict(status='incomplete',query=query,attempt=attempt,plan_sha256=pin,
                    deployment_authorized=False,root_written=False,normal_boot_release_authorized=False)
        try:
            write_record(out_fd,'request.json',dict(plan_sha256=pin,query=query,attempt=attempt,
                original_binding=original_binding,observation_binding=b))
            request=payload(plan,pin,lease.owner,attempt)
            source=Path(__file__).with_name('forge_recovery_commit_inspect.py').read_text()
            request['modules'].append(('forge_recovery_commit_inspect',source))
            request['query']=query
            request['observed_boot_id']=b['boot_id']
            bootstrap=BOOTSTRAP.replace('from forge_recovery_commit_worker import run',
                                        'from forge_recovery_commit_inspect import run')
            def call(action,check,timeout):
                request['action']=action
                log_fd=os.open(action+'-protocol.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=out_fd)
                with os.fdopen(log_fd,'wb') as transcript:
                    error_fd=os.open(action+'-stderr.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=out_fd)
                    with os.fdopen(error_fd,'wb') as errors:
                        reply=exchange(probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(bootstrap)),request,
                            Reply(query,check),prepared=None,unmounted=None,heartbeat=lambda:None,
                            transcript=transcript,errors=errors,timeout=timeout,idle_timeout=timeout)
                write_record(out_fd,action+'.json',reply)
                return reply
            fenced=call('fence',lambda value:check_fence(value,plan,pin,attempt,observed_boot_id=b['boot_id']),30)
            # Only after fencing/cleanup is a strict RAM-only renewal possible.
            request['lease']=lease.renew()
            inspected=call('inspect',lambda value:check_inspection(value,plan,pin,observed_boot_id=b['boot_id']),150)
            observed=capture(probe,output/'hashes',b['cid'],b['disk_id'],boot_id=b['boot_id'],
                             device=b['device'],lease=lease)
            hashes=observed['digests']
            if (hashes['root']!=plan['root_guard'] or hashes['suffix']!=plan['suffix_guard'] or
                    hashes['prefix']!=inspected['prefix']):
                raise ValueError('Independent reconciliation hashes differ from protected root or file observation')
            state=inspected['status']
            outcome=fenced['outcome']['status']
            if (outcome=='completed' and state!='after') or (outcome=='fenced-not-started' and state!='before'):
                state='conflict'
            result.update(status='reconciled-'+state,fence=fenced,inspection=inspected,
                          digests=hashes,evidence_directory=str(output))
        except BaseException as exc:
            result['error']=type(exc).__name__+': '+str(exc)
            raise
        finally:
            write_record(out_fd,'acceptance.json',result)
        return result
    finally:
        if out_fd is not None: os.close(out_fd)
        os.close(journal)
