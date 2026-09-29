"""One owner-confirmed private publication; never selectors, reboot or retry."""
import copy
import fcntl
import os
from pathlib import Path

from forge_boot_observation import capture, normal_boot
from forge_recovery_bootplan import digest
from forge_recovery_journal import dispatch, inspect_operation, validate
from forge_recovery_publication_prepare import inputs as build_inputs
from forge_recovery_ssh import transport
from forge_target_journal import private_directory, read_record, write_record, events


def inputs(directory, acceptance_pin):
    directory = Path(directory).absolute()
    fd = private_directory(directory)
    try:
        if os.path.lexists(directory/'failure.json') or os.path.lexists(directory/'publish-attempt'):
            raise ValueError('Failed preparation or existing publication attempt requires inspection, never replay')
        accepted = read_record(fd, 'acceptance.json')
        reviewed = read_record(fd, 'owner-review.json')
        request = read_record(fd, 'request.json')
        if (digest(accepted) != acceptance_pin or accepted.get('status') != 'prepared-not-published'
                or any(accepted.get(key) is not False for key in ('publication_performed', 'reboot_performed', 'boot_qualified'))):
            raise ValueError('Expected pinned completed publication preparation')
        journal = directory/'publication'
        child = private_directory(journal)
        try:
            plan = validate(read_record(child, 'plan.json'))
            if events(child):
                raise ValueError('Publication already has operation history; never replay')
        finally:
            os.close(child)
        pin = digest(plan)
        boot = normal_boot(reviewed['boot'], plan['machine_id'])
        if (reviewed.get('journal') != str(journal) or reviewed.get('plan') != plan
                or reviewed.get('plan_sha256') != pin or accepted.get('plan_sha256') != pin
                or accepted.get('boot_id') != boot['boot_id'] or accepted.get('image_sha256') != plan['sha256']
                or accepted.get('image_size') != plan['size'] or request.get('publication_authorized') is not False):
            raise ValueError('Publication review differs from its pinned plan')
        build = build_inputs(request['build_directory'], request['acceptance_sha256'])
        native = build['native']
        if (plan['host'] != build['request']['host'] or plan['machine_id'] != native['boot']['machine_id']
                or plan['source'] != native['image'] or plan['sha256'] != native['sha256'] or plan['size'] != native['size']):
            raise ValueError('Publication source differs from verified build')
        return dict(directory=str(directory), acceptance_sha256=acceptance_pin,
                    plan=plan, boot=boot, build=build)
    finally:
        os.close(fd)


def inspect_private(observed, boot, state):
    if (observed.get('boot_id') != boot['boot_id'] or observed.get('policy', {}).get('valid') is not True
            or observed.get('files', {}).get('parent_private') is not True
            or observed['files'].get('destination', {}).get('state') != state
            or observed['files'].get('scratch', {}).get('state') != 'absent'):
        raise ValueError('Private publication state or boot differs; preserve journals')


def publish(frozen):
    frozen = copy.deepcopy(frozen)
    directory = Path(frozen['directory'])
    fd = private_directory(directory)
    out = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if inputs(directory, frozen['acceptance_sha256']) != frozen:
            raise ValueError('Publication inputs changed after owner confirmation')
        os.mkdir('publish-attempt', 0o700, dir_fd=fd)
        os.fsync(fd)
        out = private_directory(directory/'publish-attempt')
        plan, boot = frozen['plan'], frozen['boot']
        pin, journal = digest(plan), directory/'publication'
        started = False
        try:
            write_record(out, 'request.json', dict(plan_sha256=pin, boot_id=boot['boot_id'],
                publication_authorized=True, reboot_authorized=False, automatic_retry_authorized=False))
            if normal_boot(capture(plan['host']), plan['machine_id']) != boot:
                raise ValueError('Normal boot changed since owner review; no publication attempted')
            before = inspect_operation(journal, pin, transport)
            write_record(out, 'before.json', before)
            inspect_private(before, boot, 'absent')
            # dispatch persists its own intent before the SSH effect; uncertainty
            # must never be converted into an automatic retry by this wrapper.
            started = True
            reply = dispatch(journal, 'apply', pin, transport)
            write_record(out, 'acknowledgement.json', reply)
            after = inspect_operation(journal, pin, transport)
            write_record(out, 'after.json', after)
            inspect_private(after, boot, 'matching-bytes')
            if normal_boot(capture(plan['host']), plan['machine_id']) != boot:
                raise ValueError('Boot changed after publication; preserve the acknowledged operation')
            result = dict(status='published-not-boot-qualified', plan_sha256=pin, image_sha256=plan['sha256'],
                image_size=plan['size'], publication_performed=True, reboot_performed=False,
                boot_qualified=False, root_write_authorized=False, automatic_retry_performed=False)
            write_record(out, 'acceptance.json', result)
            return result
        except BaseException as exc:
            write_record(out, 'failure.json', dict(error_type=type(exc).__name__,
                publication_may_have_started=started, preserve_journals=True,
                reboot_performed=False, automatic_retry_performed=False))
            raise
    finally:
        if out is not None:
            os.close(out)
        os.close(fd)
