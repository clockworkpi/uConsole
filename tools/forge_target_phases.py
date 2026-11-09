"""Durable phase runner for owner-defined, observable target effects.

No commands are built here. The service executor must provide bounded observe
and act callbacks and meaningful before/after contracts. Non-repeatable effects
are never automatically retried after an unacknowledged intent.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from forge_target_journal import private_directory, read_record, write_record


class PhaseUncertain(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate(plan):
    if (not isinstance(plan, dict) or set(plan) != {'schema', 'machine_id', 'boot_id', 'phases'} or
            type(plan['schema']) is not int or plan['schema'] != 1 or
            not isinstance(plan['machine_id'], str) or not re.fullmatch('[0-9a-f]{32}', plan['machine_id']) or
            not isinstance(plan['boot_id'], str) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', plan['boot_id']) or
            not isinstance(plan['phases'], list) or not 1 <= len(plan['phases']) <= 256):
        raise ValueError('Invalid phase plan identity/schema')
    seen = set()
    for phase in plan['phases']:
        if (not isinstance(phase, dict) or set(phase) != {'id', 'before', 'after', 'action', 'repeatable'} or
                not isinstance(phase['id'], str) or not re.fullmatch('[a-z0-9_-]{1,64}', phase['id']) or
                phase['id'] in seen or type(phase['repeatable']) is not bool or
                not all(isinstance(phase[key], dict) for key in ('before', 'after', 'action'))):
            raise ValueError('Invalid phase contract')
        seen.add(phase['id'])
    encoded = canonical(plan)
    if len(encoded.encode()) > 1024 * 1024:
        raise ValueError('Phase plan exceeds 1 MiB')
    return json.loads(encoded)  # Snapshot mutable caller data before any effect.


def run(directory, plan, *, identity, observe, act, reconcile=None, must_act=None):
    plan = validate(plan)
    expected_identity = {key: plan[key] for key in ('machine_id', 'boot_id')}
    if identity() != expected_identity:
        raise PhaseUncertain('Target or boot identity changed; reconcile before executing phases')
    directory = Path(directory).absolute()
    fresh = False
    try:
        directory.mkdir(mode=0o700)
        fresh = True
    except FileExistsError:
        pass
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if fresh:
            write_record(fd, 'phase-plan.json', plan)
            parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        elif canonical(read_record(fd, 'phase-plan.json')) != canonical(plan):
            raise PhaseUncertain('Phase ledger belongs to a different plan')
        names = set(os.listdir(fd)) - {'phase-plan.json'}
        allowed = {f'{i:04d}-{kind}.json' for i in range(len(plan['phases'])) for kind in ('intent', 'done')}
        if names - allowed:
            raise PhaseUncertain('Unexpected phase ledger records')
        progress = []
        for index, phase in enumerate(plan['phases']):
            digest = hashlib.sha256(canonical(phase).encode()).hexdigest()
            record = {'id': phase['id'], 'sha256': digest}
            intent, done = (f'{index:04d}-{kind}.json' for kind in ('intent', 'done'))
            has_intent, has_done = intent in names, done in names
            if has_done and not has_intent:
                raise PhaseUncertain('Phase acknowledgement has no prior intent')
            for name, present in ((intent, has_intent), (done, has_done)):
                if present and read_record(fd, name) != record:
                    raise PhaseUncertain('Phase record differs from its approved contract')
            progress.append((intent, done, record, has_intent, has_done))
        incomplete = False
        for _, _, _, has_intent, has_done in progress:
            if incomplete and (has_intent or has_done):
                raise PhaseUncertain('Phase ledger has a gap or out-of-order effect')
            incomplete = incomplete or not has_done
        for phase, (intent, done, record, has_intent, has_done) in zip(plan['phases'], progress):
            if has_done:
                continue
            if identity() != expected_identity:
                raise PhaseUncertain('Target rebooted during phased operation')
            if has_intent and phase['repeatable'] and reconcile is not None:
                reconcile(phase)
            observed = canonical(observe(phase))
            # Some effects (e.g. refreshing systemd's dependency graph) cannot
            # be proven by the exposed properties alone. Their adapter requires
            # an acknowledged invocation, including reconciliation of an intent
            # left before the invocation. Completed phases are still not rerun.
            required = must_act is not None and must_act(phase)
            at_result = observed == canonical(phase['after'])
            if at_result and not required:
                if not has_intent:
                    write_record(fd, intent, record)
                write_record(fd, done, record)
                continue
            if observed != canonical(phase['before']) and not (required and at_result):
                raise PhaseUncertain('Unexpected state at phase ' + phase['id'])
            if has_intent and not phase['repeatable']:
                raise PhaseUncertain('Unacknowledged non-repeatable effect at ' + phase['id'])
            if not has_intent:
                write_record(fd, intent, record)  # Durable before invoking effect.
            act(phase)
            if identity() != expected_identity or canonical(observe(phase)) != canonical(phase['after']):
                raise PhaseUncertain('Effect not verified at phase ' + phase['id'])
            write_record(fd, done, record)
        # Earlier phase states may legitimately have changed in later phases.
        # The final phase must define the aggregate transaction postcondition.
        final = plan['phases'][-1]
        if identity() != expected_identity or canonical(observe(final)) != canonical(final['after']):
            raise PhaseUncertain('Final transaction postcondition no longer holds')
        return {'status': 'verified', 'phases': len(progress), 'ledger': str(directory)}
    finally:
        os.close(fd)
