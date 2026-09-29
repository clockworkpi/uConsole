"""Deploy and roll back a healthy derivative on a new disposable QEMU card.

Only physical plan compilation and identity selection are substituted, using
the tightly bounded restore fixture. Production framing, lease ownership,
claims, chunk writes/readback and durable attempt ledgers remain real. This is
not qualification of physical dispatch, an application image, or native boot.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid

from forge_recovery_archive import materialize
from forge_recovery_backup import backup
from forge_recovery_bootplan import digest
from forge_recovery_deploy_plan import INPUT_NAMES as DEPLOY_INPUTS
from forge_recovery_derivative import prepare as prepare_derivative, load, stream as stream_derivative
from forge_recovery_derivative_health import inspect_root, read_health
from forge_recovery_hash import capture
from forge_recovery_lease_client import LeasePulse
from forge_recovery_restore_bootstrap import BOOTSTRAP, start
from forge_recovery_restore_dispatch import Events
from forge_recovery_restore_exchange import exchange
from forge_recovery_restore_plan import INPUT_NAMES
from forge_recovery_restore_process import run as run_process
from forge_recovery_restore_protocol import header
from forge_recovery_restore_source import prepare, stream
from forge_restore_stream_probe import checked, packet, check_completed_fence, check_incomplete_fence
from forge_target_journal import private_directory, read_record, write_record


def check_deployed(original, observed, manifest):
    expected = copy.deepcopy(original)
    expected['digests'].update(root=manifest['root'], card=manifest['card'])
    if observed != expected:
        raise ValueError('Deployed root, card or protected-range hashes differ')


def partial_root(backup_directory, manifest, pin, derivative_directory, derivative_pin):
    first = []
    def retain(chunk, data):
        if chunk['offset'] == 0:
            first.append(data)
    stream_derivative(derivative_directory, derivative_pin, retain)
    if len(first) != 1 or len(manifest['chunks']) < 2:
        raise ValueError('Interrupted derivative requires multiple root chunks')
    hashed = hashlib.sha256()
    def consume(chunk, data):
        replacement = first[0] if chunk['offset'] == 0 else data
        if len(replacement) != chunk['bytes']:
            raise ValueError('Partial derivative chunk size differs')
        hashed.update(replacement)
    stream(backup_directory, manifest, pin, consume)
    return dict(manifest['root'], sha256=hashed.hexdigest())


def make_derivative(backup_directory, manifest, pin, directory, heartbeat, *, multi_chunk=False):
    """Format only a newly created private regular file, never a target device."""
    materialize(backup_directory, directory/'materialized', heartbeat=heartbeat)
    root = directory/'new-root.img'
    with root.open('xb') as output:
        os.fchmod(output.fileno(), 0o600)
        output.truncate(manifest['root']['bytes'])
    heartbeat()
    # Small blocks place ext4 backup metadata in multiple root chunks, allowing
    # interruption after chunk zero to leave a genuinely partial derivative.
    options = ['-b', '1024'] if multi_chunk else []
    subprocess.run(['mkfs.ext4', '-q', '-F', *options, '-E',
                    'lazy_itable_init=0,lazy_journal_init=0', str(root)],
                   check=True, capture_output=True, timeout=60)
    heartbeat()
    image = directory/'materialized'/'image.img'
    with image.open('r+b') as output, root.open('rb') as source:
        output.seek(manifest['root']['offset'])
        shutil.copyfileobj(source, output, 1048576)
        output.flush()
        os.fsync(output.fileno())
    accepted = prepare_derivative(backup_directory, manifest, pin, image,
                                 directory/'derivative', heartbeat=heartbeat)
    derived_pin = accepted['manifest_sha256']
    derived, _ = load(directory/'derivative', derived_pin)
    health = inspect_root(directory/'derivative', derived_pin, directory/'health', heartbeat=heartbeat)
    read_health(directory/'health', digest(health), derived, derived_pin)
    if not health['root_filesystem_consistency_qualified']:
        raise ValueError('Disposable derivative filesystem is not clean')
    return derived, derived_pin, health


def run(probe, directory, binding, hold_plan, hold_pin, inspection, lease, *, lost_completion=False, reboot=None):
    checked(binding)
    if type(lost_completion) is not bool:
        raise ValueError('Explicit derivative completion-loss selection required')
    if reboot is not None and (not callable(reboot) or lost_completion):
        raise ValueError('Interrupted deployment requires a distinct trusted reboot callback')
    if (probe.mode != 'emulated' or lease.probe is not probe or lease.boot_id != binding['boot_id'] or
            binding.get('lease_owner') != lease.owner):
        raise ValueError('Disposable deployment lease differs')
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    result = dict(status='incomplete', qualification_scope='disposable-emulator-only',
                  physical_deployment_qualified=False, native_boot_qualified=False,
                  production_host_dispatch_qualified=False, normal_boot_release_authorized=False)
    try:
        pulse = LeasePulse(lease)
        backup(probe, directory/'backup', binding['cid'], binding['disk_id'], boot_id=binding['boot_id'],
               device=binding['device'], lease=lease, whole_card=True)
        def hashes(name):
            return capture(probe, directory/name, binding['cid'], binding['disk_id'],
                           boot_id=binding['boot_id'], device=binding['device'], lease=lease)
        original = hashes('before')
        prepare(directory/'backup', directory/'source', original['digests']['root'], heartbeat=pulse)
        source_fd = private_directory(directory/'source')
        try:
            manifest = read_record(source_fd, 'manifest.json')
        finally:
            os.close(source_fd)
        source_pin = digest(manifest)
        derived, derived_pin, health = make_derivative(directory/'backup', manifest, source_pin, directory, pulse,
                                                      multi_chunk=reboot is not None)
        partial = None
        if reboot is not None:
            partial = partial_root(directory/'backup', manifest, source_pin, directory/'derivative', derived_pin)
            if partial in (manifest['root'], derived['root']):
                raise ValueError('Interruption fixture requires a genuinely partial root')
        base = dict(schema=1, kind='guarded-backup-root-restore-plan', operation='restore-backup-root',
            qualification_scope='disposable-emulator-only', binding=binding,
            source_manifest_sha256=source_pin, hold_plan_sha256=hold_pin,
            root_before=original['digests']['root'], root_after=manifest['root'],
            prefix_guard=original['digests']['prefix'], suffix_guard=original['digests']['suffix'],
            root_write_authorized=False, boot_write_authorized=False, whole_card_write_authorized=False,
            normal_boot_release_authorized=False)
        deploy = dict(base, kind='guarded-derived-root-deploy-plan', operation='deploy-derived-root',
            source_manifest_sha256=derived_pin, root_after=derived['root'],
            rollback_manifest_sha256=source_pin, rollback_root=manifest['root'],
            source_health_sha256=digest(health), derivative_root_filesystem_qualified=True,
            native_boot_qualified=False)
        inputs = dict.fromkeys(DEPLOY_INPUTS)
        inputs.update(hold_plan=hold_plan, hold_pin=hold_pin, derivative=derived, derivative_pin=derived_pin,
            hash_plan=binding, hash_observation=original, hold_inspection=inspection,
            health=health, health_pin=digest(health))
        bootstrap = BOOTSTRAP.replace('from forge_recovery_restore_worker import run',
                                     'from forge_restore_stream_fixture import run')
        def launch(name, request, operation):
            data, pin = packet(request)
            write_record(fd, name+'-owner-code.json', json.loads(data))
            error_fd = os.open(name+'-stderr.log', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600, dir_fd=fd)
            command = probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(bootstrap)+' '+pin)
            with os.fdopen(error_fd, 'wb') as errors:
                def execute(channel, half_close, wait_success):
                    start(channel, data, pin)
                    return operation(channel, half_close, wait_success)
                return run_process(command, execute, errors, timeout=900, idle_timeout=150)
        def fence_reply(channel, half_close, wait_success):
            half_close()
            value = header(channel)
            if channel.read(1): raise ValueError('Trailing deployment fence output')
            wait_success()
            return value
        def rejected(name, request, reason):
            try:
                launch(name, request, fence_reply)
            except (OSError, EOFError, ValueError, RuntimeError):
                pass
            else:
                raise ValueError('Forbidden deployment worker returned a response')
            if reason not in (directory/(name+'-stderr.log')).read_text():
                raise ValueError('Deployment rejection occurred for an unrelated reason')
            write_record(fd, name+'.json', dict(status='rejected', expected_reason=reason,
                                              root_write_authorized=False))
        interrupted_request = None
        def transfer(name, plan, inputs, source, produce, lose=False, interrupt=False):
            nonlocal interrupted_request
            pin, attempt = digest(plan), uuid.uuid4().hex
            request = dict(protocol=1, plan=plan, pin=pin, inputs=inputs, attempt=attempt,
                           fixture_action=('restore-interrupted' if interrupt else
                                           'restore-lost-completion' if lose else 'restore'), fixture_lease=None)
            event_path = directory/(name+'-events')
            event_path.mkdir(mode=0o700)
            event_fd = private_directory(event_path)
            events = Events(event_fd)
            try:
                def transact(channel, half_close, wait_success):
                    return exchange(channel, plan, pin, source, attempt, produce,
                        authorize=lambda p, s, phase: dict(restore=s, boot_id=binding['boot_id'], phase=phase),
                        renew=lease.renew, record=events.record, half_close=half_close, wait_success=wait_success)
                try:
                    receipt = launch(name, request, transact)
                except (OSError, EOFError, ValueError, RuntimeError) as exc:
                    if not (lose or interrupt): raise
                    write_record(fd, name+'-uncertain.json', dict(status='uncertain', plan_sha256=pin,
                        attempt=attempt, error=type(exc).__name__+': '+str(exc), root_written='unknown',
                        normal_boot_release_authorized=False))
                    marker = ('Injected SIGKILL after first durable restore chunk' if interrupt else
                              'Injected lost restore completion after durable receipt')
                    if marker not in (directory/(name+'-stderr.log')).read_text():
                        raise ValueError('Derivative failed before intended failure boundary') from exc
                    fenced = launch(name+'-fence', dict(request, fixture_action='fence'), fence_reply)
                    write_record(fd, name+'-fence.json', fenced)
                    if interrupt:
                        check_incomplete_fence(fenced, plan, pin, attempt)
                        rejected('same-boot-rejected', dict(request, fixture_action='restore', attempt=uuid.uuid4().hex),
                                 'Another target attempt is incomplete')
                        interrupted_request = request
                        return None
                    receipt = check_completed_fence(fenced, plan, pin, attempt)
                else:
                    if lose or interrupt: raise ValueError('Derivative failure was not injected')
                write_record(fd, name+'-receipt.json', receipt)
                return receipt
            finally:
                events.close()
                os.close(event_fd)
        deployed = transfer('deploy', deploy, inputs, derived,
                            lambda consume: stream_derivative(directory/'derivative', derived_pin, consume),
                            lost_completion, reboot is not None)
        changed = hashes('deployed')
        if reboot is None:
            check_deployed(original, changed, derived)
        else:
            if (changed['digests']['root'] != partial or
                    any(changed['digests'][key] != original['digests'][key] for key in ('prefix', 'suffix', 'boot_id'))):
                raise ValueError('Interrupted derivative differs from exact partial root')
            old_binding = copy.deepcopy(binding)
            binding, lease, reboot_evidence = reboot()
            checked(binding)
            if (binding['boot_id'] == old_binding['boot_id'] or
                    {k:v for k,v in binding.items() if k != 'boot_id'} !=
                    {k:v for k,v in old_binding.items() if k != 'boot_id'} or
                    lease.probe is not probe or lease.boot_id != binding['boot_id'] or lease.owner != binding['lease_owner']):
                raise ValueError('Rebooted deployment fixture binding differs')
            write_record(fd, 'reboot.json', reboot_evidence)
            rejected('stale-boot-rejected', dict(interrupted_request, fixture_action='restore'),
                     'Disposable restore boot changed')
            fresh = hashes('after-reboot')
            expected = copy.deepcopy(changed)
            expected['digests']['boot_id'] = binding['boot_id']
            if fresh != expected:
                raise ValueError('Partial derivative changed across recovery reboot')
            changed = fresh
        rollback = dict(base, binding=binding, root_before=changed['digests']['root'])
        inputs = dict.fromkeys(INPUT_NAMES)
        inputs.update(hold_plan=hold_plan, hold_pin=hold_pin, source_manifest=manifest, source_pin=source_pin,
                      hash_plan=binding, hash_observation=changed, hold_inspection=inspection)
        restored = transfer('rollback', rollback, inputs, manifest,
                            lambda consume: stream(directory/'backup', manifest, source_pin, consume))
        expected = copy.deepcopy(original)
        expected['digests']['boot_id'] = binding['boot_id']
        if hashes('after') != expected:
            raise ValueError('Rollback differs from original whole-card hashes')
        changed_bytes = sum(derived['chunks'][index]['bytes'] for index in derived['changed_chunks'])
        if reboot is not None:
            changed_bytes = manifest['chunks'][0]['bytes']
        receipts = (restored,) if reboot is not None else (deployed, restored)
        if changed_bytes <= 0 or any(value['bytes_written'] != changed_bytes for value in receipts):
            raise ValueError('Deploy/rollback changed-chunk accounting differs')
        result.update(status='passed', derivative_deployed=reboot is None, original_card_restored=True,
            protected_ranges_unchanged=True, clean_derivative_root_verified=True,
            changed_chunks=len(derived['changed_chunks']), bytes_written_each_direction=changed_bytes,
            lost_completion_qualified=lost_completion, deployment_retried=False,
            substituted_boundaries=['physical-plan-compiler', 'physical-identity-selector'])
        if reboot is not None:
            result.update(interrupted_deployment_qualified=True, original_host_status='uncertain',
                same_boot_write_rejected=True, stale_boot_worker_rejected=True,
                exact_partial_root_verified=True, new_boot_rollback_verified=True)
    except BaseException as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        write_record(fd, 'acceptance.json', result)
        os.close(fd)
    return result
