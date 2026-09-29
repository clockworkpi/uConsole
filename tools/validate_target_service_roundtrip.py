"""Qualify an inert, uniquely named systemd service over live-target SSH.

Never touches an existing unit. Stops at uncertainty, retaining exact envelopes
and both ledgers for reconciliation; it does not guess that rollback succeeded.
"""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from forge_target_effects import fingerprint
from forge_target_journal import private_directory, write_record
from forge_target_phases import canonical
from forge_target_service_ssh import BOOTSTRAP, MODULES, dispatch


PROBE = BOOTSTRAP.split('from forge_target_service_runtime import execute')[0] + '''
from forge_target_effects import TargetEffects
effects = TargetEffects(**request['scope'])
phase = {'repeatable': True, 'action': {'operation': 'verify', 'observe': request['scope']}}
identity = effects.identity()
state = effects.observe(phase)
if effects.identity() != identity:
    raise RuntimeError('Boot identity changed during observation')
print(json.dumps({'nonce': request['nonce'], 'identity': identity, 'state': state}))
'''
PROVISION = '''import json, tempfile
print(json.dumps({'ledger_parent': tempfile.mkdtemp(prefix='uconsole-service-proof-', dir='/var/tmp')}))
'''


def rpc(host, source, request):
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                             'sudo -n python3 -c ' + shlex.quote(source)],
                            input=json.dumps(request), text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Target operation failed: ' + result.stderr[-2000:])
    return json.loads(result.stdout)


def envelope(identity, scope, before, after, operation, **arguments):
    return {'schema': 1, 'scope': scope, 'plan': dict(identity, schema=1, phases=[
        {'id': operation, 'before': before, 'after': after,
         'repeatable': operation not in ('start', 'stop'),
         'action': dict(operation=operation, observe=scope, **arguments)},
        {'id': 'verify', 'before': after, 'after': after, 'repeatable': True,
         'action': {'operation': 'verify', 'observe': scope}}])}


def run(host, output, *, paired=False, update_cycle=False, mcp=False, gui=False):
    if update_cycle and not paired:
        raise ValueError('Existing-service qualification requires the paired fixture workflow')
    if (mcp or gui) and (not paired or update_cycle or (mcp and gui)):
        raise ValueError('Choose one frontend for a paired new-service fixture')
    if not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Use a literal SSH host')
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    fd = private_directory(output)
    owner = None
    try:
        parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        unit = 'uconsole-forge-service-' + uuid.uuid4().hex + '.service'
        path = '/etc/systemd/system/' + unit
        link = '/etc/systemd/system/multi-user.target.wants/' + unit
        scope = {'services': [unit], 'files': [path], 'links': [link]}
        root = Path(__file__).resolve().parent
        modules = [(name, (root / (name + '.py')).read_text()) for name in MODULES]

        def observe(view):
            nonce = uuid.uuid4().hex
            result = rpc(host, PROBE, {'modules': modules, 'scope': view, 'nonce': nonce})
            if result.get('nonce') != nonce or set(result) != {'nonce', 'identity', 'state'}:
                raise ValueError('Invalid observation acknowledgement')
            return result

        original = observe(scope)
        service = original['state']['services'][unit]
        if (service['LoadState'] != 'not-found' or service['ActiveState'] != 'inactive' or
                original['state']['files'][path] != {'path': path, 'kind': 'absent'} or
                original['state']['links'][link] != {'path': link, 'kind': 'absent'}):
            raise ValueError('Fixture must start with an absent unit and enablement link')
        write_record(fd, 'backup.json', dict(original, host=host, scope=scope))
        write_record(fd, 'provision-intent.json', {'host': host, 'source': PROVISION})
        provisioned = rpc(host, PROVISION, {})
        ledger = provisioned.get('ledger_parent', '')
        if not re.fullmatch('/var/tmp/uconsole-service-proof-[A-Za-z0-9_-]+', ledger):
            raise ValueError('Invalid private ledger acknowledgement')
        write_record(fd, 'provisioned.json', provisioned)
        steps = []

        def step(operation, view, change, **arguments):
            current = observe(view)
            if current['identity'] != original['identity']:
                raise ValueError('Target identity changed; retain evidence and reconcile')
            after = copy.deepcopy(current['state'])
            change(after)
            plan = envelope(original['identity'], view, current['state'], after, operation, **arguments)
            digest = hashlib.sha256(canonical(plan).encode()).hexdigest()
            result = dispatch(host, plan, digest, output / f'{len(steps):02d}-{operation}', ledger_parent=ledger)
            steps.append(result)

        def transition(kind, expected, desired):
            key = 'files' if kind == 'file' else 'links'
            view = {'services': [], 'files': [], 'links': []}
            view[key] = [expected['path']]
            step(kind, view, lambda state: state[key].update({expected['path']: fingerprint(desired)}),
                 expected=expected, desired=desired, stage_token=uuid.uuid4().hex)

        service_view = {'services': [unit], 'files': [], 'links': []}

        def lifecycle(operation, **properties):
            step(operation, service_view, lambda state: state['services'][unit].update(properties),
                 **({'service': unit} if operation in ('start', 'stop') else {}))

        body = ('[Unit]\nDescription=uConsole forge inert service qualification\n'
                '[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/true\n'
                '[Install]\nWantedBy=multi-user.target\n').encode()
        stamp = time.time_ns()
        file = {'path': path, 'kind': 'file', 'data': base64.b64encode(body).decode(),
                'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'mode': 0o644,
                'uid': 0, 'gid': 0, 'mtime_ns': stamp, 'atime_ns': stamp, 'xattrs': {}}
        enabled = {'path': link, 'kind': 'symlink', 'target': path, 'uid': 0, 'gid': 0, 'mtime_ns': stamp}
        if paired:
            from forge_target_service_compile import new_service
            from forge_target_service_transaction import prepare
            from forge_target_service_dispatch import authorize, dispatch as dispatch_pair
            backup = {'identity': original['identity'], 'state': original['state'],
                      'files': [{'path': path, 'kind': 'absent'}], 'links': [{'path': link, 'kind': 'absent'}]}
            pair = new_service(backup, unit, file, enabled)
            directory = output / 'transaction'
            prepared = prepare(directory, pair['backup'], pair['apply'], pair['restore'])
            approved = authorize(directory, prepared['transaction_sha256'], host=host, ledger_parent=ledger)
            write_record(fd, 'paired-review.json', dict(prepared, **approved))
            if mcp:
                from target_mcp_validation import MCPTransitions
                owner = MCPTransitions(output, {'kind': 'service', 'journal': str(directory),
                                               'authorization_sha256': approved['authorization_sha256']}).start()
            elif gui:
                from target_gui_validation import GUITransitions
                owner = GUITransitions(output, {'kind': 'service', 'journal': str(directory),
                                               'authorization_sha256': approved['authorization_sha256']}).start()
            for direction in ('apply', 'restore'):
                if owner:
                    transition = owner.transition(direction)
                    steps.append((transition['job'] if gui else transition)['result'])
                else:
                    steps.append(dispatch_pair(directory, direction, approved['authorization_sha256']))
                if direction == 'apply' and update_cycle:
                    from forge_target_service_compile import update_service
                    baseline = observe(scope)
                    if (baseline['identity'] != original['identity'] or
                            baseline['state'] != pair['apply']['plan']['phases'][-1]['after']):
                        raise ValueError('Existing fixture differs from its acknowledged installation')
                    unit_backup = {'identity': baseline['identity'], 'state': baseline['state'],
                                   'files': [file], 'links': [enabled]}
                    changed_body = body.replace(b'inert service qualification', b'updated inert service qualification')
                    changed_file = dict(file, data=base64.b64encode(changed_body).decode(), size=len(changed_body),
                                        sha256=hashlib.sha256(changed_body).hexdigest(), mtime_ns=time.time_ns(), mode=0o640)
                    update = update_service(unit_backup, unit, changed_file, active=True)
                    update_directory = output / 'update-transaction'
                    update_prepared = prepare(update_directory, update['backup'], update['apply'], update['restore'])
                    update_approved = authorize(update_directory, update_prepared['transaction_sha256'],
                                                host=host, ledger_parent=ledger)
                    write_record(fd, 'update-review.json', dict(update_prepared, **update_approved))
                    for update_direction in ('apply', 'restore'):
                        steps.append(dispatch_pair(update_directory, update_direction,
                                                   update_approved['authorization_sha256']))
                    checked = observe(scope)
                    if checked['identity'] != baseline['identity'] or checked['state'] != baseline['state']:
                        raise ValueError('Existing fixture restoration differs from baseline')
                    write_record(fd, 'update-restored.json', checked)
        else:
            transition('file', {'path': path, 'kind': 'absent'}, file)
            lifecycle('daemon-reload', LoadState='loaded', FragmentPath=path, UnitFileState='disabled',
                      NeedDaemonReload='no', DropInPaths='')
            transition('link', {'path': link, 'kind': 'absent'}, enabled)
            lifecycle('daemon-reload', UnitFileState='enabled', NeedDaemonReload='no')
            lifecycle('start', ActiveState='active')
            lifecycle('stop', ActiveState='inactive')
            transition('link', enabled, {'path': link, 'kind': 'absent'})
            transition('file', file, {'path': path, 'kind': 'absent'})
            lifecycle('daemon-reload', **service)
        restored = observe(scope)
        if restored['identity'] != original['identity'] or restored['state'] != original['state']:
            raise ValueError('Final target state differs from backup')
        if owner is not None:
            finished_owner, owner = owner, None
            finished_owner.close()  # Owner/history shutdown must succeed before acceptance is published.
        result = {'status': 'restored', 'host': host, 'unit': unit, 'steps': steps, 'restored': restored,
                  'dispatch_mode': 'gui-paired' if gui else ('mcp-paired' if mcp else ('paired' if paired else 'individual-phases')),
                  'update_cycle': update_cycle,
                  'scope': 'inert service lifecycle and exact unit/link absence, not application-data rollback'}
        write_record(fd, 'acceptance.json', result)
        return result
    except BaseException as exc:
        write_record(fd, 'incomplete.json', {'error': str(exc), 'restoration': 'unverified'})
        raise
    finally:
        try:
            if owner is not None:
                owner.close()
        finally:
            os.close(fd)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--paired', action='store_true', help='Compile, review and dispatch a complete apply/restore pair')
    cli.add_argument('--update-cycle', action='store_true', help='Also update and restore the installed inert fixture')
    cli.add_argument('--mcp', action='store_true', help='Dispatch the paired fixture through a real stdio MCP owner')
    cli.add_argument('--gui', action='store_true', help='Dispatch using actual Workbench confirmation and buttons')
    args = cli.parse_args()
    result = run(args.host, args.output, paired=args.paired, update_cycle=args.update_cycle, mcp=args.mcp, gui=args.gui)
    print(json.dumps({'status': result['status'], 'unit': result['unit']}))
