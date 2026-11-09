"""Private, reviewable pairing of service backup, apply and restore envelopes.

This is an authoring boundary, not a deployment grant. Dependency effects and
application-data consistency still need qualification before dispatch.
"""
import hashlib
import json
import os
from pathlib import Path

from forge_target_backup import verify as verify_files
from forge_target_effects import fingerprint
from forge_target_journal import private_directory, write_record
from forge_target_links import validate as validate_link
from forge_target_phases import canonical
from forge_target_service_runtime import validate as validate_envelope


def validate(backup, apply, restore):
    apply, _ = validate_envelope(apply)
    restore, _ = validate_envelope(restore)
    if (not isinstance(backup, dict) or set(backup) != {'identity', 'state', 'files', 'links'} or
            not isinstance(backup['identity'], dict) or
            set(backup['identity']) != {'machine_id', 'boot_id'} or
            not isinstance(backup['files'], list) or not isinstance(backup['links'], list)):
        raise ValueError('Invalid service transaction backup')
    backup = json.loads(canonical(backup))
    scope = apply['scope']
    if canonical(scope) != canonical(restore['scope']):
        raise ValueError('Apply and restore must cover the same approved objects')
    for envelope in (apply, restore):
        if {key: envelope['plan'][key] for key in backup['identity']} != backup['identity']:
            raise ValueError('Backup and envelopes must bind the same target and boot')
        first = envelope['plan']['phases'][0]
        if (first['action']['operation'] != 'verify' or
                any(set(first['action']['observe'][key]) != set(scope[key]) for key in scope)):
            raise ValueError('Each direction must begin with aggregate preimage verification')
    if scope['files']:
        verify_files({'schema': 1, 'machine_id': backup['identity']['machine_id'],
                      'files': backup['files']}, scope['files'])
    elif backup['files']:
        raise ValueError('File backup exceeds approved scope')
    links = [validate_link(item) for item in backup['links']]
    if (len(links) != len(scope['links']) or
            {item['path'] for item in links} != set(scope['links'])):
        raise ValueError('Link backup must cover exactly the approved scope')
    state = backup['state']
    if (not isinstance(state, dict) or set(state) != set(scope) or
            any(not isinstance(state[key], dict) or set(state[key]) != set(scope[key]) for key in scope)):
        raise ValueError('Backup state must cover exactly the approved scope')
    for key in ('files', 'links'):
        expected = {item['path']: fingerprint(item) for item in backup[key]}
        if canonical(state[key]) != canonical(expected):
            raise ValueError('Backup observation differs from retained payloads')
    old = apply['plan']['phases'][0]['before']
    new = apply['plan']['phases'][-1]['after']
    restore_old = restore['plan']['phases'][0]['before']
    restored = restore['plan']['phases'][-1]['after']
    if canonical(old) != canonical(state) or canonical(restored) != canonical(state):
        raise ValueError('Apply must start at backup and restore must return to backup')
    if canonical(new) != canonical(restore_old):
        raise ValueError('Restore preimage must match the applied aggregate state')
    # Every payload removed/replaced by restore must be represented in its
    # envelope. Matching an observation hash alone cannot recover file bytes.
    for envelope in (apply, restore):
        for phase in envelope['plan']['phases']:
            action = phase['action']
            if action['operation'] not in ('file', 'link'):
                continue
            key = 'files' if action['operation'] == 'file' else 'links'
            retained = {item['path']: item for item in backup[key]}
            for record in (action['expected'], action['desired']):
                original = retained[record['path']]
                if fingerprint(record) == fingerprint(original) and canonical(record) != canonical(original):
                    # atime is deliberately not a conflict key, but restore
                    # must still retain the original timestamp in its payload.
                    raise ValueError('Backup-equivalent payload differs from retained original metadata')
    return {'schema': 1, 'kind': 'service-transaction-review', 'backup': backup,
            'apply': apply, 'restore': restore}


def prepare(directory, backup, apply, restore):
    record = validate(backup, apply, restore)
    digest = hashlib.sha256(canonical(record).encode()).hexdigest()
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    try:
        write_record(fd, 'transaction.json', record)
    finally:
        os.close(fd)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return {'status': 'prepared', 'transaction_sha256': digest,
            'apply_sha256': hashlib.sha256(canonical(record['apply']).encode()).hexdigest(),
            'restore_sha256': hashlib.sha256(canonical(record['restore']).encode()).hexdigest()}
