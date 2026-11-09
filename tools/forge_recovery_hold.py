"""Compile a persistent recovery selector; never dispatch or authorize release.

Whole-card writes would destroy this selector too. Consumers must preserve the
boot partition and partition table throughout any interrupted root restoration.
"""
import copy
import hashlib
import json

from forge_target_backup import verify
from forge_target_files import equivalent
from forge_trial_firmware import compile_recovery_recipe, paths
from forge_tryboot_recipe import CONFIG, TRYBOOT


def compile_hold(original, staged, nonce, bundle, image_plan, *, lease_owner=None):
    """Require exact staged dependencies before deriving config.txt replacement.

    This is a review artifact, not a generic file-dispatch plan. No inverse is
    exposed: normal boot requires separate proof of completed restoration.
    """
    selected = paths(nonce)
    recipe = compile_recovery_recipe(original, nonce, bundle, image_plan, lease_owner=lease_owner)
    verify(staged, selected)
    if staged['machine_id'] != original['machine_id']:
        raise ValueError('Staged recovery belongs to another target')
    old = {record['path']: record for record in original['files']}
    for record in original['files']:
        if record['kind'] == 'file' and (record['mode'] != 0o700 or
                record['uid'] != 0 or record['gid'] != 0 or record['xattrs']):
            raise ValueError('Persistent recovery requires private boot metadata')
    expected = copy.deepcopy(old)
    for output in recipe['files']:
        expected[output['path']] = dict(old[CONFIG], **output)
    for record in staged['files']:
        if not equivalent(record, expected[record['path']]):
            raise ValueError('Staged recovery dependency changed: ' + record['path'])
    after = copy.deepcopy(staged)
    selector = next(item for item in recipe['files'] if item['path'] == TRYBOOT)
    replacement = next(item for item in after['files'] if item['path'] == CONFIG)
    replacement.update({key: selector[key] for key in ('data', 'size', 'sha256')})
    verify(after, selected)
    frozen_recipe = hashlib.sha256((json.dumps(recipe, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
    result = dict(schema=1, kind='persistent-recovery-hold-review', machine_id=original['machine_id'],
                nonce=nonce, recipe_sha256=frozen_recipe, image_dependency=recipe['image_dependency'],
                before=copy.deepcopy(staged), after=after, changed_paths=[CONFIG],
                protected_paths=selected + [image_plan['destination']],
                deployment_authorized=False, normal_boot_release_authorized=False,
                root_write_authorized=False, whole_card_write_authorized=False,
                required_gates=list(dict.fromkeys(recipe['required_gates'] +
                               ['verified-host-backup', 'qualified-physical-ram-boot',
                                'acknowledged-image-publication', 'private-persistent-boot-policy',
                                'reviewed-hold-dispatch', 'verified-persistent-recovery-reboot',
                                'boot-partition-and-partition-table-write-exclusion'])),
                release_requires=['verified-complete-root-restoration',
                                  'verified-native-boot-dependencies', 'reviewed-normal-boot-release'])
    if 'lease' in recipe:
        result['lease'] = copy.deepcopy(recipe['lease'])
    return result
