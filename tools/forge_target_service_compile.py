"""Compile new standalone service installation and exact restoration to absence.

Owner-only building block, not arbitrary service/package migration. Unit contents
and their executable/dependency effects must be reviewed separately. Assumes a
normal persistent service with one explicit multi-user.target enablement link;
unexpected systemd state fails the pinned contract instead of being normalized.
"""
import copy
import uuid

from forge_target_effects import fingerprint
from forge_target_service_transaction import validate
from forge_target_services import service_names


def new_service(backup, unit, file, link):
    service_names([unit])
    path = '/etc/systemd/system/' + unit
    link_path = '/etc/systemd/system/multi-user.target.wants/' + unit
    absent = {'path': path, 'kind': 'absent'}
    absent_link = {'path': link_path, 'kind': 'absent'}
    original_service = {'Id': unit, 'LoadState': 'not-found', 'ActiveState': 'inactive',
                        'UnitFileState': '', 'FragmentPath': '', 'DropInPaths': '',
                        'NeedDaemonReload': 'no', 'Transient': 'no'}
    old = {'services': {unit: original_service}, 'files': {path: absent}, 'links': {link_path: absent_link}}
    if (backup.get('state') != old or backup.get('files') != [absent] or
            backup.get('links') != [absent_link]):
        raise ValueError('New service compilation requires exact captured absence of unit and link')
    if (file.get('path') != path or file.get('kind') != 'file' or
            link.get('path') != link_path or link.get('kind') != 'symlink' or link.get('target') != path):
        raise ValueError('Unit and enablement link must match the selected standalone service')
    scope = {'services': [unit], 'files': [path], 'links': [link_path]}
    services = {'services': [unit], 'files': [], 'links': []}
    loaded = dict(original_service, LoadState='loaded', FragmentPath=path, UnitFileState='disabled')
    enabled = dict(loaded, UnitFileState='enabled')
    active = dict(enabled, ActiveState='active')
    new = {'services': {unit: active}, 'files': {path: fingerprint(file)}, 'links': {link_path: fingerprint(link)}}

    def service_state(state):
        return {'services': {unit: state}, 'files': {}, 'links': {}}

    def phase(identifier, operation, view, before, after, **arguments):
        return {'id': identifier, 'before': copy.deepcopy(before), 'after': copy.deepcopy(after),
                'repeatable': operation not in ('start', 'stop'),
                'action': dict(operation=operation, observe=copy.deepcopy(view), **arguments)}

    def verify(identifier, state):
        return phase(identifier, 'verify', scope, state, state)

    def transition(identifier, kind, expected, desired):
        key = 'files' if kind == 'file' else 'links'
        view = {'services': [], 'files': [], 'links': []}
        view[key] = [expected['path']]
        before, after = ({'services': {}, 'files': {}, 'links': {}} for _ in range(2))
        before[key][expected['path']] = fingerprint(expected)
        after[key][expected['path']] = fingerprint(desired)
        return phase(identifier, kind, view, before, after, expected=copy.deepcopy(expected),
                     desired=copy.deepcopy(desired), stage_token=uuid.uuid4().hex)

    def reload(identifier, state, before=None):
        # Invocation is mandatory even if showing the unit already loaded it.
        return phase(identifier, 'daemon-reload', services, service_state(before or state), service_state(state))

    apply = [verify('preimage', old), transition('install-unit', 'file', absent, file),
             reload('reload-unit', loaded), transition('enable-link', 'link', absent_link, link),
             reload('reload-enabled', enabled),
             phase('start', 'start', services, service_state(enabled), service_state(active), service=unit),
             verify('result', new)]
    restore = [verify('preimage', new),
               phase('stop', 'stop', services, service_state(active), service_state(enabled), service=unit),
               transition('remove-link', 'link', link, absent_link),
               transition('remove-unit', 'file', file, absent),
               reload('reload-absent', original_service, dict(enabled, NeedDaemonReload='yes')),
               verify('result', old)]

    def envelope(phases):
        return {'schema': 1, 'scope': copy.deepcopy(scope), 'plan': dict(backup['identity'], schema=1, phases=phases)}

    return validate(backup, envelope(apply), envelope(restore))


def update_service(backup, unit, file, *, active):
    """Replace one local unit, preserving enablement and restoring prior activity.

    Does not back up application data or migrate dependencies. The owner must
    qualify quiescence and the unit's side effects before dispatching the pair.
    """
    service_names([unit])
    if type(active) is not bool:
        raise ValueError('Desired running state must be explicit')
    path = '/etc/systemd/system/' + unit
    link_path = '/etc/systemd/system/multi-user.target.wants/' + unit
    state = backup['state']
    if (set(state) != {'services', 'files', 'links'} or set(state['services']) != {unit} or
            set(state['files']) != {path} or set(state['links']) != {link_path} or
            len(backup['files']) != 1 or len(backup['links']) != 1):
        raise ValueError('Update requires exact standalone unit and enablement backup')
    original = state['services'][unit]
    expected = {'Id': unit, 'LoadState': 'loaded', 'ActiveState': original.get('ActiveState'),
                'UnitFileState': original.get('UnitFileState'), 'FragmentPath': path, 'DropInPaths': '',
                'NeedDaemonReload': 'no', 'Transient': 'no'}
    if (original != expected or original['ActiveState'] not in ('active', 'inactive') or
            original['UnitFileState'] not in ('enabled', 'disabled')):
        raise ValueError('Update requires a stable loaded local unit without drop-ins')
    old_file, link = backup['files'][0], backup['links'][0]
    if (old_file.get('kind') != 'file' or old_file.get('path') != path or
            file.get('kind') != 'file' or file.get('path') != path or link.get('path') != link_path or
            (original['UnitFileState'] == 'enabled' and
             (link.get('kind') != 'symlink' or link.get('target') != path)) or
            (original['UnitFileState'] == 'disabled' and link.get('kind') != 'absent')):
        raise ValueError('Unit payload or enablement link differs from captured service')
    scope = {'services': [unit], 'files': [path], 'links': [link_path]}
    service_scope = {'services': [unit], 'files': [], 'links': []}
    file_scope = {'services': [], 'files': [path], 'links': []}
    desired = copy.deepcopy(state)
    desired['files'][path] = fingerprint(file)
    desired['services'][unit]['ActiveState'] = 'active' if active else 'inactive'

    def direction(before, after, old_payload, new_payload):
        phases = []

        def append(identifier, operation, view, old, new, **arguments):
            phases.append({'id': identifier, 'before': copy.deepcopy(old), 'after': copy.deepcopy(new),
                           'repeatable': operation not in ('start', 'stop'),
                           'action': dict(operation=operation, observe=copy.deepcopy(view), **arguments)})

        def services(value):
            return {'services': {unit: value}, 'files': {}, 'links': {}}

        append('preimage', 'verify', scope, before, before)
        prior = before['services'][unit]
        idle = dict(prior, ActiveState='inactive')
        if prior['ActiveState'] == 'active':
            append('stop', 'stop', service_scope, services(prior), services(idle), service=unit)
        append('replace-unit', 'file', file_scope,
               {'services': {}, 'files': {path: fingerprint(old_payload)}, 'links': {}},
               {'services': {}, 'files': {path: fingerprint(new_payload)}, 'links': {}},
               expected=copy.deepcopy(old_payload), desired=copy.deepcopy(new_payload), stage_token=uuid.uuid4().hex)
        append('reload', 'daemon-reload', service_scope,
               services(dict(idle, NeedDaemonReload='yes')), services(idle))
        if after['services'][unit]['ActiveState'] == 'active':
            append('start', 'start', service_scope, services(idle), services(after['services'][unit]), service=unit)
        append('result', 'verify', scope, after, after)
        return {'schema': 1, 'scope': copy.deepcopy(scope), 'plan': dict(backup['identity'], schema=1, phases=phases)}

    return validate(backup, direction(state, desired, old_file, file),
                    direction(desired, state, file, old_file))
