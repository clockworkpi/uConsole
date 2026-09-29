"""Compile service/file ordering for review and future journaled execution.

Recipes are deliberately not accepted by the file-only executor. Service-side
phase reconciliation and enablement-link/dependency qualification are required
before this ordering may drive physical writes.
"""
from forge_target_backup import paths_checked, verify as verify_files
from forge_target_services import service_names, verify as verify_services
from forge_target_dependencies import verify as verify_graph
import copy


def contract(state):
    if state['LoadState'] == 'not-found':
        if (state['ActiveState'] != 'inactive' or state['UnitFileState'] or
                state['FragmentPath'] or state['DropInPaths']):
            raise ValueError('Absent unit has inconsistent properties')
        return {'present': False, 'active': False, 'enabled': False}
    if (state['LoadState'] != 'loaded' or state['ActiveState'] not in ('active', 'inactive') or
            state['UnitFileState'] not in ('enabled', 'disabled')):
        raise ValueError('Service restore currently requires ordinary loaded enabled/disabled units, or absence')
    return {'present': True, 'active': state['ActiveState'] == 'active',
            'enabled': state['UnitFileState'] == 'enabled'}


def steps(names, desired, direction):
    # Disable before removing unit files: afterward systemctl may no longer
    # know which installation links belong to them. Enable only after reload.
    return [
        {'phase': 'verify', 'action': 'verify-file-and-service-preimages'},
        {'phase': 'quiesce', 'action': 'stop-if-loaded', 'services': names},
        {'phase': 'quiesce', 'action': 'disable-if-loaded',
         'services': [name for name in names if not desired[name]['enabled']]},
        {'phase': 'files', 'action': 'apply-journaled-files', 'direction': direction},
        {'phase': 'reload', 'action': 'daemon-reload'},
        {'phase': 'enable', 'action': 'enable',
         'services': [name for name in names if desired[name]['enabled']]},
        {'phase': 'start', 'action': 'start',
         'services': [name for name in names if desired[name]['active']]},
        {'phase': 'verify', 'action': 'verify-file-and-service-results', 'services': desired},
    ]


def compile_recipe(snapshot, before, after, desired, *, dependency_graph=None):
    if not isinstance(desired, dict):
        raise ValueError('Desired services must be an explicit mapping')
    names = service_names(list(desired))
    verify_services(snapshot, names)
    paths = [item['path'] for item in before['files']]
    verify_files(before, paths)
    verify_files(after, paths)
    if snapshot['machine_id'] != before['machine_id'] or before['machine_id'] != after['machine_id']:
        raise ValueError('Service and file preimages belong to different targets')
    old_files = {item['path']: item for item in before['files']}
    new_files = {item['path']: item for item in after['files']}
    original = {}
    for name in names:
        wanted = desired[name]
        if (not isinstance(wanted, dict) or set(wanted) != {'present', 'active', 'enabled'} or
                any(type(value) is not bool for value in wanted.values()) or
                (not wanted['present'] and (wanted['active'] or wanted['enabled']))):
            raise ValueError('Desired service requires consistent present/active/enabled booleans')
        state = snapshot['services'][name]
        original[name] = contract(state)
        fragment = state['FragmentPath'] or '/etc/systemd/system/' + name
        # Do not guess decoding of escaped systemd paths; exact file coverage
        # must be established before a service's configuration can be restored.
        if '\\' in state['DropInPaths'] or '"' in state['DropInPaths']:
            raise ValueError('Escaped drop-in paths require explicit path decoding support')
        required = [fragment, *state['DropInPaths'].split()]
        paths_checked(required)
        for path in required:
            if path not in old_files:
                raise ValueError('Service configuration is missing from file backup: ' + path)
            if original[name]['present'] and old_files[path]['kind'] != 'file':
                raise ValueError('Loaded service configuration has no regular-file preimage')
        if (new_files[fragment]['kind'] == 'file') != wanted['present']:
            raise ValueError('Desired service presence differs from the file transition')
        if not original[name]['present'] and old_files[fragment]['kind'] != 'absent':
            raise ValueError('Absent service has an existing local unit file; reload/review first')
    dependencies = snapshot.get('dependencies')
    if dependency_graph is not None:
        verify_graph(dependency_graph, names)
        if any(dependency_graph[key] != snapshot[key] for key in ('machine_id', 'boot_id')):
            raise ValueError('Dependency graph belongs to a different target or boot')
        if dependencies is None or any(dependency_graph['nodes'][name] != dependencies[name] for name in names):
            raise ValueError('Dependency graph differs from selected-unit capture')
    related = sorted({unit for fields in (dependencies or {}).values()
                      for units in fields.values() for unit in units} - set(names))
    return {'schema': 1, 'kind': 'service-ordering-review', 'dispatchable': False,
            'machine_id': snapshot['machine_id'], 'original': original, 'desired': desired,
            'dependency_review': {
                'captured': dependencies is not None,
                'scope': 'selected units only; not a transitive dependency or executable side-effect graph',
                'edges': copy.deepcopy(dependencies),
                'transitive_graph': copy.deepcopy(dependency_graph),
                'related_units_outside_selection': related,
                'execution_approved': False},
            'apply': steps(names, desired, 'apply'), 'restore': steps(names, original, 'restore'),
            'pending_execution_requirements': ['durable service phase reconciliation',
                'enablement symlink and dependency effect qualification', 'service data consistency strategy']}
