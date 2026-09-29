"""Owner-only fresh RAM-session enrollment; no lease acquisition or target writes."""
from dataclasses import dataclass, replace
import os
from pathlib import Path

from forge_ram_transport import RecoveryProbe
from forge_ram_boot_observation import capture
from forge_recovery_lease_launcher import binding as boot_binding
from forge_recovery_session import binding, prepare as prepare_session
from forge_recovery_stage_review import load
from forge_target_journal import private_directory, write_record


@dataclass(frozen=True)
class Enrollment:
    staging: Path
    staging_pin: str
    probe: RecoveryProbe
    boot_id: str | None
    tryboot: int
    owner: str
    machine_id: str


def inputs(staging, pin, host, key, known_hosts, kernel, serial, boot_id, tryboot):
    """Validate all owner choices locally, before any SSH or host output."""
    staging = Path(staging).absolute()
    reviewed = load(staging, pin)
    if type(tryboot) is not int or tryboot not in (0, 1):
        raise ValueError('Select explicit tryboot (1) or persistent recovery (0)')
    if boot_id == reviewed['original_boot']['boot_id']:
        raise ValueError('Enrollment requires a new recovery boot, not the staged normal boot')
    request = reviewed['request']
    probe = RecoveryProbe(host, key, known_hosts, request['nonce'], kernel, serial)
    if boot_id is not None:
        binding(probe, boot_id, request['lease_owner'])
    return Enrollment(staging, pin, probe, boot_id, tryboot, request['lease_owner'],
                      reviewed['acceptance']['machine_id'])


def summary(value):
    return dict(host=value.probe.host, port=value.probe.port, kernel=value.probe.kernel,
                serial=value.probe.serial, boot_id=value.boot_id, tryboot=value.tryboot,
                machine_id=value.machine_id, staging_sha256=value.staging_pin,
                lease_acquired=False, target_written=False, policy_approved=False)


def prepare(output, value):
    """Keep evidence and a single enrollment claim even if observation fails.

    Never adopt an existing lease or guess its sequence. A completed enrollment
    is only a local binding; its first later approved job must acquire the lease.
    """
    # Revalidate immutable source and pinned credentials after queueing.
    reviewed = load(value.staging, value.staging_pin)
    if (reviewed['request']['lease_owner'] != value.owner or
            reviewed['acceptance']['machine_id'] != value.machine_id):
        raise ValueError('Staging owner or target changed before enrollment')
    value.probe._argv('true')  # Local credential recheck; never executed.
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    fd = private_directory(output)
    try:
        before = None
        if value.boot_id is None:
            # Discovery is read-only and precedes any lease or job approval.
            # Once observed, pin this exact UUID for every subsequent check.
            write_record(fd, 'discovery-request.json', summary(value))
            before = value.probe.inspect()
            write_record(fd, 'discovery.json', before)
            if boot_binding(before['observation']) != (value.probe.nonce, value.owner, 'physical'):
                raise ValueError('Recovery boot lease owner differs from sealed staging')
            observed_boot = before['verification']['boot_id']
            if observed_boot == reviewed['original_boot']['boot_id']:
                raise ValueError('Enrollment requires a new recovery boot, not the staged normal boot')
            value = replace(value, boot_id=observed_boot)
        stage_fd = private_directory(value.staging)
        try:
            write_record(stage_fd, 'session-enrollment-'+value.boot_id+'.json',
                         dict(output=str(output), staging_sha256=value.staging_pin,
                              boot_id=value.boot_id, automatic_retry_authorized=False))
        finally:
            os.close(stage_fd)
        write_record(fd, 'request.json', dict(summary(value),
                     binding=binding(value.probe, value.boot_id, value.owner)))
        if before is None:
            before = value.probe.inspect(expected_boot_id=value.boot_id)
        write_record(fd, 'before.json', before)
        if boot_binding(before['observation']) != (value.probe.nonce, value.owner, 'physical'):
            raise ValueError('Recovery boot lease owner differs from sealed staging')
        selection = capture(value.probe, boot_id=value.boot_id, expected_tryboot=value.tryboot)
        write_record(fd, 'selection.json', selection)
        after = value.probe.inspect(expected_boot_id=value.boot_id)
        write_record(fd, 'after.json', after)
        if boot_binding(after['observation']) != (value.probe.nonce, value.owner, 'physical'):
            raise ValueError('Recovery boot lease owner changed during enrollment')
        session = prepare_session(output/'session', value.probe, value.boot_id, value.owner)
        result = dict(summary(value), status='enrolled-not-leased',
                      binding_sha256=session['binding_sha256'], reboot_performed=False,
                      root_write_authorized=False, normal_boot_release_authorized=False)
        write_record(fd, 'acceptance.json', result)
        return result
    except BaseException as exc:
        write_record(fd, 'failure.json', dict(error=type(exc).__name__+': '+str(exc),
                     target_written=False, lease_acquired=False, automatic_retry_performed=False))
        raise
    finally:
        os.close(fd)
