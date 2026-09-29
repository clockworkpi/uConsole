"""Journal fresh restore observations without rewriting or retrying an attempt."""
import copy
import fcntl
import os
from pathlib import Path
import re
import sys
import uuid

from forge_recovery_commit_protocol import exact
from forge_recovery_hash import capture as capture_hashes
from forge_recovery_restore_dispatch import check_health
from forge_recovery_restore_ledger import request as check_attempt
from forge_recovery_restore_observe_transport import capture as capture_observation
from forge_recovery_restore_plan import binding_checked
from forge_recovery_operation_contract import load, source_kind
from forge_recovery_source_contract import DERIVATIVE
from forge_recovery_derivative_health import check_health as check_derivative_health
from forge_recovery_restore_reconcile import check_fence, check_restore_inspection, classify
from forge_target_journal import private_directory, read_record, write_record


def original_attempt(directory, plan, pin, inputs):
    fd = private_directory(directory/'restore-attempt')
    try:
        original = read_record(fd,'dispatch.json')
        health = read_record(fd,'source-health.json')
    finally:
        os.close(fd)
    fields = {'plan_sha256','attempt','binding','packet_sha256',
              'source_manifest_sha256','source_health_sha256','worker_protocol'}
    if (not isinstance(original,dict) or set(original)!=fields or
            original['plan_sha256']!=pin or not exact(original['binding'],plan['binding']) or
            original['source_manifest_sha256']!=plan['source_manifest_sha256'] or
            type(original['worker_protocol']) is not int or original['worker_protocol']!=1 or
            any(not isinstance(original[key],str) or not re.fullmatch('[0-9a-f]{64}',original[key])
                for key in ('packet_sha256','source_health_sha256'))):
        raise ValueError('Original restore dispatch binding differs')
    check_attempt(plan,pin,original['attempt'])
    if source_kind(plan) == DERIVATIVE:
        if original['source_health_sha256'] != plan['source_health_sha256']:
            raise ValueError('Original deployment health differs from plan')
        check_derivative_health(health,original['source_health_sha256'],inputs['derivative'],
                                plan['source_manifest_sha256'])
    else:
        check_health(health,original['source_health_sha256'],inputs['source_manifest'])
    # The old packet pin identifies historical code, not today's installed code.
    # Rebuilding that packet after an upgrade must not rewrite its provenance.
    return original


def reconcile(probe, directory, pin, lease, *, observed_boot_id=None):
    """Fence, inspect and independently hash in an exclusive new query journal.

    The original attempt is immutable, including uncertain acceptance. A new
    boot must be explicit and carry its own bound lease. There is no write,
    retry, reboot, filesystem repair or normal-boot-release path here.
    """
    directory = Path(directory).absolute()
    journal = private_directory(directory)
    out_fd = None
    try:
        fcntl.flock(journal,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan = load(directory,pin)
        inputs = read_record(journal,'inputs.json')
        binding = copy.deepcopy(plan['binding'])
        if observed_boot_id is not None: binding['boot_id'] = observed_boot_id
        binding_checked(binding)
        if (any(getattr(probe,key)!=binding[key] for key in ('nonce','kernel','serial','mode')) or
                lease.probe is not probe or lease.boot_id!=binding['boot_id'] or
                lease.owner!=binding['lease_owner']):
            raise ValueError('Restore reconciliation probe/lease differs')
        original = original_attempt(directory,plan,pin,inputs)
        attempt, query = original['attempt'], uuid.uuid4().hex
        name = 'reconcile-'+query
        os.mkdir(name,mode=0o700,dir_fd=journal)
        os.fsync(journal)
        out_fd = os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=journal)
        output = directory/name
        result = dict(status='incomplete',query=query,attempt=attempt,plan_sha256=pin,
            boot_id=binding['boot_id'],observation_only=True,root_written=False,
            root_write_authorized=False,normal_boot_release_authorized=False,physical_restore_qualified=False)
        try:
            write_record(out_fd,'request.json',dict(plan_sha256=pin,query=query,attempt=attempt,
                original_dispatch=original,observation_binding=binding))
            base = dict(protocol=1,plan=plan,pin=pin,inputs=inputs,attempt=attempt,query=query,
                observed_boot_id=binding['boot_id'],owner=lease.owner)
            def call(action, accepted):
                request = dict(base,action=action,accepted=accepted)
                # Intent (including the accepted lease) is durable before SSH.
                write_record(out_fd,action+'-request.json',request)
                error_fd = os.open(action+'-stderr.log',
                    os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=out_fd)
                os.fsync(out_fd)
                with os.fdopen(error_fd,'wb') as errors:
                    try:
                        reply = capture_observation(probe,request,errors,
                            record=lambda kind,value:write_record(out_fd,action+'-'+kind+'.json',value))
                    finally:
                        failing = sys.exc_info()[0] is not None
                        try:
                            errors.flush()
                            os.fsync(errors.fileno())
                        except BaseException:
                            if not failing: raise
                # Retain malformed replies too; validation must still fail closed.
                write_record(out_fd,action+'.json',reply)
                return reply
            fenced = call('fence',None)
            check_fence(fenced,plan,pin,attempt,binding['boot_id'])
            # A strict renewal cannot precede cleanup of the exact stale RO mount.
            accepted = lease.renew()
            write_record(out_fd,'lease.json',accepted)
            inspection = call('inspect',accepted)
            check_restore_inspection(inspection,plan,pin,attempt,inputs['hold_plan'],
                                     inputs['hold_pin'],binding['boot_id'])
            observed = capture_hashes(probe,output/'hashes',binding['cid'],binding['disk_id'],
                boot_id=binding['boot_id'],device=binding['device'],lease=lease)
            hash_fd = private_directory(output/'hashes')
            try:
                hash_plan = read_record(hash_fd,'plan.json')
                retained = read_record(hash_fd,'acceptance.json')
            finally:
                os.close(hash_fd)
            if not exact(retained,observed): raise ValueError('Retained restore hashes differ from reply')
            if not exact(hash_plan,binding): raise ValueError('Restore hash observation boot or layout differs')
            classified = classify(plan,pin,attempt,inputs['hold_plan'],inputs['hold_pin'],
                                  fenced,inspection,hash_plan,observed)
            probe.inspect(expected_boot_id=binding['boot_id'])
            result.update(classified,evidence_directory=str(output))
        except BaseException as exc:
            result['error'] = type(exc).__name__+': '+str(exc)
            try: write_record(out_fd,'acceptance.json',result)
            except BaseException: pass  # Preserve the actual failure if storage also failed.
            raise
        else:
            write_record(out_fd,'acceptance.json',result)
            return result
    finally:
        if out_fd is not None: os.close(out_fd)
        os.close(journal)
