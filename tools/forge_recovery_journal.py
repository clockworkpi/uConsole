"""Owner-only image operation journal. Transport must enforce target policy.

This module grants no client permission and exposes no deployment CLI. A failed
or unacknowledged transport leaves the plan blocked for explicit reconciliation.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from forge_recovery_image import parameters
from forge_target_backup import paths_checked
from forge_target_journal import private_directory, read_record, write_record, events, append_event


def validate(plan):
    fields = {'schema', 'kind', 'host', 'machine_id', 'fstab_sha256', 'boot_source',
              'source', 'destination', 'sha256', 'size', 'stage_token', 'preimage'}
    if not isinstance(plan, dict) or set(plan) != fields or type(plan['schema']) is not int or plan['schema'] not in (1, 2):
        raise ValueError('Invalid recovery plan schema')
    if plan['kind'] != 'private-recovery-image' or plan['preimage'] != {'kind': 'absent'}:
        raise ValueError('Only originally absent recovery images are supported')
    parameters(plan['sha256'], plan['size'], plan['stage_token'])
    for name, pattern in (('machine_id', '[0-9a-f]{32}'), ('fstab_sha256', '[0-9a-f]{64}'),
                          ('host', r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*'),
                          ('boot_source', r'/dev/[A-Za-z0-9_.-]+')):
        if not isinstance(plan[name], str) or not re.fullmatch(pattern, plan[name]):
            raise ValueError('Invalid plan field: ' + name)
    paths_checked([plan['source'], plan['destination']])
    if plan['destination'] != '/boot/firmware/forge-recovery-' + plan['stage_token'] + '.img':
        raise ValueError('Destination must be the exact token-bound recovery image')
    return plan


def prepare(directory, plan):
    validate(plan)
    if plan['schema'] != 2:
        raise ValueError('New recovery plans require durable-target schema 2')
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    try:
        digest = write_record(fd, 'plan.json', plan)
    finally:
        os.close(fd)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return {'journal': str(directory), 'plan_sha256': digest}


def acknowledgement(plan, direction, nonce, digest):
    return dict(direction=direction, nonce=nonce, plan_sha256=digest,
                machine_id=plan['machine_id'], sha256=plan['sha256'], size=plan['size'],
                status='published' if direction == 'apply' else 'removed')


def fence_checked(result, plan, intent, digest):
    request = dict(plan_sha256=digest, direction=intent['direction'], nonce=intent['nonce'])
    envelope = dict(kind='fence', nonce=intent['nonce'], plan_sha256=digest,
                    machine_id=plan['machine_id'])
    if not isinstance(result, dict) or set(result) != set(envelope) | {'outcome'} or any(
            result[k] != v for k, v in envelope.items()):
        raise RuntimeError('Invalid recovery fence response')
    outcome = result['outcome']
    if not isinstance(outcome, dict):
        raise RuntimeError('Invalid recovery fence outcome')
    status = outcome.get('status')
    expected = dict(status=status, request=request)
    if status == 'completed':
        expected['result'] = acknowledgement(plan, intent['direction'], intent['nonce'], digest)
    elif status not in ('fenced-not-started', 'incomplete'):
        raise RuntimeError('Unknown recovery fence outcome')
    if outcome != expected:
        raise RuntimeError('Invalid recovery fence receipt')
    return status


def history_checked(history, plan, digest, fd=None, pending=None):
    previous = 'restore'
    for index in range(0, len(history), 2):
        intent = history[index]
        if (intent.get('state') != 'dispatch' or intent.get('direction') not in ('apply', 'restore')
                or intent['direction'] == previous or not isinstance(intent.get('nonce'), str)
                or not re.fullmatch('[0-9a-f]{32}', intent['nonce'])):
            raise RuntimeError('Malformed recovery operation history')
        outcome = history[index + 1] if index + 1 < len(history) else dict(intent, state='uncertain')
        if any(outcome.get(key) != intent[key] for key in ('direction', 'nonce')):
            raise RuntimeError('Recovery acknowledgement does not match intent')
        if outcome.get('state') == 'uncertain':
            try:
                resolution = read_record(fd, 'resolution-' + intent['nonce'] + '.json') if fd is not None else None
            except FileNotFoundError:
                resolution = None
            if resolution is None:
                if pending is not None and index + 2 >= len(history):
                    pending.append(intent)
                    return previous
                raise RuntimeError('Uncertain recovery operation requires reconciliation')
            status = fence_checked(resolution, plan, intent, digest)
            if status == 'incomplete':
                raise RuntimeError('Incomplete recovery operation cannot be resolved')
            if status == 'completed':
                previous = intent['direction']
            continue
        if outcome.get('state') != 'acknowledged':
            raise RuntimeError('Malformed recovery operation outcome')
        if outcome.get('result') != acknowledgement(plan, intent['direction'], intent['nonce'], digest):
            raise RuntimeError('Stored recovery acknowledgement is invalid')
        previous = intent['direction']
    return previous


def dispatch(directory, direction, approved_digest, transport):
    if direction not in ('apply', 'restore'):
        raise ValueError('Invalid recovery direction')
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = validate(read_record(fd, 'plan.json'))
        if plan['schema'] != 2:
            raise ValueError('Legacy recovery plans are inspect-only')
        digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        if approved_digest != digest:
            raise PermissionError('Recovery plan is not the owner-approved revision')
        previous = history_checked(events(fd), plan, digest, fd)
        if previous == direction:
            raise RuntimeError('Recovery direction already completed; inspect history')
        nonce = uuid.uuid4().hex
        intent = dict(state='dispatch', direction=direction, nonce=nonce)
        append_event(fd, intent)  # Durable before transport may perform any write.
        try:
            result = transport(plan, direction, nonce, digest)
            expected = acknowledgement(plan, direction, nonce, digest)
            if result != expected:
                raise RuntimeError('Invalid recovery acknowledgement')
            append_event(fd, dict(intent, state='acknowledged', result=result))
            return result
        except BaseException as exc:
            append_event(fd, dict(intent, state='uncertain', error=type(exc).__name__))
            raise
    finally:
        os.close(fd)


def reconcile(directory, approved_digest, transport):
    """Explicitly fence an uncertain attempt; retain the original event history."""
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = validate(read_record(fd, 'plan.json'))
        if plan['schema'] != 2:
            raise ValueError('Legacy recovery plans are inspect-only')
        digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        if approved_digest != digest:
            raise PermissionError('Recovery plan is not the owner-approved revision')
        history = events(fd)
        pending = []
        history_checked(history, plan, digest, fd, pending)
        if not pending:
            raise RuntimeError('No uncertain recovery attempt to reconcile')
        intent = pending[0]
        if len(history) % 2:
            append_event(fd, dict(intent, state='uncertain', error='RecoveredUnacknowledgedDispatch'))
        token = uuid.uuid4().hex
        request = dict(plan_sha256=digest, direction=intent['direction'], nonce=intent['nonce'])
        write_record(fd, 'fence-intent-' + token + '.json', request)
        result = transport(plan, 'fence-' + intent['direction'], intent['nonce'], digest)
        status = fence_checked(result, plan, intent, digest)
        write_record(fd, 'fence-observation-' + token + '.json', result)
        if status != 'incomplete':
            write_record(fd, 'resolution-' + intent['nonce'] + '.json', result)
        return result
    finally:
        os.close(fd)


def inspect_operation(directory, approved_digest, transport):
    """Retain observations separately; never rewrite uncertain operation history."""
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = validate(read_record(fd, 'plan.json'))
        digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        if digest != approved_digest:
            raise PermissionError('Inspection plan is not the owner-approved revision')
        nonce = uuid.uuid4().hex
        result = transport(plan, 'inspect', nonce, digest)
        expected = dict(kind='inspection', nonce=nonce, plan_sha256=digest, machine_id=plan['machine_id'],
                        mutation_performed=False, retry_authorized=False)
        if (not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items())
                or result.get('mutation_performed') is not False or result.get('retry_authorized') is not False
                or not isinstance(result.get('files'), dict) or not isinstance(result.get('policy'), dict)
                or not isinstance(result.get('boot_id'), str)
                or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', result['boot_id'])):
            raise RuntimeError('Invalid recovery inspection response')
        write_record(fd, 'inspection-' + nonce + '.json', result)
        return result
    finally:
        os.close(fd)
