"""Disposable-QEMU qualification of the production root-restore stream.

The fixture substitutes physical plan compilation and identity selection only.
No production dispatch accepts these emulated plans. Claims, boot inspection,
leases, framing, streaming, writes, readback and RAM ledgers remain real.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import uuid

from forge_recovery_backup import backup
from forge_recovery_bootplan import digest
from forge_recovery_commit_protocol import exact
from forge_recovery_hash import capture
from forge_recovery_restore_bootstrap import BOOTSTRAP, sources, start
from forge_recovery_restore_dispatch import Events
from forge_recovery_restore_exchange import exchange
from forge_recovery_restore_ledger import checked_result, request as ledger_request
from forge_recovery_restore_plan import INPUT_NAMES
from forge_recovery_restore_process import run as run_process
from forge_recovery_restore_protocol import header
from forge_recovery_restore_source import prepare, stream
from forge_target_journal import private_directory, read_record, write_record


WORKER = r'''
import contextlib,copy,hashlib,io,json,os,signal
from forge_ram_identity import READER,verify
from forge_recovery_layout import reader,root_extent
from forge_recovery_bootcommit import ensure_lock_directory,validate as validate_hold
from forge_recovery_claim import range_digest
from forge_recovery_restore_claim import claim_restore_root
from forge_recovery_restore_plan import INPUT_NAMES
from forge_recovery_restore_source import validate as validate_source
from forge_recovery_deploy_plan import INPUT_NAMES as DEPLOY_INPUTS
from forge_recovery_derivative import validate as validate_derivative
from forge_recovery_derivative_health import check_health
from forge_recovery_operation_contract import source_kind
from forge_recovery_source_contract import DERIVATIVE
from forge_recovery_restore_protocol import write_header
from forge_recovery_commit_liveness import budget
from forge_recovery_bootplan import digest
from forge_target_ssh import target_lock
import forge_recovery_restore_executor as executor
import forge_recovery_restore_worker as worker
import forge_recovery_restore_ledger as ledger

def run(request,channel):
    request=copy.deepcopy(request)
    phase=request.pop('fixture_action')
    accepted=request.pop('fixture_lease')
    plan=request['plan']
    b=plan['binding']
    if (phase not in ('seed','seed-two','restore','restore-lost-completion','restore-interrupted','fence') or plan.get('qualification_scope')!='disposable-emulator-only' or
            b['mode']!='emulated' or b['serial'] is not None or b['device']!='/dev/mmcblk1' or
            not 0<b['extent']['disk_bytes']<=128*1024*1024 or digest(plan)!=request['pin']):
        raise ValueError('Restore fixture requires a pinned small disposable emulator card')
    def observe(source):
        output=io.StringIO()
        with contextlib.redirect_stdout(output): exec(source,{})
        return json.loads(output.getvalue())
    def check():
        identity=verify(observe(READER),b['nonce'],b['kernel'],None,mode='emulated')
        if identity['boot_id']!=b['boot_id']: raise ValueError('Disposable restore boot changed')
        extent=root_extent(observe(reader(b['device'])),b['cid'],b['disk_id'],device=b['device'])
        if extent!=b['extent']: raise ValueError('Disposable restore layout changed')
        return extent
    check()
    if phase=='fence':
        if accepted is not None or channel.read(1): raise ValueError('Unexpected fixture fence input')
        ensure_lock_directory()
        with target_lock('/run/lock/uconsole-forge-target.lock'):
            check()
            outcome=ledger.fence(ledger.provision(b['boot_id']),plan,request['pin'],request['attempt'])
            check()
        write_header(channel,dict(type='fixture-fence',plan_sha256=request['pin'],
            attempt=request['attempt'],boot_id=b['boot_id'],outcome=outcome))
        channel.flush()
        return
    if phase in ('seed','seed-two'):
        if channel.read(1): raise ValueError('Trailing fixture seed input')
        def approve(pin,boot):
            if (pin,boot)!=(request['pin'],b['boot_id']): raise ValueError('Fixture seed approval differs')
            if not 180<=budget(b,b['lease_owner'],accepted)<=300: raise ValueError('Fixture seed lease expired')
            return dict(restore=pin,boot_id=boot)
        ensure_lock_directory()
        with target_lock('/run/lock/uconsole-forge-target.lock'):
            guards=dict(root=plan['root_before'],prefix=plan['prefix_guard'],suffix=plan['suffix_guard'])
            with claim_restore_root(b['device'],b['extent'],guards,pin=request['pin'],
                    boot_id=b['boot_id'],check=check,authorize=approve) as claim:
                offsets=(0,4194304) if phase=='seed-two' else (0,)
                if b['extent']['length_bytes']<offsets[-1]+512: raise ValueError('Fixture root too short')
                for offset in offsets:
                    original=os.pread(claim.root_fd,512,offset)
                    if len(original)!=512: raise ValueError('Short fixture seed sector')
                    changed=bytes(value^255 for value in original)
                    if os.pwrite(claim.root_fd,changed,offset)!=512: raise OSError('Short fixture seed write')
                    os.fsync(claim.root_fd)
                    if os.pread(claim.root_fd,512,offset)!=changed: raise ValueError('Fixture seed readback differs')
                root=dict(plan['root_before'],sha256=range_digest(claim.root_fd,0,b['extent']['length_bytes']))
        write_header(channel,dict(status='seeded-disposable-root',root=root,root_written=True,
                                  physical_qualified=False))
        channel.flush()
        return
    if accepted is not None: raise ValueError('Restore fixture requires interactive owner renewal')
    inputs=request['inputs']
    derived=source_kind(plan)==DERIVATIVE
    names=DEPLOY_INPUTS if derived else INPUT_NAMES
    if set(inputs)!=set(names): raise ValueError('Missing fixture evidence')
    validate_hold(inputs['hold_plan'],inputs['hold_pin'])
    manifest=inputs['derivative' if derived else 'source_manifest']
    if derived:
        validate_derivative(manifest,plan['source_manifest_sha256'])
        check_health(inputs['health'],inputs['health_pin'],manifest,inputs['derivative_pin'])
        if (not inputs['health']['root_filesystem_consistency_qualified'] or
                inputs['health_pin']!=plan['source_health_sha256'] or
                inputs['derivative_pin']!=plan['source_manifest_sha256'] or
                manifest['original_manifest_sha256']!=plan['rollback_manifest_sha256'] or
                manifest['original_root']!=plan['root_before'] or
                manifest['original_root']!=plan['rollback_root']):
            raise ValueError('Disposable derivative health or rollback differs')
    else:
        validate_source(manifest,plan['source_manifest_sha256'])
    if (inputs['hold_plan']['binding']['mode']!='emulated' or
            inputs['hold_plan']['binding']['extent']!=b['extent'] or
            manifest['root']!=plan['root_after'] or
            inputs['hold_inspection']['status']!='after'):
        raise ValueError('Disposable fixture source or hold differs')
    def compile_fixture(*args):
        if args!=tuple(inputs[name] for name in names):
            raise ValueError('Disposable restore evidence changed')
        return copy.deepcopy(plan)
    def checker(binding):
        if binding!=b: raise ValueError('Disposable restore binding changed')
        return check
    # Trusted test-only substitution inside this process. No production module
    # receives an emulation flag or changes its physical validation contract.
    import forge_recovery_operation_contract as operation_contract
    if derived: operation_contract.compile_deploy=compile_fixture
    else: operation_contract.compile_restore=compile_fixture
    executor.checker=checker
    if phase=='restore-lost-completion':
        original_header=worker.write_header
        def lose_completion(channel,message):
            if message.get('type')=='complete':
                raise ConnectionError('Injected lost restore completion after durable receipt')
            return original_header(channel,message)
        worker.write_header=lose_completion
    if phase=='restore-interrupted':
        original_ack=worker.Control.acknowledge
        def interrupted_ack(self,message):
            if message.get('index')==0:
                # RootChunkWriter has fsynced and read back the first chunk,
                # but the host has not received its acknowledgement yet.
                os.write(2,b'Injected SIGKILL after first durable restore chunk\n')
                os.kill(os.getpid(),signal.SIGKILL)
            return original_ack(self,message)
        worker.Control.acknowledge=interrupted_ack
    worker.run(request,channel)
'''


def checked(binding):
    if (not isinstance(binding,dict) or binding.get('mode')!='emulated' or
            binding.get('serial') is not None or binding.get('device')!='/dev/mmcblk1' or
            type(binding.get('extent',{}).get('disk_bytes')) is not int or
            not 0<binding['extent']['disk_bytes']<=128*1024*1024):
        raise ValueError('Restore stream probe requires a small disposable emulator card')


def packet(request):
    checked(request['plan']['binding'])
    modules=sources()
    modules['forge_restore_stream_fixture']=WORKER
    value=dict(schema=1,modules=modules,request=request)
    data=json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
    return data,hashlib.sha256(data).hexdigest()


def check_completed_fence(value,plan,pin,attempt):
    if not isinstance(value,dict) or not isinstance(value.get('outcome'),dict):
        raise ValueError('Missing lost-completion fence')
    receipt=checked_result(value['outcome'].get('result'),plan,pin)
    expected=dict(type='fixture-fence',plan_sha256=pin,attempt=attempt,boot_id=plan['binding']['boot_id'],
        outcome=dict(status='completed',request=ledger_request(plan,pin,attempt),result=receipt,
            boot_id=plan['binding']['boot_id'],requires_new_recovery_boot=False,
            root_write_authorized=False,normal_boot_release_authorized=False))
    if not exact(value,expected): raise ValueError('Lost-completion fence differs')
    return receipt


def check_incomplete_fence(value,plan,pin,attempt):
    expected=dict(type='fixture-fence',plan_sha256=pin,attempt=attempt,boot_id=plan['binding']['boot_id'],
        outcome=dict(status='incomplete',request=ledger_request(plan,pin,attempt),
            boot_id=plan['binding']['boot_id'],requires_new_recovery_boot=True,
            root_write_authorized=False,normal_boot_release_authorized=False))
    if not exact(value,expected): raise ValueError('Interrupted restore fence differs')


def expected_partial(backup_directory,manifest,pin):
    if len(manifest['chunks'])<2 or manifest['chunks'][1]['bytes']<512:
        raise ValueError('Interruption fixture requires two source chunks')
    hashed=hashlib.sha256()
    def consume(chunk,data):
        if chunk['offset']==manifest['chunks'][1]['offset']:
            data=bytes(value^255 for value in data[:512])+data[512:]
        hashed.update(data)
    stream(backup_directory,manifest,pin,consume)
    return dict(manifest['root'],sha256=hashed.hexdigest())


def run(probe,directory,binding,hold_plan,hold_pin,inspection,lease,*,lost_completion=False,reboot=None):
    if type(lost_completion) is not bool: raise ValueError('Explicit lost-completion fixture switch required')
    if reboot is not None and (not callable(reboot) or lost_completion):
        raise ValueError('Interrupted restore needs a distinct trusted reboot callback')
    checked(binding)
    if (probe.mode!='emulated' or lease.probe is not probe or lease.boot_id!=binding['boot_id'] or
            binding.get('lease_owner')!=lease.owner):
        raise ValueError('Disposable restore lease differs')
    directory=Path(directory)
    directory.mkdir(mode=0o700)
    fd=private_directory(directory)
    result=dict(status='incomplete',qualification_scope='disposable-emulator-only',
                physical_restore_qualified=False,normal_boot_release_authorized=False)
    try:
        backup(probe,directory/'backup',binding['cid'],binding['disk_id'],boot_id=binding['boot_id'],
               device=binding['device'],lease=lease,whole_card=True)
        original=capture(probe,directory/'before',binding['cid'],binding['disk_id'],
                         boot_id=binding['boot_id'],device=binding['device'],lease=lease)
        prepare(directory/'backup',directory/'source',original['digests']['root'])
        source_fd=private_directory(directory/'source')
        try: manifest=read_record(source_fd,'manifest.json')
        finally: os.close(source_fd)
        source_pin=digest(manifest)
        partial=expected_partial(directory/'backup',manifest,source_pin) if reboot is not None else None
        plan=dict(schema=1,kind='guarded-backup-root-restore-plan',operation='restore-backup-root',
            qualification_scope='disposable-emulator-only',binding=binding,
            source_manifest_sha256=source_pin,hold_plan_sha256=hold_pin,
            root_before=original['digests']['root'],root_after=manifest['root'],
            prefix_guard=original['digests']['prefix'],suffix_guard=original['digests']['suffix'],
            root_write_authorized=False,boot_write_authorized=False,whole_card_write_authorized=False,
            normal_boot_release_authorized=False)
        bootstrap=BOOTSTRAP.replace('from forge_recovery_restore_worker import run',
                                    'from forge_restore_stream_fixture import run')
        def command(pin):
            return probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(bootstrap)+' '+pin)
        def launch(name,request,operation):
            data,pin=packet(request)
            write_record(fd,name+'-request.json',request)
            write_record(fd,name+'-packet.json',dict(packet_sha256=pin))
            write_record(fd,name+'-owner-code.json',json.loads(data))
            error_fd=os.open(name+'-stderr.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=fd)
            with os.fdopen(error_fd,'wb') as errors:
                def execute(channel,half_close,wait_success):
                    start(channel,data,pin)
                    return operation(channel,half_close,wait_success)
                return run_process(command(pin),execute,errors,timeout=900,idle_timeout=150)
        def seed(channel,half_close,wait_success):
            half_close()
            value=header(channel)
            if channel.read(1): raise ValueError('Trailing seed output')
            wait_success()
            return value
        seed_request=dict(protocol=1,plan=copy.deepcopy(plan),pin=digest(plan),inputs={},
            attempt=uuid.uuid4().hex,fixture_action='seed-two' if reboot is not None else 'seed',fixture_lease=lease.renew())
        seeded=launch('seed',seed_request,seed)
        write_record(fd,'seed.json',seeded)
        changed=capture(probe,directory/'seeded',binding['cid'],binding['disk_id'],
                        boot_id=binding['boot_id'],device=binding['device'],lease=lease)
        if (seeded!=dict(status='seeded-disposable-root',root=changed['digests']['root'],
                         root_written=True,physical_qualified=False) or
                changed['digests']['root']==original['digests']['root'] or
                any(changed['digests'][name]!=original['digests'][name] for name in ('prefix','suffix'))):
            raise ValueError('Controlled disposable root change differs')
        plan['root_before']=changed['digests']['root']
        pin=digest(plan)
        inputs=dict.fromkeys(INPUT_NAMES)
        inputs.update(hold_plan=hold_plan,hold_pin=hold_pin,source_manifest=manifest,source_pin=source_pin,
            hash_plan=binding,hash_observation=changed,hold_inspection=inspection)
        attempt=uuid.uuid4().hex
        request=dict(protocol=1,plan=plan,pin=pin,inputs=inputs,attempt=attempt,
                     fixture_action=('restore-interrupted' if reboot is not None else
                                     'restore-lost-completion' if lost_completion else 'restore'),fixture_lease=None)
        events=Events(fd)
        uncertain=None
        try:
            def restore(channel,half_close,wait_success):
                return exchange(channel,plan,pin,manifest,attempt,
                    lambda consume:stream(directory/'backup',manifest,source_pin,consume),
                    authorize=lambda p,s,phase:dict(restore=s,boot_id=binding['boot_id'],phase=phase),
                    renew=lease.renew,record=events.record,half_close=half_close,wait_success=wait_success)
            try:
                receipt=launch('restore',request,restore)
            except (OSError,EOFError,ValueError,RuntimeError) as exc:
                if not lost_completion and reboot is None: raise
                uncertain=dict(status='uncertain',plan_sha256=pin,attempt=attempt,
                    error=type(exc).__name__+': '+str(exc),root_written='unknown',
                    normal_boot_release_authorized=False)
                write_record(fd,'restore-acceptance.json',uncertain)
            else:
                if lost_completion or reboot is not None: raise ValueError('Injected restore failure unexpectedly succeeded')
        finally: events.close()
        if reboot is not None:
            if uncertain is None or 'Injected SIGKILL after first durable restore chunk' not in (
                    directory/'restore-stderr.log').read_text():
                raise ValueError('Restore did not reach the intended interruption boundary')
            fenced=launch('fence',dict(request,fixture_action='fence'),seed)
            write_record(fd,'fence.json',fenced)
            check_incomplete_fence(fenced,plan,pin,attempt)
            interrupted=capture(probe,directory/'interrupted',binding['cid'],binding['disk_id'],
                                boot_id=binding['boot_id'],device=binding['device'],lease=lease)
            if (interrupted['digests']['root']!=partial or
                    partial in (original['digests']['root'],changed['digests']['root']) or
                    any(interrupted['digests'][name]!=original['digests'][name] for name in ('prefix','suffix'))):
                raise ValueError('Interrupted card is not the exact planned partial root')
            def rejected(name,attempt_request,reason):
                try: launch(name,attempt_request,seed)
                except (OSError,EOFError,ValueError,RuntimeError): pass
                else: raise ValueError('Forbidden restore worker unexpectedly returned a response')
                if reason not in (directory/(name+'-stderr.log')).read_text():
                    raise ValueError('Restore rejection occurred for an unrelated reason')
                write_record(fd,name+'.json',dict(status='rejected',expected_reason=reason,root_write_authorized=False))
            rejected('same-boot-rejected',dict(request,fixture_action='restore',attempt=uuid.uuid4().hex),
                     'Another target attempt is incomplete')
            old_binding=copy.deepcopy(binding)
            binding,lease,reboot_evidence=reboot()
            checked(binding)
            if (binding['boot_id']==old_binding['boot_id'] or
                    not exact({k:v for k,v in binding.items() if k!='boot_id'},
                              {k:v for k,v in old_binding.items() if k!='boot_id'}) or
                    lease.probe is not probe or lease.boot_id!=binding['boot_id'] or lease.owner!=binding['lease_owner']):
                raise ValueError('Rebooted restore fixture binding differs')
            write_record(fd,'reboot.json',reboot_evidence)
            rejected('stale-boot-rejected',dict(request,fixture_action='restore'),'Disposable restore boot changed')
            fresh=capture(probe,directory/'after-reboot',binding['cid'],binding['disk_id'],
                          boot_id=binding['boot_id'],device=binding['device'],lease=lease)
            if not exact(fresh['digests'],dict(interrupted['digests'],boot_id=binding['boot_id'])):
                raise ValueError('Partial card changed across refusal or reboot')
            plan=copy.deepcopy(plan)
            plan.update(binding=binding,root_before=fresh['digests']['root'])
            pin=digest(plan)
            attempt=uuid.uuid4().hex
            inputs=copy.deepcopy(inputs)
            inputs.update(hash_plan=binding,hash_observation=fresh)
            request=dict(protocol=1,plan=plan,pin=pin,inputs=inputs,attempt=attempt,
                         fixture_action='restore',fixture_lease=None)
            os.mkdir('resumed-events',mode=0o700,dir_fd=fd)
            resumed_fd=private_directory(directory/'resumed-events')
            events=Events(resumed_fd)
            try: receipt=launch('resumed',request,restore)
            finally:
                events.close()
                os.close(resumed_fd)
            write_record(fd,'resumed-receipt.json',receipt)
            result.update(interrupted_restore_qualified=True,original_host_status='uncertain',
                same_boot_write_rejected=True,stale_boot_worker_rejected=True,
                exact_partial_root_verified=True,new_boot_restore_verified=True)
        elif lost_completion:
            # No resend or second restore. Inspect the original durable attempt.
            if uncertain is None: raise ValueError('Missing uncertain original restore evidence')
            text=(directory/'restore-stderr.log').read_text()
            if 'Injected lost restore completion after durable receipt' not in text:
                raise ValueError('Restore failed before the intended lost-completion boundary')
            fenced=launch('fence',dict(request,fixture_action='fence'),seed)
            write_record(fd,'fence.json',fenced)
            receipt=check_completed_fence(fenced,plan,pin,attempt)
            write_record(fd,'historical-receipt.json',receipt)
        else:
            write_record(fd,'receipt.json',receipt)
        after=capture(probe,directory/'after',binding['cid'],binding['disk_id'],
                      boot_id=binding['boot_id'],device=binding['device'],lease=lease)
        expected_after=copy.deepcopy(original)
        expected_after['digests']['boot_id']=binding['boot_id']
        if after!=expected_after: raise ValueError('Whole-card bytes differ after streamed root restore')
        remaining_chunk=1 if reboot is not None else 0
        if receipt['bytes_written']!=manifest['chunks'][remaining_chunk]['bytes']:
            raise ValueError('Expected only the remaining changed root chunk to be written')
        result.update(status='passed',receipt=receipt,card_restored=True,
            protected_ranges_unchanged=True,changed_chunk_written=True,matching_chunks_skipped=True,
            source_chunks=len(manifest['chunks']),substituted_boundaries=['physical-plan-compiler','physical-identity-selector'])
        if lost_completion:
            result.update(lost_completion_qualified=True,original_host_status='uncertain',
                historical_completion_verified=True,restore_retried=False)
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        write_record(fd,'acceptance.json',result)
        os.close(fd)
    return result
