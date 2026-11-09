"""Back up normal-SSH boot preimages and author recovery staging; never deploy."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

from forge_boot_observation import capture as capture_boot, normal_boot
from forge_recovery_bootplan import digest
from forge_recovery_hold import compile_hold
from forge_recovery_journal import history_checked, inspect_operation, validate as validate_publication
from forge_recovery_ssh import transport
from forge_target_backup import capture as capture_files, verify
from forge_target_files import equivalent
from forge_target_journal import events, private_directory, read_record, write_record
from forge_trial_firmware import paths, prepare_recovery


def pinned_record(filename, pin):
    filename = Path(filename).absolute()
    if not isinstance(pin, str) or not re.fullmatch('[0-9a-f]{64}', pin):
        raise ValueError('Explicit canonical journal SHA-256 required')
    fd = private_directory(filename.parent)
    try:
        value = read_record(fd, filename.name)
    finally:
        os.close(fd)
    if digest(value) != pin:
        raise ValueError('Selected record differs from owner-approved digest')
    return value


def inputs(publication, publication_pin, bundle, bundle_pin):
    """Freeze local owner inputs before SSH or output-directory creation."""
    publication = Path(publication).absolute()
    plan = validate_publication(pinned_record(publication/'plan.json', publication_pin))
    if plan['schema'] != 2 or plan['boot_source'] != '/dev/mmcblk0p1':
        raise ValueError('Preparation requires a current CM4 private-image publication plan')
    firmware = pinned_record(bundle, bundle_pin)
    return dict(publication=str(publication), publication_sha256=publication_pin,
                image_plan=plan, firmware_bundle=firmware, firmware_sha256=bundle_pin)


def published(value):
    directory, pin, plan = value['publication'], value['publication_sha256'], value['image_plan']
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if read_record(fd, 'plan.json') != plan or digest(plan) != pin:
            raise ValueError('Publication changed after input review')
        if history_checked(events(fd), plan, pin, fd) != 'apply':
            raise ValueError('Recovery image requires an acknowledged publication, not inspection alone')
    finally:
        os.close(fd)
    observed = inspect_operation(directory, pin, transport)
    if (observed.get('policy', {}).get('valid') is not True or
            observed.get('files', {}).get('destination', {}).get('state') != 'matching-bytes' or
            observed.get('files', {}).get('scratch', {}).get('state') != 'absent'):
        raise ValueError('Private recovery image or boot policy is not intact')
    return observed


def prepare(output, frozen):
    """Read-only target bookends surround authoring; every failed draft is retained.

    An acknowledged, currently private recovery image is a prerequisite, not
    proof of watchdog/fallback qualification. No target policies are approved,
    no firmware is staged, and no reboot or lease is dispatched here.
    """
    frozen = copy.deepcopy(frozen)
    plan = validate_publication(frozen['image_plan'])
    if (plan['schema'] != 2 or plan['boot_source'] != '/dev/mmcblk0p1' or
            digest(plan) != frozen['publication_sha256'] or digest(frozen['firmware_bundle']) != frozen['firmware_sha256']):
        raise ValueError('Frozen preparation inputs changed')
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(parent)
    finally: os.close(parent)
    fd = private_directory(output)
    nonce, owner = uuid.uuid4().hex, uuid.uuid4().hex + uuid.uuid4().hex
    record = dict(schema=1, kind='recovery-staging-preparation',
                  status='incomplete', target_written=False, staging_performed=False,
                  reboot_performed=False, deployment_authorized=False, recovery_qualified=False,
                  whole_card_backup_required=True)
    try:
        write_record(fd, 'request.json', dict(schema=1, **frozen, nonce=nonce, lease_owner=owner))
        boot = normal_boot(capture_boot(plan['host']), plan['machine_id'])
        image = published(frozen)
        if image['boot_id'] != boot['boot_id']:
            raise ValueError('Target rebooted during recovery publication inspection')
        write_record(fd, 'normal-before.json', boot)
        write_record(fd, 'publication-before.json', image)
        capture_files(plan['host'], paths(nonce), output/'before-capture.json')
        raw = verify(read_record(fd, 'before-capture.json'), paths(nonce))
        if raw.get('ssh_host') != plan['host'] or raw['machine_id'] != plan['machine_id']:
            raise ValueError('Captured boot preimages belong to another target')
        before = {key: raw[key] for key in ('schema', 'machine_id', 'files')}
        write_record(fd, 'before.json', before)
        review = prepare_recovery(output/'staging', plan['host'], before, nonce,
                                  frozen['firmware_bundle'], plan, lease_owner=owner)
        final = review['phases'][-1]
        staged_plan = pinned_record(Path(final['journal'])/'plan.json', final['plan_sha256'])
        by_path = {item['path']: item for item in staged_plan['after']['files']}
        staged = dict(before, files=[by_path[path] for path in paths(nonce)])
        write_record(fd, 'planned-staged.json', staged)
        hold = compile_hold(before, staged, nonce, frozen['firmware_bundle'], plan, lease_owner=owner)
        hold_pin = write_record(fd, 'hold-review.json', hold)
        capture_files(plan['host'], paths(nonce), output/'after-capture.json')
        second = verify(read_record(fd, 'after-capture.json'), paths(nonce))
        if (second.get('ssh_host') != plan['host'] or second['machine_id'] != plan['machine_id'] or
                any(not equivalent(left, right) for left, right in zip(before['files'], second['files']))):
            raise ValueError('Boot preimages changed during preparation; draft is not ready')
        after = normal_boot(capture_boot(plan['host']), plan['machine_id'])
        image_after = published(frozen)
        if after != boot or image_after['boot_id'] != boot['boot_id']:
            raise ValueError('Normal target boot changed during preparation; draft is not ready')
        write_record(fd, 'normal-after.json', after)
        write_record(fd, 'publication-after.json', image_after)
        seals = {name: digest(read_record(fd, name)) for name in (
            'request.json', 'before.json', 'normal-before.json', 'normal-after.json',
            'publication-before.json', 'publication-after.json', 'planned-staged.json',
            'hold-review.json')}
        seals['staging/review.json'] = digest(review)
        record.update(status='prepared-not-approved', machine_id=plan['machine_id'], boot_id=boot['boot_id'],
                      nonce=nonce, hold_review_sha256=hold_pin, publication_sha256=frozen['publication_sha256'],
                      firmware_sha256=frozen['firmware_sha256'], apply_order=review['apply_order'],
                      restore_order=review['restore_order'],
                      staging_plan_pins=[phase['plan_sha256'] for phase in review['phases']],
                      record_pins=seals)
    except BaseException as exc:
        record['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        try: write_record(fd, 'acceptance.json', record)
        finally: os.close(fd)
    return record


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--publication', type=Path, required=True)
    cli.add_argument('--publication-sha256', required=True)
    cli.add_argument('--firmware-bundle', type=Path, required=True)
    cli.add_argument('--firmware-sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(prepare(args.output, inputs(args.publication, args.publication_sha256,
                     args.firmware_bundle, args.firmware_sha256)), indent=2))


if __name__ == '__main__':
    main()
