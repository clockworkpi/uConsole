"""Internal physical root restore orchestration; no public dispatch entrypoint.

Installed-owner hooks provide a bounded transport, journaled owner approval
(including source health review), and a live acknowledged lease/watchdog budget.
They must never be deserialized client callbacks. No boot release or reboot is
performed, including on failure. An incomplete effect remains fenced in RAM.
"""
import copy
import math

from forge_recovery_bootcommit import ensure_lock_directory, mounted_boot, commit_timer
from forge_recovery_bootplan import digest
from forge_recovery_commit_inspect import inspect_files
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_claim import claim_restore_root
from forge_recovery_restore_chunks import RootChunkWriter
from forge_recovery_restore_identity import checker
from forge_recovery_operation_contract import check_evidence
from forge_recovery_restore_protocol import receive
import forge_recovery_restore_ledger as ledger
from forge_target_ssh import target_lock


def execute(plan, pin, inputs, attempt, source, *, approve, live_budget,
            acknowledge, progress=None, unmounted=None, finish_input=None):
    """Restore only after fresh guards; publish completion after claim exit.

    source must enforce transport deadlines on readline/read. approve receives
    phase, pin and frozen binding, and must explicitly approve this root restore,
    renew the lease and retain that acknowledged renewal for live_budget().
    Inspection runs with renewal paused while boot is mounted; unmounted signals
    that renewal can resume. Progress and chunk acknowledgements may renew the
    lease, but cannot override local identity, range or source verification.
    finish_input, when supplied, requests the host's input half-close only after
    all long readback/protected-range work. EOF is still mandatory before the
    durable receipt; no callback may manufacture it.
    """
    plan, inputs = copy.deepcopy((plan, inputs))
    ledger.request(plan, pin, attempt)
    source_kind, manifest = check_evidence(plan, pin, inputs)
    if (any(not callable(callback) for callback in (approve, live_budget, acknowledge)) or
            any(callback is not None and not callable(callback)
                for callback in (progress, unmounted, finish_input))):
        raise ValueError('Expected installed owner restore hooks')
    binding = plan['binding']
    check = checker(binding)

    def identity():
        if not exact(check(), binding['extent']):
            raise ValueError('Restore identity or layout changed')

    def budget(minimum):
        remaining = live_budget()
        if (type(remaining) not in (int, float) or not math.isfinite(remaining) or
                not minimum <= remaining <= 300):
            raise ValueError('Insufficient verified restore lease/watchdog budget')

    def approval(phase):
        expected = dict(restore=pin, boot_id=binding['boot_id'], phase=phase)
        if not exact(approve(phase, pin, copy.deepcopy(binding)), expected):
            raise ValueError('Explicit owner restore approval differs')
        identity()

    def acquire(expected_pin, boot_id):
        if (expected_pin, boot_id) != (pin, binding['boot_id']):
            raise ValueError('Writable claim binding differs')
        approval('acquire')
        budget(90)
        return dict(restore=pin, boot_id=binding['boot_id'])

    def effect():
        guards = dict(root=plan['root_before'], prefix=plan['prefix_guard'], suffix=plan['suffix_guard'])
        with claim_restore_root(binding['device'], binding['extent'], guards,
                                pin=pin, boot_id=binding['boot_id'], check=check,
                                authorize=acquire, progress=progress) as claim:
            approval('inspect')
            budget(180)
            with commit_timer(120):
                with mounted_boot(binding['device']+'p1', attempt, read_only=True) as point:
                    held = inspect_files(inputs['hold_plan'], point)
                    expected = {key: inputs['hold_inspection'][key]
                                for key in ('status', 'files', 'image', 'stage')}
                    if not exact(held, expected) or held['status'] != 'after':
                        raise ValueError('Persistent recovery hold dependencies changed')
            identity()
            if unmounted is not None:
                unmounted()
            claim.verify_guards(dict(prefix=plan['prefix_guard']), progress=progress)
            approval('write')
            budget(90)

            class GuardedWriter(RootChunkWriter):
                def apply(self, chunk, data):
                    # Check after receiving the chunk, immediately before I/O.
                    identity()
                    budget(30)
                    return super().apply(chunk, data)

            writer = GuardedWriter(claim.root_fd, manifest, plan['source_manifest_sha256'],
                                   expected_kind=source_kind)
            wire_binding = dict(plan_sha256=pin, manifest_sha256=plan['source_manifest_sha256'],
                                attempt=attempt, boot_id=binding['boot_id'])

            def final_progress(done, total):
                if progress is not None:
                    progress('restored-root', done, total)

            result = receive(source, writer, wire_binding, acknowledge, progress=final_progress,
                             defer_eof=finish_input is not None, expected_kind=source_kind)['result']
            identity()
            budget(30)
        # Successful context exit verifies protected bytes and closes the root
        # claim. Never publish a completion while those checks are outstanding.
        identity()
        if finish_input is not None:
            finish_input()
            if source.read(1) != b'':
                raise ValueError('Unexpected bytes after restore control-channel completion')
            identity()
        return dict(status='root-restore-verified', plan_sha256=pin,
                    source_manifest_sha256=plan['source_manifest_sha256'], boot_id=binding['boot_id'],
                    root=plan['root_after'], prefix=plan['prefix_guard'], suffix=plan['suffix_guard'],
                    bytes_written=result['bytes_written'], protected_ranges_verified=True,
                    boot_unmounted=True, normal_boot_release_authorized=False,
                    physical_restore_qualified=False)

    identity()
    ensure_lock_directory()
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        identity()
        directory = ledger.provision(binding['boot_id'])
        return ledger.run(directory, plan, pin, attempt, effect)
