"""Permission-scoped async facade over the forge's existing runtime and CLI."""
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from forge_runtime import Runtime
from forge_audio import MODES as AUDIO_MODES
from forge_keyboard import KeyboardBridge, commands_snapshot
from forge_scenario import change_power, query_power, validate_value
from forge_replay import Replay, ReplayCancelled, run_recorded
from forge_history import History
from forge_host_tasks import HostTasks
from forge_targets import TargetTransactions
from forge_guest_jobs import GuestJobCancelled
from uconsole_agent import GuestTransferCancelled
from forge_workspace import WorkspaceLock, sha256
from uconsole_emulator import ROOT, DISPLAY_BACKENDS, parser, read_config, wait_for_log


GRANTS = ('image-write', 'guest-exec', 'transfer', 'boot', 'force-stop', 'host-task', 'device-control', 'target-write', 'target-recovery')


class JobCancelled(Exception):
    """The owned worker stopped; already committed effects are not rolled back."""


def signal_owned_group(pid, signum):
    """Signal an owned, unreaped session leader's group without releasing its PID."""
    if type(pid) is not int or pid <= 1:
        raise ValueError('Expected an owned process group ID')
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin's XNU killpg1 excludes zombies and returns EPERM if none of
        # the remaining members can be signalled. Do not ignore genuine EPERM:
        # require a complete census showing this unreaped leader and ONLY
        # zombies in its group. No wait/poll may free the leader's PID here.
        # https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_sig.c
        if sys.platform != 'darwin' or not zombie_only_group(pid):
            raise


def zombie_only_group(pid):
    try:
        census = subprocess.run(['/bin/ps', '-axo', 'pid=,pgid=,stat='],
                                capture_output=True, text=True, timeout=5, check=True)
        members = {}
        for line in census.stdout.splitlines():
            process, group, state = line.split()
            process, group = int(process), int(group)
            if group == pid:
                if process in members:
                    return False
                members[process] = state
        return pid in members and all(state.startswith('Z') for state in members.values())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def tail_file(path, limit=65536):
    if type(limit) is not int or not 1 <= limit <= 65536:
        raise ValueError('Log tail limit must be 1..65536 bytes')
    path = Path(path)
    try:
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ''
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return ''
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Log resource must be a singly linked regular file')
            os.lseek(fd, max(0, info.st_size - limit), os.SEEK_SET)
            return os.read(fd, limit).decode(errors='replace')
        finally:
            os.close(fd)
    finally:
        os.close(parent)


class Controller:
    def __init__(self, workspaces, grants=(), files_root=None, *, history=None,
                 host_task_policy=None, host_task_sha256=None, keyboard_oracle=None,
                 target_policy=None, target_policy_sha256=None,
                 recovery_policy=None, recovery_policy_sha256=None):
        self.workspaces = {}
        for name, path in workspaces.items():
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
                raise ValueError('Workspace IDs must contain 1-64 letters, digits, underscores or hyphens')
            self.workspaces[name] = Path(path).resolve()
        self.grants = frozenset(grants)
        if not self.grants <= set(GRANTS):
            raise ValueError('Unknown permission grant')
        self.files_root = Path(files_root).resolve() if files_root else None
        if bool(host_task_policy) != bool(host_task_sha256):
            raise ValueError('Host-task policy path and approved SHA-256 must be provided together')
        self.host_tasks = HostTasks(host_task_policy, host_task_sha256, self.workspaces) if host_task_policy else None
        if 'host-task' in self.grants and not self.host_tasks:
            raise ValueError('The host-task grant requires an explicitly approved policy')
        if bool(target_policy) != bool(target_policy_sha256):
            raise ValueError('Target policy path and approved SHA-256 must be provided together')
        self.targets = TargetTransactions(target_policy, target_policy_sha256, self.workspaces) if target_policy else None
        if 'target-write' in self.grants and not self.targets:
            raise ValueError('The target-write grant requires an explicitly approved policy')
        if bool(recovery_policy) != bool(recovery_policy_sha256):
            raise ValueError('Recovery policy path and approved SHA-256 must be provided together')
        self.recovery_jobs = self.load_recovery_policy(recovery_policy, recovery_policy_sha256) if recovery_policy else None
        if 'target-recovery' in self.grants and not self.recovery_jobs:
            raise ValueError('The target-recovery grant requires an explicitly approved policy')
        self.history = History(history) if history else None
        self.session = uuid.uuid4().hex
        self.runtimes = {}
        # Only the owner configures executable code; client actions contain pins.
        self.keyboard_oracle = Path(keyboard_oracle).resolve() if keyboard_oracle else None
        self.keyboards = {}
        self.jobs = {}
        self.job_controls = {}
        self.job_context = {}
        self.job_finalized = {}
        self.job_persisted = {}
        self.history_errors = {}
        self.worker_context = threading.local()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.mutex = threading.Lock()
        self.busy = {}  # Canonical workspace path -> exact owning job ID.
        self.target_busy = {}  # Pinned machine identity -> exact owning job ID.

    def require(self, grant):
        if grant not in self.grants:
            raise PermissionError(f'Server was not granted {grant}')

    def workspace(self, name):
        if name not in self.workspaces:
            raise ValueError('Unknown registered workspace')
        return self.workspaces[name]

    def host_file(self, value):
        if not self.files_root:
            raise PermissionError('Host file access requires a configured --files-root')
        path = (self.files_root / value).resolve()
        if not path.is_relative_to(self.files_root) or path == self.files_root:
            raise PermissionError('Host file path escapes configured files root')
        return path

    def inspect(self, name):
        path = self.workspace(name)
        runtime = self.runtimes.get(name)
        status = {'owned': bool(runtime), 'running': False if runtime else None,
                  'status': 'exited' if runtime else 'not-owned'}
        if runtime and runtime.process.poll() is None:
            try:
                status = runtime.control('query-status')
                status['owned'] = True
            except (OSError, ValueError) as exc:
                status = {'owned': True, 'running': True, 'status': 'starting-or-unavailable', 'detail': str(exc)}
        return {'workspace': name, 'path': str(path),
                'machine': read_config(path) if (path / 'machine.json').exists() else None,
                'runtime': status, 'grants': sorted(self.grants),
                'modem_worker': runtime.modem.inspect() if runtime and runtime.modem else None,
                'guest_job': runtime.active_guest_job if runtime else None,
                'guest_channel': {'status': 'uncertain' if runtime and runtime.guest_channel_error else
                                  ('no-recorded-error' if runtime and runtime.args.mode == 'maintenance' and
                                   runtime.process.poll() is None else 'unavailable'),
                                  'detail': runtime.guest_channel_error if runtime else None},
                'coverage': 'Partial CM4. Surrogate display/input/networking; no physical hardware qualification.',
                'restore_pending': (path / 'restore-pending.json').exists()}

    def load_recovery_policy(self, path, digest):
        # Keep POSIX-only recovery dependencies out of portable controller imports.
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_recovery_jobs import RecoveryJobs
        return RecoveryJobs(path, digest, self.workspaces)

    def prepare_recovery_staging(self, name, publication, publication_pin, bundle, bundle_pin, output):
        """Owner-only normal-SSH preparation; deliberately absent from MCP call()."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_recovery_stage_prepare import inputs, prepare
        frozen = inputs(publication, publication_pin, bundle, bundle_pin)
        return self.submit(name, 'recovery_prepare_staging', lambda: prepare(output, frozen),
                           target_identity=frozen['image_plan']['machine_id'], context={
                               'publication_sha256': publication_pin, 'firmware_sha256': bundle_pin,
                               'staging_authorized': False})

    def discover_recovery_builder(self, name, host):
        """Owner-only read-only discovery; omit the host address from job history."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_recovery_build import discover
        def inspect():
            observed = discover(host)
            return {key: observed[key] for key in ('boot', 'kernel')}
        return self.submit(name, 'recovery_discover_builder', inspect)

    def build_recovery_image(self, name, discovered, credentials, output):
        """Owner-only private native build, never an agent grant or publication."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        import copy
        from forge_boot_observation import normal_boot
        from forge_recovery_build import build
        frozen = copy.deepcopy(discovered)
        normal_boot(frozen['boot'], frozen['boot']['machine_id'])
        return self.submit(name, 'recovery_build_image', lambda: build(output, frozen, credentials),
                           target_identity=frozen['boot']['machine_id'], context={
                               'boot_id': frozen['boot']['boot_id'], 'publication_authorized': False,
                               'reboot_authorized': False})

    def prepare_recovery_publication(self, name, build_directory, acceptance_pin, output):
        """Owner-only draft for an already-private target; never publication."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_recovery_publication_prepare import inputs, prepare
        frozen = inputs(build_directory, acceptance_pin)
        return self.submit(name, 'recovery_prepare_publication', lambda: prepare(output, frozen),
                           target_identity=frozen['native']['boot']['machine_id'], context={
                               'build_acceptance_sha256': acceptance_pin, 'publication_authorized': False})

    def publish_recovery_image(self, name, reviewed):
        """Owner confirmation only; no client grant, selector change or reboot."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        import copy
        from forge_recovery_publication_dispatch import publish
        from forge_recovery_bootplan import digest
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_publish_image', lambda: publish(frozen),
                           target_identity=frozen['plan']['machine_id'], context={
                               'plan_sha256': digest(frozen['plan']), 'reboot_authorized': False})

    def prepare_boot_privacy(self, name, host, output):
        """Owner-only fstab backup and draft; no live policy application."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_boot_privacy_setup import prepare
        return self.submit(name, 'recovery_prepare_boot_privacy', lambda: prepare(output, host),
                           context={'target_write_authorized': False})

    def apply_boot_privacy(self, name, reviewed):
        """Owner-only fstab transition; effective mount verification is separate."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        import copy
        from forge_boot_privacy_setup import apply
        from forge_recovery_bootplan import digest
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_apply_boot_privacy', lambda: apply(frozen),
                           target_identity=frozen['boot']['machine_id'], context={
                               'plan_sha256': digest(frozen['plan']), 'reboot_authorized': False})

    def verify_boot_privacy(self, name, reviewed, *, reboot=False):
        """Owner-only fresh-mount verification with a separately confirmed reboot."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        import copy
        from forge_boot_privacy_verify import verify
        from forge_recovery_bootplan import digest
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_verify_boot_privacy', lambda: verify(frozen, reboot=reboot),
                           target_identity=frozen['boot']['machine_id'], context={
                               'plan_sha256': digest(frozen['plan']), 'reboot_authorized': reboot})

    def approve_recovery_policy(self, path, digest):
        """Local owner action only; clients cannot approve or replace policy."""
        approved = self.load_recovery_policy(path, digest)
        with self.mutex:
            if self.busy:
                raise ValueError('Wait for controller jobs before changing recovery approval')
            self.recovery_jobs = approved
            self.grants = self.grants | {'target-recovery'}

    def enroll_recovery_session(self, name, enrollment, output):
        """Local owner action only; not exposed through client call()/MCP."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_session_enrollment import prepare
        return self.submit(name, 'recovery_enroll_session', lambda: prepare(output, enrollment),
                           target_identity=enrollment.machine_id, context={
                               'staging_sha256': enrollment.staging_pin,
                               'boot_id': enrollment.boot_id, 'lease_acquired': False})

    def boot_recovery_session(self, name, enrollment, output):
        """Owner-confirmed one-shot boot from fully acknowledged staging."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_recovery_tryboot import reviewed, boot_and_enroll
        reviewed(enrollment)
        return self.submit(name, 'recovery_tryboot_enroll', lambda: boot_and_enroll(output, enrollment),
                           target_identity=enrollment.machine_id, context={
                               'staging_sha256': enrollment.staging_pin, 'root_write_authorized': False})

    def reboot_held_recovery(self, name, source, enrollment, directory, pin, output):
        """Owner-only single reboot of an acknowledged hold, then fresh enrollment."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_held_reboot import reviewed, boot_and_enroll
        reviewed(source, enrollment, directory, pin)
        return self.submit(name, 'recovery_held_reboot_enroll',
                           lambda: boot_and_enroll(output, source, enrollment, directory, pin),
                           target_identity=source.machine_id, context={
                               'plan_sha256': pin, 'root_write_authorized': False})

    def prepare_recovery_backup(self, name, source, output):
        """Owner-only backup policy authoring; approval/execution are separate."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_backup_policy import prepare
        return self.submit(name, 'recovery_prepare_backup_policy', lambda: prepare(output, source, name),
                           target_identity=source.machine_id, context={
                               'enrollment_sha256': source.acceptance_pin,
                               'session_sha256': source.session_pin, 'policy_approved': False})

    def prepare_recovery_hash(self, name, source, output):
        """Owner-only current-card hash policy; approval and execution are separate."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_backup_policy import prepare
        return self.submit(name, 'recovery_prepare_hash_policy',
                           lambda: prepare(output, source, name, operation='hash-card'),
                           target_identity=source.machine_id, context={
                               'enrollment_sha256': source.acceptance_pin,
                               'session_sha256': source.session_pin, 'policy_approved': False})

    def prepare_recovery_hold(self, name, source, reviewed, output):
        """Owner-only offline install-hold drafting; never applies or approves it."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_hold_policy import prepare
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_prepare_hold_policy', lambda: prepare(output, source, frozen, name),
                           target_identity=source.machine_id, context={
                               'session_sha256': source.session_pin, 'staging_sha256': frozen['staging_sha256'],
                               'source_manifest_sha256': frozen['manifest_sha256'], 'policy_approved': False})

    def prepare_recovery_reconciliation(self, name, source, reviewed, output):
        """Owner-only observation-policy drafting, never an old-write retry."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_reconcile_policy import prepare
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_prepare_reconciliation_policy',
                           lambda: prepare(output, source, frozen, name),
                           target_identity=source.machine_id, context={
                               'session_sha256': source.session_pin, 'plan_sha256': frozen['plan_sha256'],
                               'policy_approved': False})

    def prepare_recovery_root(self, name, source, reviewed, output, *, accept_filesystem_errors=False):
        """Owner-only root transfer drafting; approval and execution stay separate."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_root_policy import prepare
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_prepare_root_policy', lambda: prepare(output, source, frozen, name,
                           accept_filesystem_errors=accept_filesystem_errors),
                           target_identity=source.machine_id, context={
                               'session_sha256': source.session_pin, 'plan_sha256': frozen['plan_sha256'],
                               'policy_approved': False})

    def prepare_recovery_release(self, name, source, reviewed, output, *, accept_filesystem_errors=False):
        """Owner-only normal-selector draft after independent root reconciliation."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_release_policy import prepare
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_prepare_release_policy', lambda: prepare(output, source, frozen, name,
                           accept_filesystem_errors=accept_filesystem_errors),
                           target_identity=source.machine_id, context={
                               'session_sha256': source.session_pin, 'root_plan_sha256': frozen['plan_sha256'],
                               'policy_approved': False})

    def return_recovery_to_normal(self, name, source, reviewed, output, *, reboot=False):
        """Owner-only normal-return action; read-only verification never retries reboot."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_normal_return import return_to_normal
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_normal_return',
                           lambda: return_to_normal(output, source, frozen, reboot=reboot),
                           target_identity=source.machine_id, context={
                               'plan_sha256': frozen['plan_sha256'], 'reboot_requested': reboot,
                               'root_write_authorized': False})

    def cleanup_recovery(self, name, reviewed, output):
        """Owner-only reverse staging and image removal, never public FAT permissions."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_recovery_cleanup import cleanup
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_cleanup', lambda: cleanup(output, frozen),
                           target_identity=frozen['machine_id'], context={
                               'staging_sha256': frozen['staging_sha256'],
                               'public_permissions_authorized': False})

    def restore_boot_privacy(self, name, reviewed):
        """Owner-only original fstab restoration after guarded artifact cleanup."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_boot_privacy_restore import restore
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_restore_boot_privacy', lambda: restore(frozen),
                           target_identity=frozen['cleanup']['machine_id'], context={
                               'plan_sha256': frozen['plan_sha256'], 'reboot_requested': False})

    def verify_original_boot_mount(self, name, reviewed, *, reboot=False):
        """Owner-only final reboot or read-only original-permissions verification."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_boot_mount_restore import verify
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_verify_original_mount', lambda: verify(frozen, reboot=reboot),
                           target_identity=frozen['cleanup']['machine_id'], context={
                               'plan_sha256': frozen['plan_sha256'], 'reboot_requested': reboot})

    def prepare_backup_source(self, name, reviewed, output):
        """Owner-only offline preparation; never acquires recovery authority."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_backup_source import prepare
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_prepare_backup_source', lambda: prepare(output, frozen),
                           context={'backup_acceptance_sha256': frozen['acceptance_sha256'],
                                    'target_contacted': False})

    def check_backup_filesystems(self, name, reviewed, output):
        """Owner-only retained-copy checks; never repairs or contacts a target."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_backup_source import check_filesystems
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_check_backup_filesystems', lambda: check_filesystems(output, frozen),
                           context={'backup_acceptance_sha256': frozen['acceptance_sha256'],
                                    'target_contacted': False, 'repair_authorized': False})

    def prepare_export_derivative(self, name, reviewed, output):
        """Owner-only offline export lineage; no target or deployment grant."""
        import copy
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_derivative_prepare import prepare
        frozen = copy.deepcopy(reviewed)
        return self.submit(name, 'recovery_prepare_export_derivative', lambda: prepare(output, frozen),
                           context={'original_manifest_sha256': frozen['source_sha256'],
                                    'target_contacted': False})

    def check_export_root(self, name, directory, pin, output):
        """Owner-only read-only derivative check with separately pinned evidence."""
        from uconsole_emulator import require_private_image_host
        require_private_image_host()
        from forge_recovery_derivative import load
        from forge_recovery_derivative_health import inspect_root
        from forge_recovery_restore_source import digest
        directory, output = Path(directory).absolute(), Path(output).absolute()
        load(directory, pin)
        def check():
            result = inspect_root(directory, pin, output)
            return dict(result, health_sha256=digest(result), target_contacted=False, lease_acquired=False)
        return self.submit(name, 'recovery_check_export_root', check,
                           context={'derivative_manifest_sha256': pin, 'target_contacted': False,
                                    'repair_authorized': False})

    def submit_recovery(self, name, job):
        self.require('target-recovery')
        registry = self.recovery_jobs
        approved = registry.get(job, name)
        def execute():
            from forge_recovery_jobs import execute
            return execute(approved)
        return self.submit(name, 'recovery_' + approved.operation, execute,
                           target_identity=approved.machine_id, context={
                               'recovery_job': approved.name, 'policy_sha256': registry.sha256,
                               'session_sha256': approved.session_pin})

    def approve_target_policy(self, path, digest):
        """Local owner action only; deliberately absent from call()/MCP."""
        approved = TargetTransactions(path, digest, self.workspaces)
        with self.mutex:
            if self.busy:
                raise ValueError('Wait for controller jobs before changing physical-target approval')
            self.targets = approved
            self.grants = self.grants | {'target-write'}

    def submit_target(self, name, transaction, direction):
        self.require('target-write')
        approved = self.targets.get(transaction, name)
        if direction not in ('apply', 'restore'):
            raise ValueError('Target direction must be apply or restore')
        from forge_target_recovery import binding
        _, machine_id = binding(approved)

        def execute():
            # Linux target implementation is loaded only for explicit physical
            # jobs; importing the general controller must remain cross-platform.
            if approved.kind == 'service':
                from forge_target_service_dispatch import dispatch
                return dispatch(approved.journal, direction, approved.authorization_sha256)
            if approved.kind == 'recovery-stage':
                from forge_recovery_stage_dispatch import dispatch
                return dispatch(approved.journal, approved.authorization_sha256, approved.phase, direction,
                                authorize=lambda request: request if request['boot_id'] == approved.boot_id else None)
            from forge_target_ssh import dispatch
            return dispatch(approved.journal, direction, approved_plan_sha256=approved.plan_sha256)

        return self.submit(name, 'target_' + direction, execute, target_identity=machine_id, context={
            'transaction': approved.name, 'direction': direction,
            'policy_sha256': self.targets.sha256,
            **({'kind': approved.kind, 'authorization_sha256': approved.authorization_sha256,
                'phase': approved.phase, 'boot_id': approved.boot_id} if approved.kind == 'recovery-stage' else
               {'kind': 'service', 'authorization_sha256': approved.authorization_sha256}
               if approved.kind == 'service' else {'plan_sha256': approved.plan_sha256})})

    def submit_target_staging_reconcile(self, name, transaction, direction):
        self.require('target-write')
        approved = self.targets.get(transaction, name)
        if approved.kind != 'recovery-stage' or direction not in ('apply', 'restore'):
            raise ValueError('Choose an approved recovery staging phase and its attempted direction')
        from forge_target_recovery import binding
        _, machine_id = binding(approved)
        def execute():
            from forge_recovery_stage_dispatch import reconcile
            return reconcile(approved.journal, approved.authorization_sha256, approved.phase, direction)
        return self.submit(name, 'target_staging_reconcile', execute, target_identity=machine_id, context={
            'transaction': approved.name, 'direction': direction, 'phase': approved.phase,
            'authorization_sha256': approved.authorization_sha256, 'policy_sha256': self.targets.sha256})

    def submit_target_recovery(self, name, transaction):
        # Existing physical-target authority is required even for this fixed
        # read-only SSH worker; read-only attached clients cannot inherit it.
        self.require('target-write')
        approved = self.targets.get(transaction, name)
        from forge_target_recovery import binding
        _, machine_id = binding(approved)
        def execute():
            from forge_target_recovery import inspect
            return inspect(approved)
        return self.submit(name, 'target_recovery_inspect', execute, target_identity=machine_id, context={
            'transaction': approved.name, 'policy_sha256': self.targets.sha256})

    def submit(self, name, operation, function, *, cancellable=False, context=None, target_identity=None):
        path = self.workspace(name)
        if target_identity is not None and (not isinstance(target_identity, str) or
                                           not re.fullmatch('[0-9a-f]{32}', target_identity)):
            raise ValueError('Physical job requires a pinned machine identity')
        context = json.loads(json.dumps(context)) if context is not None else None
        with self.mutex:
            if path in self.busy:
                raise ValueError('Another controller job owns this workspace; poll it first')
            if target_identity is not None and target_identity in self.target_busy:
                raise ValueError('Another controller job owns this physical target; poll it first')
            if len(self.jobs) >= 128:
                # Retain active jobs and any outcome whose history commit failed.
                # Retired results remain available through job()/job_history.
                for old_id, (_, _, old_future) in list(self.jobs.items()):
                    if (old_future.done() and self.job_finalized[old_id].is_set()
                            and self.job_persisted[old_id].is_set()):
                        for mapping in (self.jobs, self.job_controls, self.job_context,
                                        self.job_finalized, self.job_persisted, self.history_errors):
                            mapping.pop(old_id, None)
                        break
                if len(self.jobs) >= 128:
                    raise ValueError('Session job limit reached (128); no safely persisted terminal '
                                     'job can be retired. Complete active jobs and inspect history failures; '
                                     'reconnecting an attached client does not reset the owner.')
            job_id = uuid.uuid4().hex
            if self.history:
                self.history.create(job_id, self.workspace(name), operation, self.session, context=context)
            self.busy[path] = job_id
            if target_identity is not None:
                self.target_busy[target_identity] = job_id
            cancel = threading.Event()
            finalized = threading.Event()
            persisted = threading.Event()

            def record(outcome):
                if self.history:
                    try:
                        self.history.record(job_id, dict(outcome, context=context) if context is not None else outcome)
                        if outcome['status'] in ('completed', 'failed', 'cancelled'):
                            persisted.set()
                    except Exception as exc:
                        raise RuntimeError('Durable job history update failed; inspect effects before retrying: '
                                           + str(exc)) from exc

            def work():
                self.worker_context.cancel = cancel
                try:
                    record({'status': 'running'})
                    try:
                        if cancel.is_set():
                            raise JobCancelled('Cancelled before execution')
                        result = function()
                    except (JobCancelled, GuestJobCancelled, GuestTransferCancelled, ReplayCancelled) as exc:
                        record({'status': 'cancelled', 'detail': str(exc)[:16384]})
                        raise
                    except BaseException as exc:
                        record({'status': 'failed', 'error': str(exc)[:16384]})
                        raise
                    record({'status': 'completed', 'result': result})
                    return result
                finally:
                    del self.worker_context.cancel
                    # Release after operation cleanup but before the executor
                    # publishes a terminal future. Polling terminal must not
                    # race a later callback that still owns this workspace.
                    self.finished(name, job_id)
                    finalized.set()

            def cancelled(done):
                if done.cancelled():
                    try:
                        record({'status': 'cancelled', 'detail': 'Cancelled before execution'})
                    except Exception as exc:
                        self.history_errors[job_id] = str(exc)
                    finally:
                        self.finished(name, job_id)
                        finalized.set()

            try:
                future = self.executor.submit(work)
            except BaseException:
                self.busy.pop(path)
                if target_identity is not None and self.target_busy.get(target_identity) == job_id:
                    del self.target_busy[target_identity]
                record({'status': 'failed', 'error': 'Worker could not be submitted; no operation started'})
                raise
            self.jobs[job_id] = (name, operation, future)
            self.job_controls[job_id] = (cancel, cancellable)
            self.job_context[job_id] = context
            self.job_finalized[job_id] = finalized
            self.job_persisted[job_id] = persisted
        # Queued cancellation does not execute work/finally. Its callback may
        # run synchronously, so register outside mutex. Owner IDs make delayed
        # or duplicate cleanup harmless to any newer job.
        future.add_done_callback(cancelled)
        return {'job_id': job_id, 'workspace': name, 'operation': operation}

    def finished(self, name, job_id):
        path = self.workspace(name)
        with self.mutex:
            if self.busy.get(path) == job_id:
                del self.busy[path]
            for machine, owner in list(self.target_busy.items()):
                if owner == job_id:
                    del self.target_busy[machine]

    def job(self, job_id):
        # Snapshot together: another client may retire this job on submission.
        with self.mutex:
            live = self.jobs.get(job_id)
            if live:
                context = self.job_context[job_id]
                finalized = self.job_finalized[job_id]
                cancel, _ = self.job_controls[job_id]
        if live is None:
            if self.history:
                return self.history.get(job_id, self.workspaces.values())
            raise ValueError('Unknown job ID')
        name, operation, future = live
        state = {'job_id': job_id, 'workspace': name, 'operation': operation}
        if context is not None:
            state['context'] = json.loads(json.dumps(context))
        if future.cancelled():
            if not finalized.is_set():
                return dict(state, status='cancelling')
            with self.mutex:
                history_error = self.history_errors.get(job_id)
            if history_error:
                return dict(state, status='failed', error=history_error)
            return dict(state, status='cancelled')
        if not future.done():
            return dict(state, status='cancelling' if cancel.is_set() else
                        ('running' if future.running() else 'queued'))
        try:
            return dict(state, status='completed', result=future.result())
        except (JobCancelled, GuestJobCancelled, GuestTransferCancelled, ReplayCancelled) as exc:
            return dict(state, status='cancelled', detail=str(exc))
        except Exception as exc:
            return dict(state, status='failed', error=str(exc)[:16384])

    def cancel(self, job_id):
        with self.mutex:
            live = self.jobs.get(job_id)
            if live:
                cancel, supported = self.job_controls[job_id]
        if live is None:
            raise ValueError('Only jobs owned by this server session can be cancelled; history is read-only')
        future = live[2]
        if future.cancel():
            return {'job_id': job_id, 'cancelled': True, 'requested': True,
                    'detail': 'Cancelled before execution'}
        if not future.done() and supported:
            cancel.set()
            return {'job_id': job_id, 'cancelled': False, 'requested': True,
                    'detail': 'Cancellation requested; poll for terminal state. Effects are not rolled back.'}
        return {'job_id': job_id, 'cancelled': False, 'requested': False,
                'detail': 'Job is terminal or does not support running cancellation.'}

    def runtime(self, name):
        runtime = self.runtimes.get(name)
        if not runtime:
            raise ValueError('Boot a guest through this server first; external VMs are not adopted')
        runtime.require_alive()
        return runtime

    def submit_boot(self, name, mode='maintenance', scenario=None, *, display='none',
                    qmp_port=4444, serial_port=4445, keyboard='generic', audio='none', adc_reference='fixed', modem='none'):
        """Shared boot job; snapshot only supported local launch settings."""
        self.require('boot')
        self.require('force-stop')
        if mode not in ('maintenance', 'normal', 'desktop') or display not in DISPLAY_BACKENDS:
            raise ValueError('Unsupported boot mode or display')
        if any(type(port) is not int or not 1 <= port <= 65535 for port in (qmp_port, serial_port)):
            raise ValueError('Boot ports must be integers in 1..65535')
        if keyboard not in ('generic', 'composite'):
            raise ValueError('Unsupported keyboard transport')
        if modem not in ('none', 'composite'):
            raise ValueError('Unsupported modem transport')
        if audio not in AUDIO_MODES:
            raise ValueError('Unsupported audio surrogate')
        if adc_reference not in ('fixed', 'missing'):
            raise ValueError('Unsupported ADC reference profile')
        context = {'mode': mode, 'display': display, 'qmp_port': qmp_port, 'serial_port': serial_port,
                   'keyboard': keyboard, 'audio': audio, 'adc_reference': adc_reference, 'modem': modem}
        if scenario:
            from dataclasses import asdict
            context.update(scenario_sha256=scenario.sha256, power=asdict(scenario.power))
        return self.submit(name, 'boot', lambda: self.boot(
            name, mode, scenario, display=display, qmp_port=qmp_port, serial_port=serial_port,
            keyboard=keyboard, audio=audio, adc_reference=adc_reference, modem=modem),
            cancellable=True, context=context)

    def boot(self, name, mode, scenario=None, *, display='none', qmp_port=4444, serial_port=4445,
             keyboard='generic', audio='none', adc_reference='fixed', modem='none'):
        self.require('boot')
        # This explicit grant also authorizes cleanup if the client disconnects
        # while a normal/desktop guest cannot be cleanly stopped from the host.
        self.require('force-stop')
        cancel = getattr(self.worker_context, 'cancel', threading.Event())

        def check_cancel():
            if cancel.is_set():
                raise JobCancelled('Boot cancelled; completed boot-artifact refresh is not rolled back.')

        check_cancel()
        path = self.workspace(name)
        previous = self.runtimes.get(name)
        if previous:
            if previous.process.poll() is None:
                raise ValueError('A guest is already running')
            if name in self.keyboards:
                self.keyboards.pop(name).close()
            previous.release()
        args = parser().parse_args(['--workspace', str(path), 'run', '--mode', mode,
                                   '--display', display, '--qmp-port', str(qmp_port),
                                   '--serial-port', str(serial_port), '--keyboard', keyboard, '--audio', audio,
                                   '--adc-reference', adc_reference, '--modem', modem])
        runtime = Runtime(args)
        try:
            runtime = runtime.start(check_cancel=check_cancel, initial_scenario=scenario)
        except BaseException:
            if runtime.process is not None:
                self.runtimes[name] = runtime
            raise
        self.runtimes[name] = runtime
        try:
            check_cancel()
            if mode == 'maintenance':
                wait_for_log(path / 'serial.log', b'root@(none):/#', runtime.process,
                             120, check_cancel=check_cancel)
            deadline = time.monotonic() + 15
            while True:
                check_cancel()
                runtime.require_alive()
                try:
                    status = runtime.control('query-status')
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    cancel.wait(0.05)
            check_cancel()
            result = {'identity': runtime.identity, 'mode': mode, 'runtime': status}
            if isinstance(getattr(runtime, 'audio_recording', None), Path):
                result['audio_recording'] = str(runtime.audio_recording)
            if scenario:
                result['scenario'] = json.loads(
                    (path / f'scenario-{runtime.identity}.json').read_text())
            return result
        except JobCancelled:
            # Never act on a previous runtime or another owner's QMP endpoint.
            # If cleanup fails, report failure and retain ownership, not a
            # falsely terminal cancellation with a live untracked process.
            if runtime.process.poll() is None:
                runtime.stop(force=True)
            else:
                runtime.release()
            raise JobCancelled('Boot cancelled; owned VM exited after forced power removal. '
                               'The guest filesystem may be unclean; no rollback is implied.')

    def lifecycle(self, name, action, arguments):
        self.require('image-write')
        path = self.workspace(name)
        result = self.process_job([sys.executable, str(ROOT / 'tools/uconsole_emulator.py'),
                                   '--workspace', str(path), action, *arguments],
                                  stopped='Image job stopped; partial artifacts/journals were retained. '
                                          'No rollback is implied.')
        if result['exit_code']:
            raise ValueError(result['stderr'] or result['stdout'])
        return result

    def submit_lifecycle(self, name, action, arguments):
        """Shared GUI/MCP image-job entrypoint.

        Caller authorizes file paths: MCP uses host_file; the local GUI uses
        explicit file selections. This does not grant arbitrary host execution.
        """
        self.require('image-write')
        if action not in ('checkpoint', 'restore', 'recover', 'refresh-boot', 'export', 'prepare', 'configure-display'):
            raise ValueError('Unknown image lifecycle operation')
        if not isinstance(arguments, (list, tuple)) or any(not isinstance(v, str) for v in arguments):
            raise ValueError('Image arguments must be a list of strings')
        arguments = tuple(arguments)
        return self.submit(name, action.replace('-', '_'),
                           lambda: self.lifecycle(name, action, arguments),
                           cancellable=True, context={'arguments': list(arguments)})

    def submit_transfer(self, name, direction, host, guest):
        """Shared transfer job; callers authorize the selected host path."""
        self.require('transfer')
        if direction not in ('upload', 'download'):
            raise ValueError('Unknown transfer direction')
        host = Path(host).resolve()
        if not isinstance(guest, str) or not guest.startswith('/') or '\x00' in guest:
            raise ValueError('Guest transfer path must be absolute without NUL')
        def action():
            runtime = self.runtime(name)
            if direction == 'upload':
                return runtime.upload(host, guest, cancel=self.worker_context.cancel)
            return runtime.download(guest, host, cancel=self.worker_context.cancel)
        return self.submit(name, direction, action, cancellable=True,
                           context={'host_path': str(host), 'guest_path': guest})

    def submit_guest(self, name, script, timeout=60):
        self.require('guest-exec')
        if not isinstance(script, str) or not 1 <= len(script) <= 2200:
            raise ValueError('Guest script must contain 1..2200 characters')
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError('Guest timeout must be an integer in 1..300 seconds')
        return self.submit(name, 'guest_exec',
                           lambda: self.runtime(name).execute(script, timeout=timeout,
                                                              cancel=self.worker_context.cancel),
                           cancellable=True,
                           context={'script_sha256': hashlib.sha256(script.encode()).hexdigest(),
                                    'timeout': timeout})

    def host_task(self, name, task_name):
        self.require('host-task')
        task = self.host_tasks.get(task_name, name)
        with WorkspaceLock(task.cwd) as lock:
            result = self.process_job(task.argv, cwd=task.cwd, timeout=task.timeout,
                                      environment=self.host_tasks.environment, owned_fds=(lock.fileno(),),
                                      stopped='Host task stopped; committed host effects are not rolled back.')
        return dict(result, task=task.name, policy_sha256=self.host_tasks.sha256,
                    argv=list(task.argv), cwd=str(task.cwd))

    def process_job(self, command, *, cwd=None, timeout=1800, environment=None, owned_fds=(), stopped):
        # A child keeps ordinary CLI output out of the MCP stdout stream.
        cancel = getattr(self.worker_context, 'cancel', threading.Event())
        if cancel.is_set():
            raise JobCancelled('Cancelled before host subprocess started')
        # File-backed output bounds Python memory even for noisy image tools.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(command, cwd=cwd, env=environment,
                                   stdout=stdout, stderr=stderr,
                                   start_new_session=True, pass_fds=owned_fds)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() >= deadline:
                    # Only this job's process group, never the workspace VM.
                    signal_owned_group(process.pid, signal.SIGTERM)
                    # Do not poll/wait (reap) the leader before our last group
                    # signal. Even if TERM exits it immediately, its unreaped
                    # PID pins this PGID against reuse by an unrelated job.
                    # A fixed grace period also covers TERM-ignoring children
                    # after their launcher exits, on both Linux and macOS.
                    threading.Event().wait(10)
                    signal_owned_group(process.pid, signal.SIGKILL)
                    process.wait()
                    if cancel.is_set():
                        raise JobCancelled(stopped)
                    raise TimeoutError(stopped)
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass

            def tail(stream):
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 16384))
                return stream.read(16384).decode(errors='replace')

            output, error = tail(stdout), tail(stderr)
            return {'stdout': output, 'stderr': error, 'exit_code': process.returncode}

    def screenshot(self, name, target):
        if target.exists():
            raise FileExistsError(target)
        with tempfile.TemporaryDirectory(prefix='.forge-capture-', dir=target.parent) as directory:
            image = Path(directory) / 'capture.png'
            self.runtime(name).control('screendump', {'filename': str(image), 'format': 'png'})
            os.link(image, target)
        return {'host_path': str(target), 'sha256': sha256(target), 'bytes': target.stat().st_size}

    def replay_power(self, name, replay, evidence):
        runtime = self.runtime(name)
        return run_recorded(replay, runtime, self.worker_context.cancel, evidence)

    def power_operation(self, name, changes, evidence):
        runtime = self.runtime(name)
        with evidence.open('x') as stream:
            def record(value):
                stream.write(json.dumps(value) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            record({'runtime_identity': runtime.identity,
                    'requested': changes, 'operation': 'set' if changes else 'query'})
            parent = os.open(evidence.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            try:
                if changes:
                    field, value = next(iter(changes.items()))
                    result = change_power(runtime.control, field, value, record=record)
                else:
                    result = query_power(runtime.control)
            except Exception as exc:
                record({'status': 'failed', 'error': str(exc), 'rollback': False})
                raise
            record({'status': 'completed', 'result': result})
        return result

    def submit_modem(self, name, changes=None, *, connected=None):
        from forge_modem_at import ModemState
        allowed = {'sim', 'radio', 'registration', 'rssi', 'ber'}
        if connected is not None:
            self.require('device-control')
            if type(connected) is not bool or changes is not None:
                raise ValueError('Specify only a boolean modem connection change')
        if changes is not None:
            self.require('device-control')
            if not isinstance(changes, dict) or not changes or not set(changes) <= allowed:
                raise ValueError('Specify supported modem scenario fields')
            changes = dict(changes)
            ModemState(**changes)  # Validate types/ranges before queueing any mutation.
        evidence = self.workspace(name) / f'modem-event-{uuid.uuid4().hex}.jsonl'
        def action():
            runtime = self.runtime(name)
            if runtime.modem is None:
                raise ValueError('Boot an owned composite modem first')
            with evidence.open('x') as stream:
                def record(value):
                    stream.write(json.dumps(value)+'\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                record(dict(state='dispatch', runtime_identity=runtime.identity,
                            requested={'connected':connected} if connected is not None else changes))
                parent = os.open(evidence.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
                try:
                    if connected is not None:
                        result = runtime.modem.connect(connected)
                    else:
                        result = runtime.modem.query() if changes is None else runtime.modem.change(changes)
                except Exception as exc:
                    record(dict(state='failed', error=str(exc), rollback=False))
                    raise
                record(dict(state='acknowledged', result=result))
                return result
        operation = 'modem_connection' if connected is not None else ('modem_query' if changes is None else 'modem_set')
        return self.submit(name, operation, action,
                           context={'changes': changes, 'connected':connected, 'evidence': str(evidence)})

    def submit_audio(self, name, connected=None):
        if connected is not None:
            self.require('device-control')
            if type(connected) is not bool:
                raise ValueError('Audio connected state must be boolean')
        from forge_audio import operation
        evidence = self.workspace(name) / f'audio-event-{uuid.uuid4().hex}.jsonl'
        return self.submit(name, 'audio_query' if connected is None else 'audio_set',
                           lambda: operation(self.runtime(name), evidence, connected),
                           context={'connected': connected, 'evidence': str(evidence)})

    def runstate(self, name, running):
        runtime = self.runtime(name)
        before = runtime.control('query-status')
        if before.get('status') not in ('running', 'paused'):
            raise ValueError('Pause/resume requires a running or paused owned VM')
        runtime.control('cont' if running else 'stop')
        observed = runtime.control('query-status')
        expected = 'running' if running else 'paused'
        if observed.get('running') is not running or observed.get('status') != expected:
            raise ValueError(f'VM state readback did not confirm {expected}; effects are not rolled back')
        return {'before': before, 'observed': observed}

    def submit_replay(self, name, replay):
        """Submit a validated schedule snapshot from GUI or MCP."""
        self.require('device-control')
        if not isinstance(replay, Replay):
            raise ValueError('Power replay requires a validated schedule snapshot')
        evidence = self.workspace(name) / f'power-replay-{uuid.uuid4().hex}.jsonl'
        return self.submit(name, 'power_replay', lambda: self.replay_power(name, replay, evidence),
                           cancellable=True,
                           context={'source_sha256': replay.sha256, 'evidence_path': str(evidence),
                                    'clock': 'host-monotonic', 'event_count': len(replay.events)})

    def submit_keyboard(self, name, commands, *, timeout=5):
        self.require('device-control')
        commands = commands_snapshot(commands)
        if type(timeout) is not int or not 1 <= timeout <= 30:
            raise ValueError('Keyboard timeout must be an integer in 1..30')
        if self.keyboard_oracle is None:
            raise ValueError('Owner has not configured a firmware oracle')
        evidence = self.workspace(name) / f'keyboard-event-{uuid.uuid4().hex}.json'

        def action():
            runtime = self.runtime(name)
            bridge = self.keyboards.get(name)
            if bridge is None:
                bridge = KeyboardBridge(runtime, self.keyboard_oracle)
                self.keyboards[name] = bridge
            if bridge.identity != runtime.identity:
                raise ValueError('Keyboard bridge belongs to a different runtime')
            with evidence.open('x') as destination:
                try:
                    return bridge.apply(commands, timeout=timeout)
                finally:
                    json.dump(bridge.last_attempt, destination, indent=2)
                    destination.write('\n')

        return self.submit(name, 'keyboard_input', action,
                           context={'commands': commands, 'timeout': timeout,
                                    'evidence_path': str(evidence)})

    def stop_runtime(self, name, *, force=False):
        self.runtime(name).stop(force=force)
        bridge = self.keyboards.pop(name, None)
        if bridge is not None:
            bridge.close()
        return {'stopped': True}

    def call(self, tool, args):
        if tool == 'job_status':
            return self.job(args['job_id'])
        if tool == 'job_cancel':
            return self.cancel(args['job_id'])
        name = args['workspace']
        self.workspace(name)
        if tool == 'recovery_jobs':
            return {'jobs': self.recovery_jobs.describe(name) if self.recovery_jobs else [],
                    'execution_granted': 'target-recovery' in self.grants}
        if tool == 'recovery_job':
            return self.submit_recovery(name, args['job'])
        if tool == 'target_transactions':
            return {'transactions': self.targets.describe(name) if self.targets else [],
                    'execution_granted': 'target-write' in self.grants}
        if tool == 'target_transition':
            return self.submit_target(name, args['transaction'], args['direction'])
        if tool == 'target_recovery_inspect':
            return self.submit_target_recovery(name, args['transaction'])
        if tool == 'target_staging_reconcile':
            return self.submit_target_staging_reconcile(name, args['transaction'], args['direction'])
        if tool == 'host_tasks':
            return {'tasks': self.host_tasks.describe(name) if self.host_tasks else [],
                    'execution_granted': 'host-task' in self.grants}
        if tool == 'job_history':
            limit = args.get('limit', 20)
            items = self.history.recent(self.workspace(name), limit, args.get('before')) if self.history else []
            return {'jobs': items, 'enabled': self.history is not None,
                    'next_before': items[-1]['job_id'] if items and len(items) == limit else None}
        if tool == 'workspace_inspect':
            return self.inspect(name)
        if tool == 'keyboard_input':
            return self.submit_keyboard(name, args['commands'], timeout=args.get('timeout', 5))
        if tool == 'audio_query':
            return self.submit_audio(name)
        if tool == 'modem_set':
            return self.submit_modem(name, {key: value for key, value in args.items() if key != 'workspace'})
        if tool == 'modem_query':
            return self.submit_modem(name)
        if tool == 'modem_connection':
            if type(args.get('connected')) is not bool:
                raise ValueError('Modem connected state must be boolean')
            return self.submit_modem(name, connected=args['connected'])
        if tool == 'audio_set':
            if type(args.get('connected')) is not bool:
                raise ValueError('Audio connected state must be boolean')
            return self.submit_audio(name, args['connected'])
        if tool == 'boot':
            self.require('boot')
            self.require('force-stop')
            from forge_scenario import Scenario
            scenario = (Scenario.load(self.host_file(args['scenario_path']))
                        if 'scenario_path' in args else None)
            return self.submit_boot(name, args.get('mode', 'maintenance'), scenario,
                                    keyboard=args.get('keyboard', 'generic'), audio=args.get('audio', 'none'),
                                    adc_reference=args.get('adc_reference', 'fixed'),
                                    modem=args.get('modem', 'none'))
        elif tool == 'power_replay':
            self.require('device-control')
            replay = Replay.load(self.host_file(args['schedule_path']))
            return self.submit_replay(name, replay)
        elif tool in ('pause', 'resume'):
            self.require('boot')
            action = lambda: self.runstate(name, tool == 'resume')
        elif tool == 'power_query':
            changes = None
            evidence = self.workspace(name) / f'power-event-{uuid.uuid4().hex}.jsonl'
            action = lambda: self.power_operation(name, changes, evidence)
        elif tool == 'power_set':
            self.require('device-control')
            changes = {key: value for key, value in args.items() if key != 'workspace'}
            if len(changes) != 1:
                raise ValueError('Specify exactly one power field per change')
            field, value = next(iter(changes.items()))
            validate_value(field, value)
            evidence = self.workspace(name) / f'power-event-{uuid.uuid4().hex}.jsonl'
            action = lambda: self.power_operation(name, changes, evidence)
        elif tool == 'host_task':
            self.require('host-task')
            approved = self.host_tasks.get(args['task'], name)
            action = lambda: self.host_task(name, args['task'])
        elif tool == 'stop':
            self.require('boot')
            if args.get('force', False):
                self.require('force-stop')
            action = lambda: self.stop_runtime(name, force=args.get('force', False))
        elif tool == 'guest_exec':
            return self.submit_guest(name, args['script'], args.get('timeout', 60))
        elif tool in ('upload', 'download'):
            self.require('transfer')
            host = self.host_file(args['host_path'])
            return self.submit_transfer(name, tool, host, args['guest_path'])
        elif tool == 'screenshot':
            self.require('transfer')
            target = self.host_file(args['host_path'])
            action = lambda: self.screenshot(name, target)
        elif tool in ('checkpoint', 'restore', 'recover', 'refresh_boot', 'export', 'prepare', 'configure_display'):
            self.require('image-write')
            operation = tool.replace('_', '-')
            arguments = []
            if tool in ('checkpoint', 'restore'):
                arguments = [args['name']]
            elif tool == 'export':
                arguments = [str(self.host_file(args['host_path']))]
            elif tool == 'prepare':
                arguments = [str(self.host_file(args['host_path'])), '--sha256', args['sha256']]
            return self.submit_lifecycle(name, operation, arguments)
        else:
            raise ValueError('Unknown tool')
        operation = f'host_task:{args["task"]}' if tool == 'host_task' else tool
        context = ({'policy_sha256': self.host_tasks.sha256, 'task': approved.name,
                    'argv': list(approved.argv), 'cwd': str(approved.cwd), 'timeout': approved.timeout}
                   if tool == 'host_task' else None)
        if tool in ('power_set', 'power_query'):
            context = {'requested': changes, 'evidence_path': str(evidence)}
        if tool in ('pause', 'resume'):
            context = {'requested_running': tool == 'resume'}
        return self.submit(name, operation, action, context=context, cancellable=tool in (
            'boot', 'guest_exec', 'upload', 'download', 'checkpoint', 'restore', 'recover',
            'refresh_boot', 'export', 'prepare', 'host_task', 'power_replay'))

    def close(self):
        # Finish accepted mutations before releasing their locks. Running
        # cancellation is deliberately not represented as a successful rollback.
        self.executor.shutdown(wait=True, cancel_futures=True)
        try:
            bridge_errors = []
            for bridge in self.keyboards.values():
                try:
                    bridge.close()
                except Exception as exc:
                    bridge_errors.append(str(exc))
            for runtime in self.runtimes.values():
                if runtime.process.poll() is None:
                    try:
                        runtime.stop()
                    except Exception:
                        if 'force-stop' in self.grants:
                            runtime.stop(force=True)
                else:
                    runtime.release()
            if bridge_errors:
                raise RuntimeError('Firmware oracle cleanup failed: ' + '; '.join(bridge_errors))
        finally:
            if self.history:
                self.history.close()
