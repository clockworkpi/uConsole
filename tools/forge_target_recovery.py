"""Read-only recovery inspection bound to an owner-approved target transaction."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import uuid


def binding(approved):
    if approved.kind == 'recovery-stage':
        from forge_recovery_stage_review import load
        reviewed = load(approved.journal, approved.authorization_sha256)
        image = reviewed['request']['image_plan']
        return image['host'], image['machine_id']
    if approved.kind == 'service':
        from forge_target_service_dispatch import locked, transaction, digest
        from forge_target_journal import read_record
        with locked(approved.journal) as fd:
            authorization = read_record(fd, 'authorization.json')
            if digest(authorization) != approved.authorization_sha256:
                raise PermissionError('Service authorization differs from owner approval')
            record = transaction(fd, authorization['transaction_sha256'])
            return authorization['host'], record['backup']['identity']['machine_id']
    from forge_target_journal import locked
    with locked(approved.journal) as (_, plan):
        digest = hashlib.sha256((json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        if digest != approved.plan_sha256:
            raise PermissionError('Target plan differs from owner approval')
        return plan['host'], plan['before']['machine_id']


BOOTSTRAP = '''import json, pathlib, sys
r=json.load(sys.stdin)
if pathlib.Path('/etc/machine-id').read_text().strip() != r['machine_id']:
    raise ValueError('Target identity differs from approved backup')
scope={'__name__':'forge_inventory_worker'}
exec(compile(r['source'], '<owner-recovery-inventory>', 'exec'), scope)
inventory=scope['capture']()
print(json.dumps({'nonce':r['nonce'], 'machine_id':r['machine_id'],
 'recovery_qualified':False, 'watchdog_observations':inventory['watchdog_observations'],
 'file_status':{p:v['status'] for p,v in inventory['files'].items()},
 'error_paths':[e['path'] for e in inventory['errors']]}))
'''


def inspect(approved):
    host, machine = binding(approved)
    if (not isinstance(host, str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host)
            or not isinstance(machine, str) or not re.fullmatch('[0-9a-f]{32}', machine)):
        raise ValueError('Invalid approved target identity')
    nonce = uuid.uuid4().hex
    source = Path(__file__).with_name('target_recovery_inventory.py').read_text()
    request = dict(nonce=nonce, machine_id=machine, source=source)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             host, 'sudo -n python3 -c ' + shlex.quote(BOOTSTRAP)],
                            input=json.dumps(request), text=True, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Recovery inspection failed; no recovery readiness established')
    report = json.loads(result.stdout)
    if (not isinstance(report, dict) or report.get('nonce') != nonce
            or report.get('machine_id') != machine or report.get('recovery_qualified') is not False
            or set(report) != {'nonce', 'machine_id', 'recovery_qualified',
                              'watchdog_observations', 'file_status', 'error_paths'}):
        raise ValueError('Recovery inspection acknowledgement differs from request')
    from target_recovery_inventory import PATHS
    observations = report['watchdog_observations']
    if (not isinstance(observations, dict) or set(observations) != {
            'runtime_state', 'runtime_timeout', 'kernel_open_timeout', 'bootstatus',
            'firmware_handoff_qualified', 'failed_boot_fallback_qualified'}
            or any(observations[k] is not False for k in (
                'firmware_handoff_qualified', 'failed_boot_fallback_qualified'))
            or any(v is not None and (not isinstance(v, str) or len(v) > 128)
                   for k, v in observations.items() if not k.endswith('_qualified'))
            or not isinstance(report['file_status'], dict)
            or set(report['file_status']) != set(PATHS)
            or any(v not in ('read', 'absent', 'error') for v in report['file_status'].values())
            or not isinstance(report['error_paths'], list)
            or set(report['error_paths']) != {p for p, v in report['file_status'].items() if v == 'error'}):
        raise ValueError('Invalid recovery inspection observations')
    return dict(report, host=host, coverage='Read-only prerequisites; not a backup or boot-recovery qualification')
