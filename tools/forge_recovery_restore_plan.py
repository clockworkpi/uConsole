"""Compile physical backup-root restoration under independently observed hold.

This creates owner-review evidence, not write or boot-release authorization.
Emulator fixtures must not masquerade as physical persistent-hold evidence.
"""
import copy
import os
from pathlib import Path
import re

from forge_recovery_bootcommit import validate as validate_hold
from forge_recovery_bootplan import digest
from forge_recovery_commit_protocol import exact
from forge_recovery_commit_reconcile import check_inspection
from forge_recovery_hash import check_receipt
from forge_ram_boot_observation import checked as check_selection
from forge_recovery_restore_source import validate as validate_source
from forge_target_journal import private_directory, read_record, write_record

INPUT_NAMES=('hold_plan','hold_pin','source_manifest','source_pin','backup_plan',
             'hash_plan','hash_observation','hold_inspection','ram_boot_observation')


def binding_checked(binding):
    fields={'nonce','kernel','serial','mode','boot_id','cid','disk_id','device','extent','lease_owner'}
    if not isinstance(binding,dict) or set(binding)!=fields or binding['mode']!='physical':
        raise ValueError('Physical root restore requires an exact leased RAM binding')
    patterns=dict(nonce='[0-9a-f]{32}',kernel='[A-Za-z0-9.+_-]{1,128}',serial='[0-9a-f]{16}',
                  boot_id='[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',cid='[0-9a-f]{32}',
                  disk_id='[0-9a-f]{8}',device='/dev/mmcblk[0-9]{1,2}',lease_owner='[0-9a-f]{64}')
    if any(not isinstance(binding[key],str) or not re.fullmatch(pattern,binding[key]) for key,pattern in patterns.items()):
        raise ValueError('Invalid physical restore identity')
    extent=binding['extent']
    expected={'kind','cid','disk_id','mbr_sha256','device','offset_bytes','length_bytes',
              'protected_prefix_bytes','protected_suffix_start_bytes','disk_bytes',
              'restore_authorized','consistent_backup_qualified','whole_card_write_authorized'}
    if (not isinstance(extent,dict) or set(extent)!=expected or extent['kind']!='root-partition-extent' or
            extent['cid']!=binding['cid'] or extent['disk_id']!=binding['disk_id'] or
            extent['device']!=binding['device']+'p2' or
            not isinstance(extent['mbr_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',extent['mbr_sha256']) or
            any(type(extent[key]) is not int or extent[key]<=0 or extent[key]%512
                for key in ('offset_bytes','length_bytes','disk_bytes')) or
            extent['offset_bytes']+extent['length_bytes']>extent['disk_bytes'] or
            type(extent['protected_prefix_bytes']) is not int or extent['protected_prefix_bytes']!=extent['offset_bytes'] or
            type(extent['protected_suffix_start_bytes']) is not int or
            extent['protected_suffix_start_bytes']!=extent['offset_bytes']+extent['length_bytes'] or
            any(extent[key] is not False for key in
                ('restore_authorized','consistent_backup_qualified','whole_card_write_authorized'))):
        raise ValueError('Invalid physical restore partition geometry')
    return binding


def compile_plan(hold_plan, hold_pin, source_manifest, source_pin, backup_plan,
                 hash_plan, hash_observation, hold_inspection, ram_boot_observation):
    # Freeze nested inputs before validation; caller changes cannot alter output.
    (hold_plan,source_manifest,backup_plan,hash_plan,hash_observation,hold_inspection,ram_boot_observation)=copy.deepcopy(
        (hold_plan,source_manifest,backup_plan,hash_plan,hash_observation,hold_inspection,ram_boot_observation))
    validate_hold(hold_plan,hold_pin)
    validate_source(source_manifest,source_pin)
    current=binding_checked(hash_plan)
    original=binding_checked(hold_plan['binding'])
    if (hold_plan['operation']!='install-hold' or current['boot_id']==original['boot_id'] or
            not exact({key:value for key,value in current.items() if key!='boot_id'},
                      {key:value for key,value in original.items() if key!='boot_id'})):
        raise ValueError('Restore requires the same held target in a separately verified newer RAM boot')
    check_selection(ram_boot_observation,current['nonce'],current['kernel'],current['serial'],
                    boot_id=current['boot_id'],expected_tryboot=0)
    tokens=ram_boot_observation['identity']['cmdline'].split()
    for prefix,expected in (('uconsole.recovery_owner=',current['lease_owner']),('uconsole.recovery_lease=','1')):
        if [token for token in tokens if token.startswith(prefix)]!=[prefix+expected]:
            raise ValueError('Running physical recovery lease differs')
    check_inspection(hold_inspection,hold_plan,hold_pin,observed_boot_id=current['boot_id'])
    if hold_inspection['status']!='after': raise ValueError('Persistent hold dependencies are not intact')
    if (not isinstance(hash_observation,dict) or hash_observation.get('status')!='verified-offline-storage-digests' or
            hash_observation.get('mode')!='physical' or
            any(hash_observation.get(key) is not False for key in
                ('is_backup','root_write_authorized','normal_boot_release_authorized','target_written'))):
        raise ValueError('Restore requires completed read-only current-card hashes')
    hashes=check_receipt(hash_observation['digests'],current['extent'],current['boot_id'])
    if not exact(hashes['prefix'],hold_inspection['prefix']):
        raise ValueError('Current protected boot bytes differ from held-file inspection')
    if digest(backup_plan)!=source_manifest['backup_plan_sha256'] or backup_plan.get('backup_kind')!='whole-card-bytes':
        raise ValueError('Restore source backup plan differs from the frozen manifest')
    for key in ('mode','kernel','serial','cid','disk_id','device','extent'):
        if not exact(backup_plan.get(key),current[key]):
            raise ValueError('Restore backup belongs to a different target or layout: '+key)
    extent=current['extent']
    if (not exact(backup_plan.get('source'),dict(device=current['device'],length_bytes=extent['disk_bytes'])) or
            source_manifest['card']['bytes']!=extent['disk_bytes'] or
            source_manifest['root']['offset']!=extent['offset_bytes'] or
            source_manifest['root']['bytes']!=extent['length_bytes']):
        raise ValueError('Restore source must fit the exact original root partition')
    return dict(schema=1,kind='guarded-backup-root-restore-plan',operation='restore-backup-root',
        binding=current,hold_plan_sha256=hold_pin,source_manifest_sha256=source_pin,
        source_backup_plan_sha256=digest(backup_plan),hash_plan_sha256=digest(hash_plan),
        hash_observation_sha256=digest(hash_observation),hold_inspection_sha256=digest(hold_inspection),
        ram_boot_observation_sha256=digest(ram_boot_observation),
        root_before=hashes['root'],root_after=source_manifest['root'],
        prefix_guard=hashes['prefix'],suffix_guard=hashes['suffix'],
        held_files=hold_plan['after'],image_dependency=hold_plan['image_dependency'],
        writable_devices=[extent['device']],whole_card_write_authorized=False,
        boot_write_authorized=False,root_write_authorized=False,normal_boot_release_authorized=False,
        filesystem_consistency_qualified=False,
        required_gates=['explicit-owner-restore-plan-approval','fresh-physical-normal-selection-ram-boot',
            'verified-persistent-hold-files-and-image','verified-retained-source-archive',
            'reviewed-source-filesystem-health','fenced-prior-root-writers','exclusive-writable-root-claim',
            'fresh-current-root-and-protected-range-hashes','live-bound-lease-and-watchdog',
            'bounded-source-verified-chunk-transport','synchronized-chunk-readback',
            'complete-root-readback-and-protected-range-verification',
            'separate-native-boot-compatibility-and-release-approval'])


def prepare(directory, *arguments):
    arguments=copy.deepcopy(arguments)
    plan=compile_plan(*arguments)
    directory=Path(directory).absolute()
    directory.mkdir(mode=0o700)
    fd=private_directory(directory)
    try:
        # Retain source evidence as well as its digest; the plan alone cannot
        # recreate approved chunks or observations after a host interruption.
        write_record(fd,'inputs.json',dict(zip(INPUT_NAMES,arguments)))
        pin=write_record(fd,'plan.json',plan)
    finally:
        os.close(fd)
    parent=os.open(directory.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(parent)
    finally: os.close(parent)
    return dict(status='prepared-not-dispatched',journal=str(directory),plan_sha256=pin,
                root_write_authorized=False,normal_boot_release_authorized=False)


def load(directory,pin):
    """Recompile retained evidence under the external owner's immutable pin."""
    fd=private_directory(directory)
    try:
        plan=read_record(fd,'plan.json')
        inputs=read_record(fd,'inputs.json')
        if not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin) or digest(plan)!=pin:
            raise ValueError('Restore journal differs from owner plan pin')
        if not isinstance(inputs,dict) or set(inputs)!=set(INPUT_NAMES):
            raise ValueError('Restore source evidence set differs')
        rebuilt=compile_plan(*(inputs[name] for name in INPUT_NAMES))
        if not exact(plan,rebuilt): raise ValueError('Restore plan differs from its retained evidence')
        return plan
    finally:
        os.close(fd)
