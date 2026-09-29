"""Durable, one-attempt physical restore dispatch for installed owner code.

GUI/MCP jobs reach this worker only through separately pinned owner policy.
Source health is evidence, not implicit approval: the owner callback must
explicitly acknowledge non-clean source filesystems. An existing
attempt is never reused, even if transport failed before acknowledgement.
"""
import copy
import fcntl
import json
import os
import sys
from pathlib import Path
import uuid

from forge_ram_boot_observation import capture as capture_selection
from forge_recovery_bootplan import digest
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_bootstrap import payload, argv, start
from forge_recovery_restore_exchange import exchange
from forge_recovery_restore_ledger import checked_result
from forge_recovery_restore_plan import load
from forge_recovery_restore_process import run as run_process
from forge_recovery_restore_source import stream, opened
from forge_recovery_deploy_plan import load as load_deployment
from forge_recovery_derivative import load as load_derivative, stream as stream_derivative
from forge_recovery_derivative_health import read_health as read_derivative_health
from forge_recovery_lease_client import LeasePulse
from forge_target_journal import private_directory, read_record, write_record


class Events:
    def __init__(self, directory_fd):
        fd = os.open('events.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
        self.output = os.fdopen(fd,'wb')
        self.sequence = self.total = 0
        self.failed = False
        try:
            os.fsync(fd)
            os.fsync(directory_fd)
        except BaseException:
            self.output.close()
            raise

    def record(self, kind, value):
        if self.failed: raise RuntimeError('Restore journal is terminal')
        try:
            data = (json.dumps(dict(sequence=self.sequence,kind=kind,value=value),sort_keys=True,
                               allow_nan=False,separators=(',',':'))+'\n').encode()
            if len(data)>8192 or self.total+len(data)>128*1024*1024:
                raise ValueError('Restore event journal exceeds bound')
            if self.output.write(data)!=len(data): raise OSError('Incomplete restore event write')
            self.output.flush()
            os.fsync(self.output.fileno())
            self.sequence += 1
            self.total += len(data)
        except BaseException:
            self.failed = True
            raise

    def close(self): self.output.close()


def read_health(directory, pin, manifest):
    fd = private_directory(directory)
    try: value = read_record(fd,'acceptance.json')
    finally: os.close(fd)
    return check_health(value,pin,manifest)


def check_health(value, pin, manifest):
    fields = {'status','filesystem_consistency_qualified','restore_authorized','target_written',
              'repair_performed','check_returncodes','image'}
    if (not isinstance(value,dict) or set(value)!=fields or digest(value)!=pin or value['status']!='checked' or
            not exact(value['image'],manifest['card']) or
            any(value[key] is not False for key in ('restore_authorized','target_written','repair_performed')) or
            type(value['filesystem_consistency_qualified']) is not bool or
            not isinstance(value['check_returncodes'],dict) or set(value['check_returncodes'])!={'boot','root'} or
            any(type(code) is not int or not 0<=code<=255 for code in value['check_returncodes'].values()) or
            value['filesystem_consistency_qualified'] != all(code==0 for code in value['check_returncodes'].values())):
        raise ValueError('Source filesystem health evidence differs from pinned backup')
    return value


def dispatch(probe, directory, pin, lease, backup_directory, health_directory, health_pin, *, authorize):
    """Run once after owner health review, retaining uncertainty on all failures.

    authorize(plan,pin,phase,health) is installed owner policy, not a client flag.
    It must return the explicit pinned approval shape below without prompting
    during the worker's bounded handshake window.
    """
    return _dispatch(probe, directory, pin, lease, backup_directory, health_directory, health_pin,
                     authorize=authorize, derivative=False)


def deploy(probe, directory, pin, lease, derivative_directory, health_directory, health_pin, *, authorize):
    """Dispatch one healthy derivative under explicit pinned owner approval.

    The owner callback must approve deploy=pin, boot_id, phase, source_health_sha256
    and rollback_manifest_sha256. Backup restore approval is not interchangeable.
    Before worker dispatch, reverify the complete derivative and retained rollback
    lineage while maintaining this owner's lease. Never retry an existing intent.
    """
    return _dispatch(probe, directory, pin, lease, derivative_directory, health_directory, health_pin,
                     authorize=authorize, derivative=True)


def _dispatch(probe, directory, pin, lease, source_directory, health_directory, health_pin, *, authorize, derivative):
    if not callable(authorize): raise ValueError('Explicit owner restore policy required')
    directory = Path(directory).absolute()
    journal = private_directory(directory)
    attempt_fd = None
    try:
        fcntl.flock(journal,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan = (load_deployment if derivative else load)(directory,pin)
        inputs = read_record(journal,'inputs.json')
        binding = plan['binding']
        manifest = inputs['derivative' if derivative else 'source_manifest']
        if (probe.mode!='physical' or
                any(getattr(probe,key)!=binding[key] for key in ('nonce','kernel','serial','mode')) or
                lease.probe is not probe or lease.boot_id!=binding['boot_id'] or lease.owner!=binding['lease_owner']):
            raise ValueError('Restore probe/lease differs from pinned target')
        # Reject a prior attempt before any new target access.
        if 'restore-attempt' in os.listdir(journal):
            raise FileExistsError('Restore attempt exists; reconciliation is required')
        if derivative:
            if health_pin != plan['source_health_sha256']:
                raise ValueError('Derivative health differs from approved deployment plan')
            health = read_derivative_health(health_directory,health_pin,manifest,plan['source_manifest_sha256'])
            current, _ = load_derivative(source_directory,plan['source_manifest_sha256'])
            if not exact(current,manifest):
                raise ValueError('Derivative source differs from deployment plan')
            stream_derivative(source_directory,plan['source_manifest_sha256'],lambda *_:None,
                              heartbeat=LeasePulse(lease))
        else:
            health = read_health(health_directory,health_pin,manifest)
            with opened(source_directory,manifest['root']) as (_, source_binding):
                if any(not exact(source_binding[key],manifest[key]) for key in source_binding):
                    raise ValueError('Restore source changed before dispatch')
        capture_selection(probe,boot_id=binding['boot_id'],expected_tryboot=0)
        storage = probe.inspect_storage(binding['cid'],binding['disk_id'],
                                        expected_boot_id=binding['boot_id'],device=binding['device'])
        if not exact(storage['extent'],binding['extent']): raise ValueError('Restore target layout changed')
        attempt = uuid.uuid4().hex
        data, packet_pin = payload(plan,pin,inputs,attempt)
        command = argv(probe,packet_pin)  # Recheck credential pins before creating intent.
        os.mkdir('restore-attempt',mode=0o700,dir_fd=journal)
        os.fsync(journal)
        attempt_fd = os.open('restore-attempt',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=journal)
        result = dict(status='uncertain',plan_sha256=pin,attempt=attempt,root_written='unknown',
                      normal_boot_release_authorized=False,physical_restore_qualified=False)
        events = None
        try:
            write_record(attempt_fd,'dispatch.json',dict(plan_sha256=pin,attempt=attempt,binding=binding,
                packet_sha256=packet_pin,source_manifest_sha256=plan['source_manifest_sha256'],
                source_health_sha256=health_pin,worker_protocol=1))
            write_record(attempt_fd,'source-health.json',health)
            events = Events(attempt_fd)
            def owner(reviewed_plan,reviewed_pin,phase):
                approval = authorize(copy.deepcopy(reviewed_plan),reviewed_pin,phase,copy.deepcopy(health))
                expected = (dict(deploy=pin,boot_id=binding['boot_id'],phase=phase,
                    source_health_sha256=health_pin,rollback_manifest_sha256=plan['rollback_manifest_sha256'])
                    if derivative else dict(restore=pin,boot_id=binding['boot_id'],phase=phase,
                        source_health_sha256=health_pin,
                        accept_source_filesystem_errors=not health['filesystem_consistency_qualified']))
                if not exact(approval,expected): raise ValueError('Owner root restore/source-health approval differs')
                events.record('source-health-approval',approval)
                return dict(restore=pin,boot_id=binding['boot_id'],phase=phase)
            error_fd = os.open('stderr.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=attempt_fd)
            os.fsync(attempt_fd)
            with os.fdopen(error_fd,'wb') as errors:
                def operation(channel,half_close,wait_success):
                    start(channel,data,packet_pin)
                    events.record('bootstrap-ready',dict(packet_sha256=packet_pin))
                    def produce(consume):
                        if derivative:
                            return stream_derivative(source_directory,plan['source_manifest_sha256'],consume)
                        return stream(source_directory,manifest,plan['source_manifest_sha256'],consume)
                    return exchange(channel,plan,pin,manifest,attempt,
                        produce,
                        authorize=owner,renew=lease.renew,record=events.record,
                        half_close=half_close,wait_success=wait_success)
                try:
                    receipt = run_process(command,operation,errors)
                finally:
                    failing = sys.exc_info()[0] is not None
                    try:
                        errors.flush()
                        os.fsync(errors.fileno())
                    except BaseException:
                        if not failing: raise
            checked_result(receipt,plan,pin)
            probe.inspect(expected_boot_id=binding['boot_id'])
            result.update(status='acknowledged',root_written=receipt['bytes_written']>0,receipt=receipt)
        except BaseException as exc:
            result['error'] = type(exc).__name__+': '+str(exc)
            # A journal disk failure may prevent this record. Exclusive attempt
            # creation still blocks redispatch; preserve the original failure.
            try: write_record(attempt_fd,'acceptance.json',result)
            except BaseException: pass
            raise
        else:
            write_record(attempt_fd,'acceptance.json',result)
            return result
        finally:
            failing = sys.exc_info()[0] is not None
            if events is not None:
                try: events.close()
                except BaseException:
                    if not failing: raise
    finally:
        if attempt_fd is not None: os.close(attempt_fd)
        os.close(journal)
