"""Separate mutating SSH channel with durable two-phase CONFIG intent.

authorize(plan) must be the trusted owner gate,
not client flags or the read-only backup lease. There is exactly one attempt
per pinned journal: failures retain uncertain evidence and require inspection.
"""
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import time
import uuid

from forge_recovery_bootcommit import validate
from forge_recovery_commit_protocol import Protocol, decode
from forge_recovery_commit_worker import BOOTSTRAP, payload
from forge_recovery_lease_client import LeasePulse
from forge_target_journal import private_directory, read_record, write_record


def exchange(argv, request, protocol, *, prepared, unmounted, heartbeat, transcript, errors=None, timeout=82800,
             idle_timeout=90):
    """Bounded duplex channel; renewal is disabled throughout the mounted phase."""
    if (type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 82800 or
            type(idle_timeout) is not int or not 1 <= idle_timeout <= 180):
        raise ValueError('Invalid commit transport deadline')
    pending = (json.dumps(request,allow_nan=False)+'\n').encode()
    if len(pending)>100*1024*1024: raise ValueError('Owner commit request exceeds bound')
    process = None
    diagnostics,lines = bytearray(),bytearray()
    offset,total = 0,0
    deadline = time.monotonic()+timeout
    last_activity = time.monotonic()
    try:
        process = subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        with selectors.DefaultSelector() as selector:
            for stream,event,label in ((process.stdin,selectors.EVENT_WRITE,'input'),
                    (process.stdout,selectors.EVENT_READ,'output'),(process.stderr,selectors.EVENT_READ,'error')):
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,event,label)
            while selector.get_map():
                if protocol.may_renew: heartbeat()
                now = time.monotonic()
                if now>=deadline or now-last_activity>idle_timeout:
                    raise TimeoutError('Commit transport deadline or progress timeout')
                for key,_ in selector.select(min(1,deadline-now)):
                    if key.data=='input':
                        try: written=os.write(key.fd,pending[offset:offset+65536])
                        except BlockingIOError: continue
                        if written<=0: raise EOFError('Commit request channel closed')
                        offset+=written
                        last_activity=time.monotonic()
                        if offset==len(pending):
                            selector.unregister(key.fileobj)
                        continue
                    data=os.read(key.fd,65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    last_activity=time.monotonic()
                    if key.data=='error':
                        diagnostics.extend(data)
                        if len(diagnostics)>65536: raise ValueError('Oversized commit diagnostics')
                        if errors is not None: errors.write(data)
                        continue
                    total+=len(data)
                    if total>16*1024*1024: raise ValueError('Oversized commit transcript')
                    transcript.write(data)
                    lines.extend(data)
                    while b'\n' in lines:
                        line,_,rest=lines.partition(b'\n')
                        lines=bytearray(rest)
                        if len(line)>4096: raise ValueError('Oversized commit protocol line')
                        message=decode(line)
                        action=protocol.consume(message)
                        if action=='prepared':
                            if offset!=len(pending): raise ValueError('Prepared before complete request delivery')
                            transcript.flush()
                            os.fsync(transcript.fileno())
                            response=prepared(message)
                            pending=(json.dumps(response,allow_nan=False)+'\n').encode()
                            if len(pending)>4096: raise ValueError('Oversized owner acknowledgement')
                            offset=0
                            protocol.committed()
                            selector.register(process.stdin,selectors.EVENT_WRITE,'input')
                        elif action=='unmounted':
                            unmounted(message)
                    if len(lines)>4096: raise ValueError('Oversized unterminated commit protocol line')
            if lines: raise ValueError('Truncated commit protocol line')
            if process.wait(timeout=max(0.01,deadline-time.monotonic())):
                raise RuntimeError('Commit worker/SSH failed; completion is uncertain')
            if protocol.phase!='complete' or protocol.result is None:
                raise RuntimeError('Commit ended without a verified completion receipt')
        return protocol.result
    finally:
        try:
            for stream in (transcript,errors):
                if stream is not None:
                    stream.flush()
                    os.fsync(stream.fileno())
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                for stream in (process.stdin,process.stdout,process.stderr): stream.close()


def dispatch(probe, directory, pin, lease, *, authorize):
    """Dispatch exactly once from an existing, private, digest-pinned plan journal.

    authorize must return {'commit': pin, 'boot_id': ...} only after validating
    the owner gates. It runs before the guest mounts boot, and must not wait for
    interactive input: the guest approval window is bounded to 60 seconds.
    """
    if not callable(authorize): raise ValueError('Explicit owner authorization callback required')
    directory=Path(directory).absolute()
    journal=private_directory(directory)
    attempt_fd=None
    try:
        fcntl.flock(journal,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=validate(read_record(journal,'plan.json'),pin)
        binding=plan['binding']
        if (any(getattr(probe,key)!=binding[key] for key in ('nonce','kernel','serial','mode')) or
                lease.probe is not probe or lease.boot_id!=binding['boot_id'] or
                (binding.get('lease_owner') is not None and binding['lease_owner']!=lease.owner) or
                (probe.mode=='physical' and binding.get('lease_owner')!=lease.owner)):
            raise ValueError('Commit probe/lease differs from pinned plan')
        inspected=probe.inspect_storage(binding['cid'],binding['disk_id'],expected_boot_id=binding['boot_id'],
                                        device=binding['device'])
        if inspected['extent']!=binding['extent']: raise ValueError('Commit storage layout changed')
        os.mkdir('commit-attempt',mode=0o700,dir_fd=journal)
        os.fsync(journal)
        attempt_fd=os.open('commit-attempt',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=journal)
        attempt=uuid.uuid4().hex
        result=dict(status='uncertain',plan_sha256=pin,attempt=attempt,root_written=False)
        try:
            write_record(attempt_fd,'dispatch.json',dict(plan_sha256=pin,attempt=attempt,binding=binding,
                                                        lease_owner=lease.owner,worker_protocol=2))
            protocol=Protocol(plan,pin,attempt)
            pulse=LeasePulse(lease)
            pulse()
            def prepared(message):
                write_record(attempt_fd,'prepared.json',message)
                approval=authorize(copy.deepcopy(plan))
                if approval!={'commit':pin,'boot_id':binding['boot_id']}:
                    raise ValueError('Owner refused or mismatched commit approval')
                receipt=lease.renew()  # Bound RAM-only check before and after renewal.
                response=dict(approval,attempt=attempt,lease=receipt)
                write_record(attempt_fd,'commit-intent.json',response)
                return response
            def unmounted(message):
                write_record(attempt_fd,'unmounted.json',message)
            log_fd=os.open('protocol.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=attempt_fd)
            with os.fdopen(log_fd,'wb') as transcript:
                error_fd=os.open('stderr.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=attempt_fd)
                with os.fdopen(error_fd,'wb') as errors:
                    receipt=exchange(probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(BOOTSTRAP)),
                        payload(plan,pin,lease.owner,attempt),protocol,prepared=prepared,unmounted=unmounted,
                        heartbeat=pulse,transcript=transcript,errors=errors)
            probe.inspect(expected_boot_id=binding['boot_id'])
            result.update(status='acknowledged',receipt=receipt)
        except BaseException as exc:
            result['error']=type(exc).__name__+': '+str(exc)
            raise
        finally:
            write_record(attempt_fd,'acceptance.json',result)
        return result
    finally:
        if attempt_fd is not None: os.close(attempt_fd)
        os.close(journal)
