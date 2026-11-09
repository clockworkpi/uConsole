"""Pinned, target-locked execution boundary for service phase envelopes.

Internal target-side API, not yet exposed by GUI/MCP. The owner must provision
a private ledger parent and review unit dependency effects and application data
consistency before authorizing an envelope. No source-generated shell commands.
"""
import hashlib
import json
import os
from pathlib import Path
import re

from forge_target_effects import TargetEffects, fingerprint
from forge_target_journal import private_directory
from forge_target_phases import canonical, run, validate as validate_phases
from forge_target_ssh import target_lock


def validate(envelope):
    if (not isinstance(envelope, dict) or set(envelope) != {'schema', 'scope', 'plan'} or
            type(envelope['schema']) is not int or envelope['schema'] != 1 or
            not isinstance(envelope['scope'], dict) or set(envelope['scope']) != {'services', 'files', 'links'} or
            any(not isinstance(values, list) for values in envelope['scope'].values())):
        raise ValueError('Invalid service phase envelope')
    envelope = json.loads(canonical(envelope))
    plan = validate_phases(envelope['plan'])
    effects = TargetEffects(**envelope['scope'])
    for phase in plan['phases']:
        action = effects.checked(phase)
        view = action['observe']
        for state in (phase['before'], phase['after']):
            if (set(state) != {'services', 'files', 'links'} or
                    any(not isinstance(state[key], dict) or set(state[key]) != set(view[key]) for key in view)):
                raise ValueError('Phase states must cover exactly their observation scope')
        kind = action['operation']
        if kind in ('file', 'link'):
            key = 'files' if kind == 'file' else 'links'
            path = action['expected']['path']
            if (canonical(phase['before'][key][path]) != canonical(fingerprint(action['expected'])) or
                    canonical(phase['after'][key][path]) != canonical(fingerprint(action['desired']))):
                raise ValueError('File/link phase postcondition differs from intended contents')
        elif kind in ('start', 'stop'):
            wanted = 'active' if kind == 'start' else 'inactive'
            if phase['after']['services'][action['service']].get('ActiveState') != wanted:
                raise ValueError('Service lifecycle postcondition differs from action')
        elif kind == 'daemon-reload':
            if any(state.get('NeedDaemonReload') != 'no' for state in phase['after']['services'].values()):
                raise ValueError('Reload phase must require a reloaded unit configuration')
        elif canonical(phase['before']) != canonical(phase['after']):
            raise ValueError('Verification-only phase cannot change state')
    final = plan['phases'][-1]
    if (final['action']['operation'] != 'verify' or
            any(set(final['action']['observe'][key]) != set(envelope['scope'][key]) for key in envelope['scope'])):
        raise ValueError('Final verification must observe every approved object')
    return envelope, effects


def execute(envelope, approved_sha256, *, ledger_parent='/var/lib/uconsole-forge/service-phases',
            lock_path='/run/lock/uconsole-forge-target.lock'):
    envelope, effects = validate(envelope)
    digest = hashlib.sha256(canonical(envelope).encode()).hexdigest()
    if not isinstance(approved_sha256, str) or not re.fullmatch('[0-9a-f]{64}', approved_sha256) or approved_sha256 != digest:
        raise PermissionError('Service phase envelope differs from owner-approved digest')
    expected_identity = {key: envelope['plan'][key] for key in ('machine_id', 'boot_id')}
    if effects.identity() != expected_identity:
        raise ValueError('Service phase target or boot identity differs from approval')
    # The parent is owner-provisioned, never a wire-supplied path. Refuse an
    # insecure or absent parent instead of creating broad privileged directories.
    fd = private_directory(ledger_parent)
    os.close(fd)
    with target_lock(lock_path):
        result = run(Path(ledger_parent) / digest, envelope['plan'], identity=effects.identity,
                     observe=effects.observe, act=effects.act, reconcile=effects.reconcile,
                     must_act=lambda phase: phase['action']['operation'] == 'daemon-reload')
        return dict(result, envelope_sha256=digest)
