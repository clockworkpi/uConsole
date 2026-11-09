"""Bind a verified native build to an already-private target; draft only."""
import copy
import os
from pathlib import Path
import re
import uuid

from forge_boot_observation import capture as capture_boot, normal_boot
from forge_recovery_bootplan import digest
from forge_recovery_image import parameters, verified
from forge_recovery_journal import prepare as prepare_plan, inspect_operation
from forge_recovery_ssh import transport
from forge_target_backup import capture as capture_files, verify
from forge_target_journal import private_directory, read_record, write_record


def inputs(build_directory, acceptance_pin):
    """Freeze a completed owner build and verify its private local image bytes."""
    directory = Path(build_directory).absolute()
    fd = private_directory(directory)
    try:
        if os.path.lexists(directory/'failure.json'):
            raise ValueError('Failed build cannot authorize publication preparation')
        accepted = read_record(fd, 'acceptance.json')
        native = read_record(fd, 'native-build.json')
        request = read_record(fd, 'request.json')
        if (not isinstance(acceptance_pin, str) or not re.fullmatch('[0-9a-f]{64}', acceptance_pin)
                or digest(accepted) != acceptance_pin or accepted.get('status') != 'built-not-published'
                or native.get('status') != accepted['status']
                or any(accepted.get(key) is not False for key in ('boot_files_written', 'publication_performed',
                    'reboot_performed', 'boot_qualified', 'root_write_authorized', 'automatic_retry_performed'))
                or accepted.get('contains_private_credentials') is not True
                or accepted.get('target_scratch_created') is not True):
            raise ValueError('Expected pinned completed private build, not boot qualification')
        token = request.get('token')
        if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
            raise ValueError('Invalid native build token')
        parameters(accepted.get('sha256'), accepted.get('size'), token)
        scratch = '/var/tmp/uconsole-forge-build-'+token
        if (request.get('target_directory') != scratch or native.get('image') != scratch+'/build/recovery.img'
                or native.get('token') != token or native.get('boot') != request.get('boot')
                or native.get('kernel') != request.get('kernel')
                or native.get('module_sha256') != request.get('module_sha256')
                or any(native.get(key) != accepted.get(key) for key in ('status', 'kernel', 'sha256', 'size',
                    'contains_private_credentials', 'target_scratch_created', 'boot_files_written',
                    'publication_performed', 'reboot_performed'))
                or any(request.get(key) is not False for key in ('boot_files_written', 'publication_authorized', 'reboot_authorized'))):
            raise ValueError('Native build provenance differs from its owner request')
        normal_boot(native['boot'], native['boot']['machine_id'])
        image = os.open('recovery.img', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            verified(image, accepted['sha256'], accepted['size'])
        finally:
            os.close(image)
        return dict(build_directory=str(directory), acceptance_sha256=acceptance_pin,
                    request=request, native=native, acceptance=accepted)
    finally:
        os.close(fd)


def prepare(output, frozen):
    """Read target policy and absence, retain a plan; never publish or chmod."""
    frozen = copy.deepcopy(frozen)
    if inputs(frozen['build_directory'], frozen['acceptance_sha256']) != frozen:
        raise ValueError('Build changed after owner review')
    request, native = frozen['request'], frozen['native']
    host, machine = request['host'], native['boot']['machine_id']
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    fd = private_directory(output)
    try:
        write_record(fd, 'request.json', dict(build_directory=frozen['build_directory'],
            acceptance_sha256=frozen['acceptance_sha256'], publication_authorized=False))
        before = normal_boot(capture_boot(host), machine)
        capture_files(host, ['/etc/fstab'], output/'fstab.json')
        backup = read_record(fd, 'fstab.json')
        verify(backup, ['/etc/fstab'])
        if backup['machine_id'] != machine or backup['files'][0]['kind'] != 'file':
            raise ValueError('Target fstab identity differs')
        token = uuid.uuid4().hex
        plan = dict(schema=2, kind='private-recovery-image', host=host, machine_id=machine,
            fstab_sha256=backup['files'][0]['sha256'], boot_source='/dev/mmcblk0p1',
            source=native['image'], destination='/boot/firmware/forge-recovery-'+token+'.img',
            sha256=native['sha256'], size=native['size'], stage_token=token, preimage=dict(kind='absent'))
        prepared = prepare_plan(output/'publication', plan)
        observed = inspect_operation(prepared['journal'], prepared['plan_sha256'], transport)
        if (observed['policy'].get('valid') is not True
                or observed['files'].get('parent_private') is not True
                or any(observed['files'].get(key, {}).get('state') != 'absent' for key in ('destination', 'scratch'))):
            raise ValueError('Publication requires verified private boot policy and absent destination/scratch; nothing published')
        after = normal_boot(capture_boot(host), machine)
        if after != before or observed['boot_id'] != before['boot_id']:
            raise ValueError('Normal boot changed during publication preparation')
        if inputs(frozen['build_directory'], frozen['acceptance_sha256']) != frozen:
            raise ValueError('Build evidence changed during preparation')
        write_record(fd, 'owner-review.json', dict(plan=plan, **prepared, boot=before))
        result = dict(status='prepared-not-published', plan_sha256=prepared['plan_sha256'],
            image_sha256=native['sha256'], image_size=native['size'], boot_id=before['boot_id'],
            publication_performed=False, reboot_performed=False, boot_qualified=False)
        write_record(fd, 'acceptance.json', result)
        return result
    except BaseException as exc:
        write_record(fd, 'failure.json', dict(error_type=type(exc).__name__, publication_performed=False,
            reboot_performed=False, preserve_journals=True))
        raise
    finally:
        os.close(fd)
