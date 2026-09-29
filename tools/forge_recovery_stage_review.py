"""Verify a sealed staging draft locally; grants no target execution authority."""
import argparse
import json
from pathlib import Path
import re

from forge_recovery_bootplan import digest
from forge_recovery_hold import compile_hold
from forge_recovery_journal import validate as validate_publication
from forge_recovery_stage_prepare import normal_boot, pinned_record
from forge_target_journal import validate_plan
from forge_trial_firmware import compile_recovery_recipe, paths
from forge_tryboot_recipe import CONFIG, TRIAL_CMDLINE, TRYBOOT


RECORDS = {'request.json', 'before.json', 'normal-before.json', 'normal-after.json',
           'publication-before.json', 'publication-after.json', 'planned-staged.json',
           'hold-review.json', 'staging/review.json'}


def load(directory, acceptance_pin):
    """Recompile all four transitions, checking each pinned private journal.

    This is deliberately offline. A valid draft is not evidence that its boot,
    publication, fallback, or file preimages are still current on the target.
    Old unsealed drafts must be prepared again, never silently upgraded.
    """
    directory = Path(directory).absolute()
    accepted = pinned_record(directory/'acceptance.json', acceptance_pin)
    if (type(accepted.get('schema')) is not int or accepted['schema'] != 1 or
            accepted.get('kind') != 'recovery-staging-preparation' or
            accepted.get('status') != 'prepared-not-approved' or
            any(accepted.get(key) is not False for key in ('target_written', 'staging_performed',
                'reboot_performed', 'deployment_authorized', 'recovery_qualified')) or
            accepted.get('whole_card_backup_required') is not True or
            not isinstance(accepted.get('record_pins'), dict) or set(accepted['record_pins']) != RECORDS):
        raise ValueError('Expected a sealed, unapproved recovery staging preparation')
    records = {name: pinned_record(directory/name, accepted['record_pins'][name]) for name in RECORDS}
    request = records['request.json']
    if (set(request) != {'schema', 'publication', 'publication_sha256', 'image_plan',
                        'firmware_bundle', 'firmware_sha256', 'nonce', 'lease_owner'} or
            type(request['schema']) is not int or request['schema'] != 1):
        raise ValueError('Unexpected staging preparation request')
    if (not isinstance(request['lease_owner'], str) or
            not re.fullmatch('[0-9a-f]{64}', request['lease_owner'])):
        raise ValueError('Staging preparation requires its frozen lease owner')
    image = validate_publication(request['image_plan'])
    if (image['schema'] != 2 or image['boot_source'] != '/dev/mmcblk0p1' or
            digest(image) != request['publication_sha256'] or
            digest(request['firmware_bundle']) != request['firmware_sha256'] or
            any(accepted[key] != request[key] for key in ('publication_sha256', 'firmware_sha256', 'nonce'))):
        raise ValueError('Staging inputs differ from sealed preparation')
    boot = normal_boot(records['normal-before.json'], image['machine_id'])
    if (records['normal-after.json'] != boot or accepted['boot_id'] != boot['boot_id'] or
            accepted['machine_id'] != image['machine_id']):
        raise ValueError('Staging normal boot bookends differ')
    for name in ('publication-before.json', 'publication-after.json'):
        observation = records[name]
        if (observation.get('boot_id') != boot['boot_id'] or
                observation.get('machine_id') != image['machine_id'] or
                observation.get('plan_sha256') != request['publication_sha256'] or
                observation.get('mutation_performed') is not False or
                observation.get('policy', {}).get('valid') is not True or
                observation.get('files', {}).get('destination', {}).get('state') != 'matching-bytes' or
                observation.get('files', {}).get('scratch', {}).get('state') != 'absent'):
            raise ValueError('Staging publication bookends differ')
    before, nonce, owner = records['before.json'], request['nonce'], request['lease_owner']
    recipe = compile_recovery_recipe(before, nonce, request['firmware_bundle'], image, lease_owner=owner)
    selected = paths(nonce)
    current = {item['path']: item for item in before['files']}
    if any(item['kind'] == 'file' and
           (item['mode'] != 0o700 or item['uid'] != 0 or item['gid'] != 0 or item['xattrs'])
           for item in current.values()):
        raise ValueError('Staging requires private root-owned boot preimages')
    native = current[CONFIG]
    desired = {item['path']: dict(native, **item) for item in recipe['files']}
    steps = [('firmware-start', selected[-2]), ('firmware-fixup', selected[-1]),
             ('command', TRIAL_CMDLINE), ('selector', TRYBOOT)]
    order = [name for name, _ in steps]
    review = records['staging/review.json']
    if (accepted['apply_order'] != order or accepted['restore_order'] != order[::-1] or
            len(accepted['staging_plan_pins']) != len(steps)):
        raise ValueError('Unexpected staging phase order')
    phases, plans = [], []
    for (name, target), pin in zip(steps, accepted['staging_plan_pins']):
        journal = directory/'staging'/name
        plan = validate_plan(pinned_record(journal/'plan.json', pin))
        file_order = [path for path in selected if path != target] + [target]
        expected_before = dict(before, files=[current[path] for path in file_order])
        current[target] = desired[target]
        expected_after = dict(before, files=[current[path] for path in file_order])
        if plan['host'] != image['host'] or plan['before'] != expected_before or plan['after'] != expected_after:
            raise ValueError('Staging phase differs from independently compiled transition')
        phases.append(dict(journal=str(journal), plan_sha256=pin, status='prepared'))
        plans.append(plan)
    expected_review = dict(recipe, phases=phases, guarded_paths=selected,
                           apply_order=order, restore_order=order[::-1])
    staged = dict(before, files=[current[path] for path in selected])
    hold = compile_hold(before, staged, nonce, request['firmware_bundle'], image, lease_owner=owner)
    if (review != expected_review or records['planned-staged.json'] != staged or
            records['hold-review.json'] != hold or digest(hold) != accepted['hold_review_sha256']):
        raise ValueError('Staging review differs from independently compiled transitions')
    return dict(acceptance=accepted, request=request, review=review, plans=plans, original_boot=boot)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--directory', type=Path, required=True)
    cli.add_argument('--acceptance-sha256', required=True)
    args = cli.parse_args()
    reviewed = load(args.directory, args.acceptance_sha256)
    # Do not print the private request, firmware payloads, or lease owner.
    print(json.dumps(reviewed['acceptance'], indent=2))


if __name__ == '__main__':
    main()
