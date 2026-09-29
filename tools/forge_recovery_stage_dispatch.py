"""Explicit single-phase staging and fencing; never retry, reboot or deploy root."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from forge_boot_observation import capture, normal_boot
from forge_recovery_stage_prepare import published
from forge_recovery_stage_review import load
from forge_recovery_stage_worker import digest, roots, validate
from forge_target_journal import private_directory, read_record, write_record


PHASES = ('firmware-start', 'firmware-fixup', 'command', 'selector')
MODULES = ('forge_target_backup', 'forge_target_files', 'forge_target_journal',
           'forge_target_ssh', 'forge_boot_privacy', 'forge_recovery_image',
           'forge_recovery_journal', 'forge_recovery_ledger', 'forge_recovery_ssh',
           'forge_boot_observation', 'forge_recovery_stage_worker')
BOOTSTRAP = '''import json, sys, types
request = json.load(sys.stdin)
for name, source in request['modules']:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, '<forge-owner:' + name + '>', 'exec'), module.__dict__)
from forge_recovery_stage_worker import perform
print(json.dumps(perform(request['value'], request['operation'], request['observed_boot'])))
'''


def transport(value, operation, observed_boot=None):
    validate(value)
    root = Path(__file__).resolve().parent
    payload = dict(value=value, operation=operation, observed_boot=observed_boot,
                   modules=[(name, (root/(name+'.py')).read_text()) for name in MODULES])
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             value['plan']['host'], 'sudo -n python3 -c '+shlex.quote(BOOTSTRAP)],
                            input=json.dumps(payload), text=True, capture_output=True,
                            timeout=180, check=True)
    return json.loads(result.stdout)


def key(phase, direction):
    if phase not in PHASES or direction not in ('apply', 'restore'):
        raise ValueError('Choose one fixed staging phase and apply or restore')
    return 'stage-attempt-'+phase+'-'+direction


@contextmanager
def locked(directory, pin):
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd, load(directory, pin)
    finally:
        os.close(fd)


def checked_response(value, response, boot, *, reconcile=False):
    if (not isinstance(response, dict) or response.get('request_sha256') != digest(value) or
            response.get('attempt') != value['attempt'] or response.get('boot_id') != boot['boot_id'] or
            response.get('machine_id') != boot['machine_id'] or
            any(response.get(name) is not False for name in ('root_written', 'reboot_performed', 'recovery_qualified')) or
            not isinstance(response.get('files'), list) or
            [item.get('path') for item in response['files']] != [item['path'] for item in value['plan']['before']['files']] or
            any(item.get('state') not in ('before', 'after', 'unchanged', 'conflict') for item in response['files']) or
            not isinstance(response.get('scratch'), list)):
        raise ValueError('Staging acknowledgement differs from its bound request')
    if reconcile:
        outcome = response.get('outcome', {})
        if (response.get('status') != 'inspected' or outcome.get('status') not in
                ('completed', 'incomplete', 'fenced-not-started', 'previous-boot-fenced') or
                response.get('requires_new_boot') is not (outcome.get('status') == 'incomplete') or
                (outcome['status'] == 'previous-boot-fenced') != (boot['boot_id'] != value['boot']['boot_id'])):
            raise ValueError('Unexpected staging fence response')
        if outcome['status'] != 'previous-boot-fenced' and outcome.get('request') != dict(
                plan_sha256=digest(value), direction=value['direction'], nonce=value['attempt']):
            raise ValueError('Staging fence belongs to another attempt')
    else:
        desired = 'after' if value['direction'] == 'apply' else 'before'
        if (response.get('status') != 'acknowledged' or
                any(item['state'] not in ('unchanged', desired) for item in response['files']) or
                response.get('file', {}).get('path') != value['plan']['before']['files'][-1]['path'] or
                response.get('file', {}).get('status') not in ('applied', 'already-applied')):
            raise ValueError('Staging write was not fully acknowledged')
    return response


def bound_attempt(fd, reviewed, pin, phase, direction):
    value = validate(read_record(fd, key(phase, direction)+'.json'))
    if (value['preparation_sha256'] != pin or value['nonce'] != reviewed['request']['nonce'] or
            value['phase'] != phase or value['direction'] != direction or
            value['original_boot'] != reviewed['original_boot'] or
            value['plan'] != reviewed['plans'][PHASES.index(phase)] or
            value['image_plan'] != reviewed['request']['image_plan']):
        raise ValueError('Retained staging attempt differs from sealed preparation')
    return value


def pending(fd, reviewed, pin):
    """Any unknown outcome stops every new phase; reconciliation is explicit."""
    names = os.listdir(fd)
    for phase in PHASES:
        for direction in ('apply', 'restore'):
            stem = key(phase, direction)
            if stem+'.json' not in names:
                continue
            value = bound_attempt(fd, reviewed, pin, phase, direction)
            if stem+'-ack.json' in names:
                checked_response(value, read_record(fd, stem+'-ack.json'), value['boot'])
                continue
            reconciliations = sorted(name for name in names if name.startswith(stem+'-reconciled-'))
            if reconciliations:
                # Ordered host records, not random query IDs, establish the latest observation.
                if reconciliations != [stem+f'-reconciled-{index:06d}.json' for index in range(len(reconciliations))]:
                    raise ValueError('Staging reconciliation sequence is incomplete')
                record = read_record(fd, reconciliations[-1])
                response = checked_response(value, record['response'], record['boot'], reconcile=True)
                if not response['requires_new_boot'] and all(item['state'] != 'conflict' for item in response['files']):
                    continue
            raise RuntimeError('Staging outcome is uncertain; explicitly reconcile '+phase+' '+direction)


def dispatch(directory, pin, phase, direction, *, authorize):
    stem = key(phase, direction)
    with locked(directory, pin) as (fd, reviewed):
        if stem+'.json' in os.listdir(fd):
            raise RuntimeError('Staging attempt already exists; never resubmit it')
        pending(fd, reviewed, pin)
        frozen = reviewed['request']
        boot = normal_boot(capture(frozen['image_plan']['host']), frozen['image_plan']['machine_id'])
        plan = reviewed['plans'][PHASES.index(phase)]
        value = validate(dict(schema=1, preparation_sha256=pin, nonce=frozen['nonce'], phase=phase,
            direction=direction, attempt=uuid.uuid4().hex, original_boot=reviewed['original_boot'],
            boot=boot, plan=plan, plan_sha256=digest(plan), image_plan=frozen['image_plan']))
        if direction == 'apply':
            if published(frozen)['boot_id'] != boot['boot_id']:
                raise ValueError('Recovery publication inspection observed another boot')
        approval = dict(stage=pin, phase=phase, direction=direction, boot_id=boot['boot_id'], plan_sha256=digest(plan))
        if authorize(dict(approval)) != approval:
            raise PermissionError('Explicit approval of this phase, direction and normal boot is required')
        write_record(fd, stem+'.json', value)
        try:
            response = checked_response(value, transport(value, 'execute'), boot)
            write_record(fd, stem+'-ack.json', response)
            return response
        except BaseException as exc:
            # The durable request already blocks all new writes even if recording this fails.
            write_record(fd, stem+'-uncertain.json', dict(error=type(exc).__name__+': '+str(exc)))
            raise RuntimeError('Staging completion uncertain; retain evidence and reconcile, do not retry') from exc


def reconcile(directory, pin, phase, direction):
    stem = key(phase, direction)
    with locked(directory, pin) as (fd, reviewed):
        value = bound_attempt(fd, reviewed, pin, phase, direction)
        boot = normal_boot(capture(value['plan']['host']), value['image_plan']['machine_id'])
        if roots(boot) != roots(value['original_boot']):
            raise ValueError('Reconciliation target no longer uses the native root')
        query = uuid.uuid4().hex
        write_record(fd, stem+'-query-'+query+'.json', dict(boot=boot, request_sha256=digest(value)))
        response = checked_response(value, transport(value, 'reconcile', boot), boot, reconcile=True)
        names = sorted(name for name in os.listdir(fd) if name.startswith(stem+'-reconciled-'))
        if names != [stem+f'-reconciled-{index:06d}.json' for index in range(len(names))]:
            raise ValueError('Staging reconciliation sequence is incomplete')
        write_record(fd, stem+f'-reconciled-{len(names):06d}.json', dict(boot=boot, response=response))
        return response


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('operation', choices=('apply', 'restore', 'reconcile-apply', 'reconcile-restore'))
    cli.add_argument('--directory', type=Path, required=True)
    cli.add_argument('--acceptance-sha256', required=True)
    cli.add_argument('--phase', choices=PHASES, required=True)
    cli.add_argument('--approve-boot-id')
    args = cli.parse_args()
    if args.operation.startswith('reconcile-'):
        if args.approve_boot_id: cli.error('Reconciliation does not approve a new write')
        result = reconcile(args.directory, args.acceptance_sha256, args.phase, args.operation[len('reconcile-'):])
    else:
        if not args.approve_boot_id: cli.error('Writing a boot file requires --approve-boot-id')
        result = dispatch(args.directory, args.acceptance_sha256, args.phase, args.operation,
                          authorize=lambda approval: approval if approval['boot_id'] == args.approve_boot_id else None)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
