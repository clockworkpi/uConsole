"""Owner-pinned recovery jobs; clients select IDs, never hosts or disk paths."""
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from forge_host_tasks import unique_object
from forge_ram_transport import RecoveryProbe
from forge_recovery_bootplan import digest
from forge_recovery_commit_protocol import exact
from forge_recovery_session import Session, binding
from forge_target_journal import private_directory, read_record


FIELDS = {
    'backup-card': {'cid', 'disk_id', 'device', 'destination'},
    'hash-card': {'cid', 'disk_id', 'device', 'destination'},
    'restore-root': {'journal', 'plan_sha256', 'source_directory', 'health_directory',
                     'health_sha256', 'accept_filesystem_errors'},
    'deploy-root': {'journal', 'plan_sha256', 'source_directory', 'health_directory', 'health_sha256'},
    'reconcile-root': {'journal', 'plan_sha256'},
    'install-hold': {'journal', 'plan_sha256', 'root_sha256', 'root_bytes'},
    'release-hold': {'journal', 'plan_sha256', 'root_sha256', 'root_bytes'},
    'reconcile-hold': {'journal', 'plan_sha256', 'root_sha256', 'root_bytes', 'transition'},
    'prepare-hold': {'input_directory', 'input_sha256', 'destination', 'transition'},
    'retry-lease': set(),
}


def path(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or
            not Path(value).is_absolute() or '..' in Path(value).parts or
            any(ord(char) < 32 for char in value)):
        raise ValueError('Recovery policy requires absolute owner-selected paths')
    return Path(value)


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Recovery policy requires explicit SHA-256 pins')
    return value


@dataclass(frozen=True)
class RecoveryJob:
    name: str
    workspace: str
    machine_id: str
    session: Path
    session_pin: str
    probe: RecoveryProbe
    boot_id: str
    owner: str
    operation: str
    arguments: dict


class RecoveryJobs:
    def __init__(self, filename, expected_sha256, workspaces):
        sha(expected_sha256)
        with Path(filename).open('rb') as stream:
            payload = stream.read(1024*1024+1)
        if len(payload) > 1024*1024:
            raise ValueError('Recovery policy exceeds 1 MiB')
        self.sha256 = hashlib.sha256(payload).hexdigest()
        if self.sha256 != expected_sha256:
            raise ValueError('Recovery policy differs from owner-approved digest')
        value = json.loads(payload, object_pairs_hook=unique_object)
        if (not isinstance(value, dict) or set(value) != {'schema', 'jobs'} or
                type(value['schema']) is not int or value['schema'] != 1 or
                not isinstance(value['jobs'], dict) or not 1 <= len(value['jobs']) <= 64):
            raise ValueError('Recovery policy requires schema 1 and 1..64 jobs')
        self.jobs = {}
        for name, item in value['jobs'].items():
            if not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', name):
                raise ValueError('Invalid recovery job name')
            fields = {'workspace', 'machine_id', 'session', 'session_sha256', 'key', 'known_hosts',
                      'operation', 'arguments'}
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError('Invalid recovery job fields')
            if not isinstance(item['workspace'], str) or item['workspace'] not in workspaces:
                raise ValueError('Recovery job must bind to a registered workspace')
            if not isinstance(item['machine_id'], str) or not re.fullmatch('[0-9a-f]{32}', item['machine_id']):
                raise ValueError('Recovery job requires the owner-approved normal-system machine identity')
            operation, arguments = item['operation'], item['arguments']
            if (not isinstance(operation, str) or operation not in FIELDS or
                    not isinstance(arguments, dict) or set(arguments) != FIELDS[operation]):
                raise ValueError('Recovery operation arguments differ from their fixed schema')
            for field, argument in arguments.items():
                if field.endswith('_sha256'): sha(argument)
                elif field in ('journal', 'source_directory', 'health_directory', 'destination', 'input_directory'): path(argument)
                elif field == 'accept_filesystem_errors':
                    if type(argument) is not bool: raise ValueError('Filesystem-error review must be explicit boolean')
                elif field == 'root_bytes':
                    if type(argument) is not int or not 512 <= argument < 2**63 or argument % 512:
                        raise ValueError('Recovery hold requires an aligned owner-approved root size')
                elif field == 'transition':
                    if not isinstance(argument, str) or argument not in ('install-hold', 'release-hold'):
                        raise ValueError('Hold reconciliation requires an explicit transition')
                elif field == 'cid':
                    if not isinstance(argument, str) or not re.fullmatch('[0-9a-f]{32}', argument):
                        raise ValueError('Recovery backup requires a pinned card CID')
                elif field == 'disk_id':
                    if not isinstance(argument, str) or not re.fullmatch('[0-9a-f]{8}', argument):
                        raise ValueError('Recovery backup requires a pinned disk identity')
                elif field == 'device' and argument != '/dev/mmcblk0':
                    raise ValueError('Physical CM4 recovery policy supports /dev/mmcblk0 only')
            session, pin = path(item['session']), sha(item['session_sha256'])
            fd = private_directory(session)
            try:
                saved = read_record(fd, 'binding.json')
            finally:
                os.close(fd)
            if not isinstance(saved, dict) or digest(saved) != pin or saved.get('mode') != 'physical':
                raise ValueError('Recovery job requires the pinned physical session binding')
            probe = RecoveryProbe(saved['host'], path(item['key']), path(item['known_hosts']),
                                  saved['nonce'], saved['kernel'], saved['serial'], port=saved['port'])
            if not exact(saved, binding(probe, saved['boot_id'], saved['owner'])):
                raise ValueError('Recovery policy credentials differ from approved session')
            self.jobs[name] = RecoveryJob(name, item['workspace'], item['machine_id'], session, pin, probe,
                                         saved['boot_id'], saved['owner'], operation, copy.deepcopy(arguments))

    def get(self, name, workspace):
        item = self.jobs.get(name)
        if item is None or item.workspace != workspace:
            raise ValueError('Recovery job is not approved for this workspace')
        return copy.deepcopy(item)

    def describe(self, workspace):
        return [dict(name=item.name, operation=item.operation, policy_sha256=self.sha256,
                     session_sha256=item.session_pin,
                     **{key: value for key, value in item.arguments.items() if key.endswith('_sha256')})
                for item in sorted(self.jobs.values(), key=lambda item: item.name) if item.workspace == workspace]


def execute(job):
    """One foreground action under a durable session; never chain a failed action."""
    arguments = job.arguments
    if job.operation in ('backup-card', 'hash-card', 'prepare-hold') and os.path.lexists(arguments['destination']):
        raise FileExistsError('Recovery destination exists; preserve it and review a new job')
    hold = None
    if job.operation in ('install-hold', 'release-hold', 'reconcile-hold'):
        from forge_recovery_hold_jobs import load
        hold = load(job)
    preparation = None
    if job.operation == 'prepare-hold':
        from forge_recovery_hold_jobs import load_preparation
        preparation = load_preparation(job)
    with Session(job.session, job.session_pin, job.probe, job.boot_id, job.owner) as lease:
        if job.operation == 'retry-lease':
            receipt = lease.retry_pending()
            return dict(status='acknowledged-pending-lease', sequence=receipt['sequence'],
                        root_written=False, normal_boot_release_authorized=False)
        if lease.unresolved:
            raise RuntimeError('Resolve the pending lease through its separately approved retry job')
        if preparation is not None:
            from forge_recovery_hold_jobs import prepare
            return prepare(job, preparation)
        if hold is not None:
            from forge_recovery_hold_jobs import execute as execute_hold
            return execute_hold(job, lease, hold)
        if job.operation == 'hash-card':
            from forge_recovery_hash import capture
            return capture(job.probe, arguments['destination'], arguments['cid'], arguments['disk_id'],
                           boot_id=job.boot_id, device=arguments['device'], lease=lease)
        if job.operation == 'backup-card':
            from forge_recovery_backup import backup
            return backup(job.probe, arguments['destination'], arguments['cid'], arguments['disk_id'],
                          boot_id=job.boot_id, device=arguments['device'], lease=lease, whole_card=True)
        if job.operation == 'reconcile-root':
            from forge_recovery_restore_reconcile_host import reconcile
            result = reconcile(job.probe, arguments['journal'], arguments['plan_sha256'], lease,
                               observed_boot_id=job.boot_id)
            # Keep private host paths, source contents and target topology in the
            # owner journal, not in cross-client job history.
            fields = ('status', 'observation_only', 'root_written', 'requires_new_recovery_boot',
                      'original_attempt_completion_verified', 'normal_boot_release_authorized',
                      'root_write_authorized', 'physical_restore_qualified', 'plan_sha256', 'boot_id')
            return {key: result[key] for key in fields if key in result}
        if job.operation not in ('restore-root', 'deploy-root'):
            raise ValueError('Unsupported recovery action')
        from forge_recovery_restore_dispatch import dispatch, deploy
        derived = job.operation == 'deploy-root'
        def authorize(plan, pin, phase, health):
            if (pin != arguments['plan_sha256'] or digest(plan) != pin or
                    plan['binding']['boot_id'] != job.boot_id or digest(health) != arguments['health_sha256']):
                raise ValueError('Recovery worker review differs from owner-approved job')
            approved = dict(boot_id=job.boot_id, phase=phase, source_health_sha256=arguments['health_sha256'])
            if derived:
                return dict(approved, deploy=pin, rollback_manifest_sha256=plan['rollback_manifest_sha256'])
            errors = not health['filesystem_consistency_qualified']
            if errors and not arguments['accept_filesystem_errors']:
                raise PermissionError('Owner did not approve restoration of source filesystem errors')
            return dict(approved, restore=pin, accept_source_filesystem_errors=errors)
        return (deploy if derived else dispatch)(job.probe, arguments['journal'], arguments['plan_sha256'], lease,
            arguments['source_directory'], arguments['health_directory'], arguments['health_sha256'],
            authorize=authorize)
