"""Freeze single-selector recovery transitions with independent root guards.

Plans are review artifacts, not generic file transactions or release authority.
A future executor must recheck digests while holding the root read-only claim.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from forge_recovery_hash import check_receipt
from forge_target_backup import verify
from forge_target_files import equivalent
from forge_target_journal import private_directory, write_record
from forge_trial_firmware import paths
from forge_tryboot_recipe import CONFIG


def digest(record):
    return hashlib.sha256((json.dumps(record, sort_keys=True, indent=2)+'\n').encode()).hexdigest()


def compile_transition(review, hash_plan, observation, expected_root, operation):
    if not all(isinstance(item, dict) for item in (review, hash_plan, observation)):
        raise ValueError('Expected structured hold and checksum evidence')
    if operation not in ('install-hold', 'release-hold'):
        raise ValueError('Expected explicit hold installation or release')
    if (review.get('kind') != 'persistent-recovery-hold-review' or type(review.get('schema')) is not int or
            review['schema'] != 1 or
            review.get('changed_paths') != [CONFIG] or
            any(review.get(field) is not False for field in ('deployment_authorized',
                'normal_boot_release_authorized', 'whole_card_write_authorized', 'root_write_authorized'))):
        raise ValueError('Expected a non-deploying, single-selector hold review')
    selected = paths(review['nonce'])
    for state in ('before', 'after'):
        verify(review[state], selected)
        if review[state]['machine_id'] != review['machine_id']:
            raise ValueError('Hold review target identities differ')
        for item in review[state]['files']:
            if item['kind'] == 'file' and (item['uid'] != 0 or item['gid'] != 0 or
                                          item['mode'] != 0o700 or item['xattrs']):
                raise ValueError('Boot transitions require private FAT metadata')
    before = {item['path']: item for item in review['before']['files']}
    after = {item['path']: item for item in review['after']['files']}
    if [path for path in selected if not equivalent(before[path], after[path])] != [CONFIG]:
        raise ValueError('Only config.txt may change in a held-boot transition')
    if before[CONFIG]['kind'] != 'file' or after[CONFIG]['kind'] != 'file':
        raise ValueError('Both boot selectors must be regular-file preimages')
    if ({k:v for k,v in before[CONFIG].items() if k not in ('data','size','sha256')} !=
            {k:v for k,v in after[CONFIG].items() if k not in ('data','size','sha256')}):
        raise ValueError('Boot selector transition may change content only')
    fields = {'nonce','kernel','serial','mode','boot_id','cid','disk_id','device','extent'}
    if (set(hash_plan) not in (fields, fields | {'lease_owner'}) or
            any(not isinstance(hash_plan.get(field), str) for field in
                ('nonce','kernel','mode','boot_id','cid','disk_id','device')) or
            hash_plan['nonce'] != review['nonce'] or hash_plan['mode'] not in ('physical','emulated') or
            not re.fullmatch('[A-Za-z0-9.+_-]{1,128}', hash_plan['kernel']) or
            not re.fullmatch('/dev/mmcblk[0-9]{1,2}', hash_plan['device']) or
            not re.fullmatch('[0-9a-f]{32}', hash_plan['cid']) or
            not re.fullmatch('[0-9a-f]{8}', hash_plan['disk_id']) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', hash_plan['boot_id'])):
        raise ValueError('Invalid recovery checksum binding')
    if (hash_plan.get('lease_owner') != review.get('lease', {}).get('owner') or
            ('lease_owner' in hash_plan and not re.fullmatch('[0-9a-f]{64}', hash_plan['lease_owner']))):
        raise ValueError('Hold review and checksum lease owners differ')
    if hash_plan['mode'] == 'physical' and (not isinstance(hash_plan['serial'], str) or
                                           not re.fullmatch('[0-9a-f]{16}', hash_plan['serial'])):
        raise ValueError('Physical transition requires a pinned hardware serial')
    if (observation.get('status') != 'verified-offline-storage-digests' or
            observation.get('mode') != hash_plan['mode'] or
            any(observation.get(field) is not False for field in ('is_backup','root_write_authorized',
                'normal_boot_release_authorized','target_written'))):
        raise ValueError('Expected a completed read-only checksum observation')
    receipt = check_receipt(observation['digests'], hash_plan['extent'], hash_plan['boot_id'])
    extent = hash_plan['extent']
    if (any(type(extent.get(field)) is not int or extent[field] <= 0 or extent[field] % 512
            for field in ('disk_bytes','offset_bytes','length_bytes')) or
            extent['offset_bytes']+extent['length_bytes'] > extent['disk_bytes']):
        raise ValueError('Invalid aligned root extent')
    # expected_root is supplied separately from an owner-selected backup/export
    # or pre-trial root baseline; never silently adopt the currently read root.
    if not isinstance(expected_root, dict) or expected_root != receipt['root']:
        raise ValueError('Current root differs from the independently approved root guard')
    source, desired = ('before','after') if operation == 'install-hold' else ('after','before')
    return dict(schema=1, kind='guarded-recovery-boot-file-plan', operation=operation,
                hold_review_sha256=digest(review), checksum_plan_sha256=digest(hash_plan),
                checksum_observation_sha256=digest(observation), binding=copy.deepcopy(hash_plan),
                before=copy.deepcopy(review[source]), after=copy.deepcopy(review[desired]),
                guarded_paths=selected, changed_paths=[CONFIG],
                root_guard=copy.deepcopy(expected_root), prefix_guard=copy.deepcopy(receipt['prefix']),
                suffix_guard=copy.deepcopy(receipt['suffix']), image_dependency=copy.deepcopy(review['image_dependency']),
                deployment_authorized=False, root_write_authorized=False,
                normal_boot_release_authorized=False,
                required_gates=list(dict.fromkeys(review['required_gates'] +
                               (review['release_requires'] if operation == 'release-hold' else []) +
                               ['explicit-owner-plan-approval','exclusive-read-only-root-claim',
                                'fresh-root-and-protected-range-hashes','verified-boot-file-preimages',
                                'bounded-commit-with-live-watchdog','verified-unmount-before-acknowledgement'])))


def prepare(directory, review, hash_plan, observation, expected_root, operation):
    plan = compile_transition(review, hash_plan, observation, expected_root, operation)
    plan['stage_token'] = uuid.uuid4().hex  # Retain this exact name for retry/reconciliation.
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    try:
        pin = write_record(fd, 'plan.json', plan)
    finally:
        os.close(fd)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return dict(status='prepared', journal=str(directory), plan_sha256=pin,
                deployment_authorized=False)
