"""Draft backup-only or hash-only policies from enrollment and read-only inventory."""
import base64
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import struct

from forge_ram_transport import RecoveryProbe
from forge_recovery_layout import reader, root_extent
from forge_recovery_session import Session, binding
from forge_recovery_stage_prepare import pinned_record
from forge_recovery_jobs import RecoveryJobs
from forge_recovery_lease_launcher import binding as boot_binding
from forge_target_journal import private_directory, read_record, write_record


@dataclass(frozen=True)
class BackupSource:
    directory: Path
    acceptance_pin: str
    session_pin: str
    probe: RecoveryProbe
    boot_id: str
    owner: str
    machine_id: str


def inputs(directory, pin, key, known_hosts):
    directory = Path(directory).absolute()
    accepted = pinned_record(directory/'acceptance.json', pin)
    if (accepted.get('status') != 'enrolled-not-leased' or
            any(accepted.get(name) is not False for name in ('lease_acquired', 'target_written',
                'policy_approved', 'reboot_performed', 'root_write_authorized', 'normal_boot_release_authorized')) or
            os.path.lexists(directory/'failure.json')):
        raise ValueError('Require successful owner-pinned enrollment without contradictory failure')
    fd = private_directory(directory/'session')
    try: saved = read_record(fd, 'binding.json')
    finally: os.close(fd)
    probe = RecoveryProbe(saved['host'], key, known_hosts, saved['nonce'], saved['kernel'],
                          saved['serial'], port=saved['port'])
    from forge_recovery_bootplan import digest
    if (digest(saved) != accepted.get('binding_sha256') or
            saved != binding(probe, saved['boot_id'], saved['owner']) or
            any(accepted.get(name) != saved[name] for name in ('host', 'port', 'kernel', 'serial', 'boot_id')) or
            not isinstance(accepted.get('machine_id'), str) or
            not re.fullmatch('[0-9a-f]{32}', accepted['machine_id'])):
        raise ValueError('Enrollment credentials, session or target differ')
    return BackupSource(directory, pin, accepted['binding_sha256'], probe, saved['boot_id'],
                        saved['owner'], accepted['machine_id'])


def prepare(output, source, workspace, *, operation='backup-card'):
    if operation not in ('backup-card', 'hash-card'):
        raise ValueError('Inventory policy permits only backup-card or hash-card')
    job_name, destination = ('backup', 'card-backup') if operation == 'backup-card' else ('hash', 'card-hashes')
    if not isinstance(workspace, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', workspace):
        raise ValueError('Backup policy requires a registered workspace name')
    current = inputs(source.directory, source.acceptance_pin, source.probe.key, source.probe.known_hosts)
    if current != source:
        raise ValueError('Enrollment changed while backup preparation was queued')
    output = Path(output).resolve()
    enrolled = source.directory.resolve(strict=True)
    if output.is_relative_to(enrolled) or enrolled.is_relative_to(output):
        raise ValueError('Keep backup drafts separate from the enrolled session')
    # Local lock only: no lease renewal, sequence reset or target write.
    with Session(source.directory/'session', source.session_pin, source.probe,
                 source.boot_id, source.owner) as session:
        if session.unresolved:
            raise ValueError('Resolve the existing uncertain lease; do not draft another job')
        output.mkdir(mode=0o700)
        fd = private_directory(output)
        try:
            write_record(fd, 'request.json', dict(enrollment_sha256=source.acceptance_pin,
                         session_sha256=source.session_pin, workspace=workspace, boot_id=source.boot_id))
            before = source.probe.inspect(expected_boot_id=source.boot_id)
            write_record(fd, 'before.json', before)
            if boot_binding(before['observation']) != (source.probe.nonce, source.owner, 'physical'):
                raise ValueError('RAM boot owner differs from the enrolled session')
            observed = source.probe._observe('/usr/bin/python3 -I -S -c '+shlex.quote(reader()))
            write_record(fd, 'layout.json', observed)
            header = base64.b64decode(observed['mbr'], validate=True)
            if len(header) != 512: raise ValueError('Expected one DOS partition sector')
            disk_id = format(struct.unpack_from('<I', header, 440)[0], '08x')
            extent = root_extent(observed, observed.get('cid'), disk_id)
            checked = source.probe.inspect_storage(extent['cid'], disk_id, expected_boot_id=source.boot_id)
            write_record(fd, 'checked-storage.json', checked)
            if checked['layout'] != observed or checked['extent'] != extent:
                raise ValueError('Card identity or geometry changed during backup preparation')
            policy = dict(schema=1, jobs={job_name: dict(workspace=workspace, machine_id=source.machine_id,
                session=str(source.directory/'session'), session_sha256=source.session_pin,
                key=str(source.probe.key), known_hosts=str(source.probe.known_hosts), operation=operation,
                arguments=dict(cid=extent['cid'], disk_id=disk_id, device='/dev/mmcblk0',
                               destination=str(output/destination)))})
            pin = write_record(fd, 'policy.json', policy)
            # Exercise the same parser used by subsequent owner approval.
            registry = RecoveryJobs(output/'policy.json', pin, {workspace: output})
            if registry.get(job_name, workspace).operation != operation:
                raise ValueError('Draft differs from requested read-only card operation')
            result = dict(status=f'prepared-{job_name}-policy-not-approved', policy_sha256=pin,
                          enrollment_sha256=source.acceptance_pin, session_sha256=source.session_pin,
                          target_written=False, lease_acquired=False, policy_approved=False,
                          backup_created=False, root_write_authorized=False)
            if operation == 'hash-card': result['hashes_captured'] = False
            write_record(fd, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(fd, 'failure.json', dict(error=type(exc).__name__+': '+str(exc),
                         target_written=False, lease_acquired=False, automatic_retry_performed=False))
            raise
        finally:
            os.close(fd)
